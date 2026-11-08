"""Microphone capture and speaker playback via PortAudio (sounddevice).

All audio exchanged with the rest of the app is 16-bit mono PCM at 24 kHz. Devices that
won't open at 24 kHz are opened at their native rate and resampled here.
"""

from __future__ import annotations

import collections
import logging
import threading
from typing import Callable

import numpy as np

from .config import SAMPLE_RATE

log = logging.getLogger(__name__)

BLOCK_MS = 40
# The speaker counts as idle only after this much silence, so brief network gaps
# between audio chunks don't flicker the status between Speaking and Listening.
IDLE_HOLD_MS = 350


class AudioError(Exception):
    pass


def _sd():
    try:
        import sounddevice
    except OSError as exc:  # PortAudio library missing
        raise AudioError(f"The audio system could not be loaded: {exc}") from None
    return sounddevice


# --------------------------------------------------------------------------- devices


def list_devices(kind: str) -> list[str]:
    """Names of input ("input") or output ("output") devices on the default host API."""
    sd = _sd()
    try:
        devices = sd.query_devices()
        hostapi = sd.default.hostapi
    except Exception as exc:
        raise AudioError(f"Couldn't list audio devices: {exc}") from None
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    names: list[str] = []
    for dev in devices:
        if dev["name"].startswith("Microsoft Sound Mapper"):
            continue  # MME's alias for the default device; "System default" covers it
        if dev["hostapi"] == hostapi and dev[key] > 0 and dev["name"] not in names:
            names.append(dev["name"])
    return names


def _resolve_device(name: str | None, kind: str) -> int | None:
    if not name:
        return None
    sd = _sd()
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    hostapi = sd.default.hostapi
    for index, dev in enumerate(sd.query_devices()):
        if dev["name"] == name and dev["hostapi"] == hostapi and dev[key] > 0:
            return index
    label = "microphone" if kind == "input" else "speaker"
    raise AudioError(
        f"The selected {label} “{name}” isn't available. Plug it in, or pick "
        "another device in Settings."
    )


def _open_stream(kind: str, device_name: str | None, make_callback, finished_callback):
    """Opens a raw int16 stream, trying 24 kHz mono first, then the device's native format.

    make_callback(samplerate, channels) must return the PortAudio callback.
    Returns (stream, samplerate, channels).
    """
    sd = _sd()
    label = "microphone" if kind == "input" else "speaker"
    device = _resolve_device(device_name, kind)
    try:
        info = sd.query_devices(device, kind)
    except Exception:
        raise AudioError(
            f"No {label} was found. Connect one and check Windows Sound settings."
        ) from None

    native_rate = int(info["default_samplerate"]) or SAMPLE_RATE
    max_channels = int(info["max_input_channels" if kind == "input" else "max_output_channels"])
    if kind == "output" and max_channels >= 2:
        # Play through both speakers explicitly rather than relying on Windows to upmix mono.
        candidates = [(SAMPLE_RATE, 2), (SAMPLE_RATE, 1), (native_rate, 2), (native_rate, 1)]
    else:
        candidates = [(SAMPLE_RATE, 1)]
        if native_rate != SAMPLE_RATE:
            candidates.append((native_rate, 1))
        if max_channels >= 2:
            candidates.append((native_rate, 2))
    candidates = list(dict.fromkeys(candidates))

    stream_cls = sd.RawInputStream if kind == "input" else sd.RawOutputStream
    last_error: Exception | None = None
    for rate, channels in candidates:
        try:
            stream = stream_cls(
                samplerate=rate,
                channels=channels,
                dtype="int16",
                blocksize=int(rate * BLOCK_MS / 1000),
                device=device,
                latency="low",
                callback=make_callback(rate, channels),
                finished_callback=finished_callback,
            )
            stream.start()
            log.info("Opened %s %r at %d Hz, %d ch", label, info["name"], rate, channels)
            return stream, rate, channels
        except Exception as exc:
            last_error = exc
            log.info("Couldn't open %s at %d Hz/%d ch: %s", label, rate, channels, exc)

    hint = ""
    if kind == "input":
        hint = (
            " If Windows blocked microphone access, turn on Settings › Privacy & security "
            "› Microphone › “Let desktop apps access your microphone”."
        )
    raise AudioError(f"Couldn't open the {label} “{info['name']}” ({last_error}).{hint}")


