import logging

import numpy as np

from naretta.audio import LinearResampler, PlaybackBuffer, apply_gain, voice_level
from naretta.history import ConversationHistory, estimate_heard_text
from naretta.realtime import describe_http_status
from naretta.redact import RedactingFilter, redact, register_secret
from naretta.wake import contains_wake_word


def test_resampler_rate_and_continuity():
    rate_in, rate_out = 44100, 24000
    t = np.arange(rate_in) / rate_in
    tone = (np.sin(2 * np.pi * 440 * t) * 10000).astype(np.float32)
    resampler = LinearResampler(rate_in, rate_out)
    pieces = [resampler.process(tone[i : i + 1764]) for i in range(0, tone.size, 1764)]
    out = np.concatenate(pieces)
    assert abs(out.size - rate_out) <= 2
    expected = np.sin(2 * np.pi * 440 * np.arange(out.size) / rate_out) * 10000
    assert np.max(np.abs(out - expected)) < 150  # no glitches at chunk boundaries


def test_resampler_passthrough():
    data = np.arange(10, dtype=np.float32)
    assert LinearResampler(24000, 24000).process(data) is data


def test_playback_buffer_tracks_played_audio_and_interrupts():
    events = []
    buf = PlaybackBuffer(24000, events.append)
    buf.enqueue("a1", np.ones(2400, dtype=np.int16).tobytes())  # 100 ms
    buf.enqueue("a2", np.ones(2400, dtype=np.int16).tobytes())
    assert events == [True]
    buf.read(3600)  # 150 ms
    assert buf.played_ms("a1") == 100
    assert buf.played_ms("a2") == 50
    buf.interrupt()
    assert events == [True, False]
    assert not buf.read(480).any()  # queued audio was discarded


def test_playback_buffer_idle_hysteresis():
    events = []
    buf = PlaybackBuffer(24000, events.append)
    buf.enqueue("a1", np.ones(240, dtype=np.int16).tobytes())
    buf.read(960)  # short gap: still "speaking"
    assert events == [True]
    for _ in range(10):
        buf.read(960)
    assert events == [True, False]


def test_playback_buffer_resamples_to_device_rate():
    buf = PlaybackBuffer(48000, lambda _: None)
    buf.enqueue("a1", np.ones(2400, dtype=np.int16).tobytes())  # 100 ms at 24 kHz
    buf.read(48000)
    assert abs(buf.played_ms("a1") - 100) < 1


def test_estimate_heard_text():
    text = "Hi, I'm Naretta. What did you do this weekend?"
    assert estimate_heard_text(text, 1000, 1000) == text
    partial = estimate_heard_text(text, 400, 1000)
    assert partial.endswith("…") and text.startswith(partial[:-1])
    assert estimate_heard_text(text, 0, 1000) == ""


def test_history_replay_items():
    history = ConversationHistory()
    history.upsert("u1", "user", text="Hello", final=True)
    history.upsert("a1", "assistant", text="Hi, I'm Naretta.", final=True)
    history.upsert("u2", "user")  # pending, no text
    assert history.replay_items() == [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Hello"}]},
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Hi, I'm Naretta."}],
        },
    ]


def test_redaction():
    key = "sk-proj-abcdefghijklmnop1234567890"
    register_secret("custom-secret-value")
    assert key not in redact(f"failed with {key}")
    assert "custom-secret-value" not in redact("x custom-secret-value y")
    assert "token123" not in redact("Authorization: Bearer token123")

    record = logging.LogRecord("t", logging.INFO, __file__, 1, "key=%s", (key,), None)
    RedactingFilter().filter(record)
    assert key not in record.getMessage()


def test_http_status_messages():
    assert "API key" in describe_http_status(401, "m")[0]
    assert describe_http_status(401, "m")[1] is False
    assert describe_http_status(503, "m")[1] is True


def test_voice_level_scale():
    assert voice_level(np.zeros(480, dtype=np.int16)) == 0.0
    loud = (np.sin(np.arange(480) / 5) * 16000).astype(np.int16)
    quiet = (loud / 100).astype(np.int16)
    assert voice_level(loud) > 0.9
    assert 0.0 < voice_level(quiet) < 0.5


def test_wake_word_matching():
    for heard in ("Hey Naretta, how are you?", "naretta", "Hi Nareta!", "OK Narretta what's up", "Loretta, are you there?", "Hey Naréta"):
        assert contains_wake_word(heard), heard
    for heard in ("What time is it?", "I need a narrator", "nearest station", "Rita is here", ""):
        assert not contains_wake_word(heard), heard


def test_voice_processor_cancels_echo():
    import pytest

    pytest.importorskip("livekit.rtc")
    from naretta.audio import VoiceProcessor

    rng = np.random.default_rng(1)
    sr, secs = 24000, 5
    t = np.arange(sr * secs) / sr
    far = (rng.standard_normal(t.size) * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)) ** 2 * 6000).astype(np.int16)
    delay = int(0.06 * sr)
    mic = (np.concatenate([np.zeros(delay), far[:-delay]]) * 0.4).astype(np.int16)
    processor = VoiceProcessor()
    processor.set_delay_ms(60)
    out = []
    for i in range(0, t.size, 960):  # 40 ms blocks, like the audio callbacks
        processor.process_far(far[i : i + 960])
        out.append(processor.process_near(mic[i : i + 960]))
    cleaned = np.concatenate(out).astype(np.float32)
    tail = slice(3 * sr, 5 * sr)  # after the canceller has adapted

    def rms_db(x):
        return 20 * np.log10(np.sqrt(np.mean(np.square(x))) + 1e-9)

    assert rms_db(mic[tail].astype(np.float32)) - rms_db(cleaned[tail]) > 20


def test_voice_volume_boost_is_limited_not_clipped():
    quiet = (np.sin(np.arange(2400) / 6) * 8000).astype(np.int16)  # peaks near -12 dBFS
    boosted = apply_gain(quiet, 3.0)
    assert np.abs(boosted).max() > 20000  # clearly louder
    loud = apply_gain((np.sin(np.arange(2400) / 6) * 30000).astype(np.int16), 4.0)
    assert np.abs(loud.astype(np.int32)).max() <= 32767  # limited, no wrap-around
    assert apply_gain(quiet, 1.0) is quiet
    buf = PlaybackBuffer(24000, lambda _: None, gain=2.0)
    buf.enqueue("a1", quiet.tobytes())
    assert np.abs(buf.read(2400)).max() > np.abs(quiet).max() * 1.8