# --------------------------------------------------------------------------- resampling


class LinearResampler:
    """Streaming linear-interpolation resampler for mono float32 audio."""

    def __init__(self, src_rate: int, dst_rate: int):
        self.src_rate = src_rate
        self.dst_rate = dst_rate
        self._step = src_rate / dst_rate
        self.reset()

    def reset(self) -> None:
        self._pos = 0.0  # read position, in samples of the current working buffer
        self._prev: float | None = None

    def process(self, samples: np.ndarray) -> np.ndarray:
        if self.src_rate == self.dst_rate or samples.size == 0:
            return samples
        buf = samples if self._prev is None else np.concatenate(([self._prev], samples))
        last = buf.size - 1
        if last < self._pos:
            count = 0
        else:
            count = int(np.floor((last - self._pos) / self._step)) + 1
        positions = self._pos + self._step * np.arange(count)
        out = np.interp(positions, np.arange(buf.size), buf).astype(np.float32)
        # The next buffer starts with this buffer's last sample at index 0.
        self._pos = self._pos + self._step * count - last
        self._prev = float(buf[-1])
        return out


def _to_int16(samples: np.ndarray) -> np.ndarray:
    return np.clip(np.round(samples), -32768, 32767).astype(np.int16)


def apply_gain(samples: np.ndarray, gain: float) -> np.ndarray:
    """Amplifies int16 audio, softly limiting peaks above about -2 dBFS instead of clipping."""
    if gain == 1.0 or samples.size == 0:
        return samples
    x = samples.astype(np.float32) * (gain / 32768.0)
    knee = 0.8
    over = np.abs(x) > knee
    if over.any():
        excess = np.abs(x[over]) - knee
        x[over] = np.sign(x[over]) * (knee + (1 - knee) * np.tanh(excess / (1 - knee)))
    return _to_int16(x * 32767.0)


def voice_level(samples: np.ndarray) -> float:
    """Loudness of int16 samples on a 0..1 scale (-50 dBFS -> 0, -10 dBFS -> 1), for animation."""
    if samples.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(samples.astype(np.float32))))) / 32768.0
    if rms <= 1e-6:
        return 0.0
    return min(1.0, max(0.0, (20.0 * np.log10(rms) + 50.0) / 40.0))


# --------------------------------------------------------------------------- voice processing


class VoiceProcessor:
    """WebRTC echo cancellation and noise suppression (via livekit).

    The speaker feeds it what is being played (far end); the microphone runs its audio
    through it (near end), which removes Naretta's own voice picked up from the speakers.
    Both sides are 24 kHz mono and are cut into the 10 ms frames WebRTC requires.
    """

    FRAME = SAMPLE_RATE // 100

    def __init__(self):
        from livekit import rtc

        self._rtc = rtc
        self._apm = rtc.AudioProcessingModule(
            echo_cancellation=True,
            noise_suppression=True,
            high_pass_filter=True,
            # Off on purpose: it boosted mic hiss by ~15 dB until speech clipped, which garbled
            # transcripts and made the noise look like barge-ins.
            auto_gain_control=False,
        )
        self._lock = threading.Lock()
        self._near = np.zeros(0, dtype=np.int16)
        self._far = np.zeros(0, dtype=np.int16)

    @classmethod
    def create(cls) -> "VoiceProcessor | None":
        try:
            return cls()
        except Exception as exc:  # livekit missing or its native library failed to load
            log.warning("Echo cancellation unavailable: %s", exc)
            return None

    def set_delay_ms(self, delay_ms: float) -> None:
        with self._lock:
            self._apm.set_stream_delay_ms(int(max(0, min(500, delay_ms))))

    def _frames(self, pending: np.ndarray, samples: np.ndarray):
        buf = np.concatenate((pending, samples)) if pending.size else samples
        whole = buf.size - buf.size % self.FRAME
        return buf[:whole], buf[whole:].copy()

    def process_far(self, samples: np.ndarray) -> None:
        """Audio being played, 24 kHz int16 (speaker thread)."""
        frames, self._far = self._frames(self._far, samples)
        for i in range(0, frames.size, self.FRAME):
            frame = self._rtc.AudioFrame(frames[i : i + self.FRAME].tobytes(), SAMPLE_RATE, 1, self.FRAME)
            with self._lock:
                self._apm.process_reverse_stream(frame)

    def process_near(self, samples: np.ndarray) -> np.ndarray:
        """Microphone audio, 24 kHz int16 (microphone thread). Returns the cleaned audio."""
        frames, self._near = self._frames(self._near, samples)
        out = []
        for i in range(0, frames.size, self.FRAME):
            frame = self._rtc.AudioFrame(frames[i : i + self.FRAME].tobytes(), SAMPLE_RATE, 1, self.FRAME)
            with self._lock:
                self._apm.process_stream(frame)
            out.append(np.frombuffer(bytes(frame.data), dtype=np.int16))
        return np.concatenate(out) if out else np.zeros(0, dtype=np.int16)


# --------------------------------------------------------------------------- microphone


class Microphone:
    """Captures audio and hands 24 kHz mono PCM16 chunks to on_chunk (called on the audio thread)."""

    def __init__(
        self,
        device_name: str | None,
        on_chunk: Callable[[bytes], None],
        on_failure: Callable[[str], None],
        processor: VoiceProcessor | None = None,
    ):
        self._device_name = device_name
        self._on_chunk = on_chunk
        self._on_failure = on_failure
        self._processor = processor
        self._stream = None
        self._closing = False

    def start(self) -> None:
        def make_callback(rate: int, channels: int):
            resampler = LinearResampler(rate, SAMPLE_RATE)
            direct = rate == SAMPLE_RATE and channels == 1
            processor = self._processor

            def callback(indata, frames, time_info, status):
                if direct and processor is None:
                    self._on_chunk(bytes(indata))
                    return
                if direct:
                    samples = np.frombuffer(indata, dtype=np.int16).copy()
                else:
                    data = np.frombuffer(indata, dtype=np.int16).astype(np.float32)
                    if channels > 1:
                        data = data.reshape(-1, channels).mean(axis=1)
                    samples = _to_int16(resampler.process(data))
                if processor is not None:
                    samples = processor.process_near(samples)
                if samples.size:
                    self._on_chunk(samples.tobytes())

            return callback

        self._stream, _, _ = _open_stream("input", self._device_name, make_callback, self._finished)

    def _finished(self) -> None:
        if not self._closing:
            self._on_failure("The microphone stopped unexpectedly. Was it unplugged?")

    def close(self) -> None:
        self._closing = True
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.abort()
                stream.close()
            except Exception as exc:
                log.debug("Error closing microphone: %s", exc)


# --------------------------------------------------------------------------- playback


class PlaybackBuffer:
    """Queued assistant audio, tagged by conversation item, with per-item played-frame counts.

    Shared between the network thread (enqueue/interrupt) and the audio thread (read).
    """

    def __init__(self, device_rate: int, on_playing_changed: Callable[[bool], None], gain: float = 1.0):
        self.device_rate = device_rate
        self.gain = gain
        self._on_playing_changed = on_playing_changed
        self._lock = threading.Lock()
        self._chunks: collections.deque[list] = collections.deque()  # [item_id, int16 array, offset]
        self._played: dict[str, int] = {}
        self._resampler = LinearResampler(SAMPLE_RATE, device_rate)
        self._playing = False
        self._idle_frames = 0
        self.level = 0.0  # loudness of the most recently played block

    def enqueue(self, item_id: str, pcm24k: bytes) -> None:
        samples = apply_gain(np.frombuffer(pcm24k, dtype=np.int16), self.gain)
        if self.device_rate != SAMPLE_RATE:
            samples = _to_int16(self._resampler.process(samples.astype(np.float32)))
        if samples.size == 0:
            return
        with self._lock:
            self._chunks.append([item_id, samples, 0])
            self._idle_frames = 0
            changed = self._set_playing(True)
        if changed:
            self._on_playing_changed(True)

    def read(self, frames: int) -> np.ndarray:
        out = np.zeros(frames, dtype=np.int16)
        pos = 0
        with self._lock:
            while pos < frames and self._chunks:
                chunk = self._chunks[0]
                item_id, samples, offset = chunk
                n = min(frames - pos, samples.size - offset)
                out[pos : pos + n] = samples[offset : offset + n]
                self._played[item_id] = self._played.get(item_id, 0) + n
                pos += n
                chunk[2] = offset + n
                if chunk[2] >= samples.size:
                    self._chunks.popleft()
            changed = False
            if pos < frames:
                self._idle_frames += frames - pos
                if self._idle_frames >= self.device_rate * IDLE_HOLD_MS / 1000:
                    changed = self._set_playing(False)
            else:
                self._idle_frames = 0
        self.level = voice_level(out)
        if changed:
            self._on_playing_changed(False)
        return out

    def interrupt(self) -> None:
        """Drops all queued audio immediately."""
        with self._lock:
            self._chunks.clear()
            self._resampler.reset()
            self._idle_frames = 0
            self.level = 0.0
            changed = self._set_playing(False)
        if changed:
            self._on_playing_changed(False)

    def played_ms(self, item_id: str) -> float:
        with self._lock:
            return self._played.get(item_id, 0) * 1000.0 / self.device_rate

    def forget(self) -> None:
        with self._lock:
            self._played.clear()

    @property
    def playing(self) -> bool:
        return self._playing

    def _set_playing(self, value: bool) -> bool:
        if self._playing == value:
            return False
        self._playing = value
        return True


class Speaker:
    """Plays queued assistant audio on an output device."""

    def __init__(
        self,
        device_name: str | None,
        on_playing_changed: Callable[[bool], None],
        on_failure: Callable[[str], None],
        processor: VoiceProcessor | None = None,
        gain: float = 1.0,
    ):
        self._device_name = device_name
        self._on_playing_changed = on_playing_changed
        self._gain = gain
        self._on_failure = on_failure
        self._processor = processor
        self._stream = None
        self._closing = False
        self.buffer: PlaybackBuffer | None = None
        self._latency_ms = 0.0

    def start(self) -> None:
        def make_callback(rate: int, channels: int):
            self.buffer = PlaybackBuffer(rate, self._on_playing_changed, self._gain)
            buffer = self.buffer
            processor = self._processor
            to_24k = LinearResampler(rate, SAMPLE_RATE)

            def callback(outdata, frames, time_info, status):
                samples = buffer.read(frames)
                if processor is not None:
                    # The echo canceller needs exactly what is played, silence included.
                    if rate != SAMPLE_RATE:
                        reference = _to_int16(to_24k.process(samples.astype(np.float32)))
                    else:
                        reference = samples
                    processor.process_far(reference)
                if channels > 1:
                    samples = np.repeat(samples, channels)
                outdata[:] = samples.tobytes()

            return callback

        self._stream, _, _ = _open_stream("output", self._device_name, make_callback, self._finished)
        try:
            self._latency_ms = float(self._stream.latency) * 1000.0
        except Exception:
            self._latency_ms = 0.0
        if self._processor is not None:
            # Speaker latency plus a typical capture latency; WebRTC refines this itself.
            self._processor.set_delay_ms(self._latency_ms + 40)

    def _finished(self) -> None:
        if not self._closing:
            self._on_failure("The speaker stopped unexpectedly. Was it unplugged?")

    def enqueue(self, item_id: str, pcm24k: bytes) -> None:
        if self.buffer is not None:
            self.buffer.enqueue(item_id, pcm24k)

    def interrupt(self) -> None:
        if self.buffer is not None:
            self.buffer.interrupt()

    def played_ms(self, item_id: str) -> float:
        """Milliseconds of the item handed to the audio device."""
        return self.buffer.played_ms(item_id) if self.buffer is not None else 0.0

    def heard_ms(self, item_id: str) -> float:
        """Milliseconds of the item that reached the user's ears (minus output latency)."""
        return max(0.0, self.played_ms(item_id) - self._latency_ms)

    def forget(self) -> None:
        if self.buffer is not None:
            self.buffer.forget()

    @property
    def level(self) -> float:
        return self.buffer.level if self.buffer is not None else 0.0

    def close(self) -> None:
        self._closing = True
        stream, self._stream = self._stream, None
        if self.buffer is not None:
            self.buffer.interrupt()
        if stream is not None:
            try:
                stream.abort()
                stream.close()
            except Exception as exc:
                log.debug("Error closing speaker: %s", exc)
