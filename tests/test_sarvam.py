"""Drives SarvamEngine against local fake Sarvam services (speech-to-text, chat, text-to-speech)."""

import asyncio
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from websockets.asyncio.server import serve

from naretta.capability_log import CapabilityLog
from naretta.config import SessionConfig
from naretta.engines import ProviderEngine
from naretta.realtime import CONNECTED, CONNECTING, DISCONNECTED
from naretta.sarvam import CREDIT_EXHAUSTED, SarvamEngine
from test_engine import FakeMic, FakeSpeaker, Recorder, audio_b64, fake_audio, wait_until

KEY = "sk_test_0123456789abcdef"


@pytest.fixture(autouse=True)
def fresh_devices():
    FakeMic.instances.clear()
    FakeSpeaker.instances.clear()


class FakeChat:
    """An HTTP server that streams scripted chat replies and records each request."""

    def __init__(self, replies):
        self.replies = list(replies)  # each: a list of chunk dicts, or ("error", status, body)
        self.requests = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append({"key": self.headers.get("api-subscription-key"), "body": body})
                reply = fake.replies.pop(0) if fake.replies else [text_chunk("Okay.")]
                if isinstance(reply, tuple):
                    _, status, error = reply
                    data = json.dumps({"error": error}).encode()
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for chunk in reply:
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()


def text_chunk(text):
    return {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}


def tool_chunks(name, arguments):
    first = {"index": 0, "id": "call_1", "type": "function", "function": {"name": name, "arguments": arguments[:10]}}
    rest = {"index": 0, "id": None, "function": {"name": None, "arguments": arguments[10:]}}
    return [
        {"choices": [{"index": 0, "delta": {"tool_calls": [first]}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [rest]}, "finish_reason": "tool_calls"}]},
    ]


class FakeSarvamSockets:
    """Speech-to-text and text-to-speech on one WebSocket server, routed by path."""

    def __init__(self):
        self.stt = None
        self.stt_queries = []
        self.tts_configs = []
        self.tts_texts = []
        self.audio_ms = 1000

    async def handler(self, ws):
        path = ws.request.path
        if path.startswith("/speech-to-text-realtime/ws"):
            self.stt_queries.append(path)
            self.stt = ws
            await ws.send(json.dumps({"event": "session.begin"}))
            async for _ in ws:
                pass
        elif path.startswith("/text-to-speech/ws"):
            text = ""
            async for message in ws:
                event = json.loads(message)
                if event["type"] == "config":
                    self.tts_configs.append(event["data"])
                elif event["type"] == "text":
                    assert any(ch.isalpha() for ch in event["data"]["text"]), "Bulbul rejects text without letters"
                    text += event["data"]["text"]
                elif event["type"] == "flush":
                    self.tts_texts.append(text)
                    await ws.send(json.dumps({"type": "audio", "data": {"content_type": "audio/pcm", "audio": audio_b64(self.audio_ms)}}))
                    await ws.send(json.dumps({"type": "event", "data": {"event_type": "final"}}))

    async def say(self, *events):
        for event in events:
            await self.stt.send(json.dumps(event))


def config(chat, sockets_port, **kw):
    return SessionConfig(
        api_key=KEY, provider="sarvam", sarvam_api_url=chat.url,
        sarvam_ws_url=f"ws://127.0.0.1:{sockets_port}", transcribe_language="en", **kw,
    )


def run(engine, chat, fake, scenario, **cfg):
    async def main():
        async with serve(fake.handler, "127.0.0.1", 0) as server:
            engine.start(config(chat, server.sockets[0].getsockname()[1], **cfg))
            await wait_until(lambda: fake.stt is not None)
            await scenario()

    try:
        asyncio.run(main())
    finally:
        engine.shutdown()
        chat.close()


def test_conversation_and_barge_in(tmp_path):
    rec = Recorder()
    engine = SarvamEngine(rec, audio_factory=fake_audio, capability_log=CapabilityLog(tmp_path / "m.jsonl"))
    chat = FakeChat([[text_chunk(" "), text_chunk("Hi, I'm Naretta. "), text_chunk("How are you today?")], [text_chunk("Sure.")]])
    fake = FakeSarvamSockets()

    async def scenario():
        FakeMic.instances[-1].on_chunk(b"\x10\x00" * 2400)  # 100 ms at 24 kHz
        await fake.say(
            {"event": "vad.speech_start"},
            {"event": "transcript.partial", "text": "Hello"},
            {"event": "vad.speech_end"},
            {"event": "transcript.final", "text": "Hello there"},
        )
        speaker = FakeSpeaker.instances[-1]
        await wait_until(lambda: speaker.buffer.playing)
        speaker.advance(300)  # she is heard for 300 ms, then interrupted
        await fake.say({"event": "vad.speech_start"}, {"event": "transcript.final", "text": "Wait, one question"})
        await wait_until(lambda: len(chat.requests) == 2)
        await wait_until(lambda: len(fake.tts_texts) == 2)
        engine.stop()
        await wait_until(lambda: rec.of("state")[-1] == (DISCONNECTED,))

    run(engine, chat, fake, scenario)

    query = fake.stt_queries[0]
    assert "language_code=en-IN" in query and "sample_rate=16000" in query and "model=saaras%3Av4" in query
    assert fake.tts_configs[0]["speaker"] == "simran_en_conversation"
    assert fake.tts_configs[0]["speech_sample_rate"] == 24000 and fake.tts_configs[0]["output_audio_codec"] == "linear16"
    assert fake.tts_texts[0] == " Hi, I'm Naretta. How are you today?"
    first = chat.requests[0]
    assert first["key"] == KEY and first["body"]["model"] == "sarvam-105b-conversations" and first["body"]["stream"]
    assert first["body"]["messages"][0]["role"] == "system"
    assert first["body"]["messages"][1:] == [{"role": "user", "content": "Hello there"}]
    # The second request carries only what was heard of the interrupted reply.
    heard = chat.requests[1]["body"]["messages"][2]
    assert heard["role"] == "assistant" and heard["content"].startswith("Hi,") and heard["content"].endswith("…")
    assert chat.requests[1]["body"]["messages"][3] == {"role": "user", "content": "Wait, one question"}
    assert ("sv_u1", "Hello", "partial") in [u for u in rec.of("user")]
    assert any(a[2] == "interrupted" for a in rec.of("assistant"))
    assert (CONNECTED,) in rec.of("state")
    assert FakeMic.instances[-1].closed and FakeSpeaker.instances[-1].closed


def test_missing_capability_is_recorded_then_answered(tmp_path):
    rec = Recorder()
    log = CapabilityLog(tmp_path / "m.jsonl")
    engine = SarvamEngine(rec, audio_factory=fake_audio, capability_log=log)
    arguments = json.dumps({"request": "Weather in Chennai", "capability": "live weather"})
    chat = FakeChat([tool_chunks("log_missing_capability", arguments), [text_chunk("I can't check live weather.")]])
    fake = FakeSarvamSockets()

    async def scenario():
        await fake.say({"event": "vad.speech_start"}, {"event": "vad.speech_end"},
                       {"event": "transcript.final", "text": "What's the weather in Chennai?"})
        await wait_until(lambda: fake.tts_texts)
        engine.stop()
        await wait_until(lambda: rec.of("state")[-1] == (DISCONNECTED,))

    run(engine, chat, fake, scenario)
    [entry] = log.entries()
    assert entry["capability"] == "live weather"
    follow_up = chat.requests[1]["body"]["messages"]
    assert follow_up[-2]["tool_calls"][0]["function"]["arguments"] == arguments
    assert follow_up[-1]["role"] == "tool" and follow_up[-1]["tool_call_id"] == "call_1"
    assert fake.tts_texts == ["I can't check live weather."]


def test_end_conversation_says_goodbye_then_stops(tmp_path):
    rec = Recorder()
    engine = SarvamEngine(rec, audio_factory=fake_audio, capability_log=CapabilityLog(tmp_path / "m.jsonl"))
    chat = FakeChat([tool_chunks("end_conversation", "{}"), [text_chunk("Bye for now!")]])
    fake = FakeSarvamSockets()
    fake.audio_ms = 300

    async def scenario():
        await fake.say({"event": "vad.speech_start"}, {"event": "vad.speech_end"},
                       {"event": "transcript.final", "text": "That's all, bye!"})
        await wait_until(lambda: fake.tts_texts)
        await asyncio.sleep(0.3)
        assert rec.of("state")[-1] == (CONNECTED,)  # still saying goodbye
        FakeSpeaker.instances[-1].advance(300)
        await wait_until(lambda: rec.of("state")[-1] == (DISCONNECTED,))

    run(engine, chat, fake, scenario)
    assert chat.requests[1]["body"]["tool_choice"] == "none"
    assert fake.tts_texts == ["Bye for now!"]
    assert FakeMic.instances[-1].closed


def test_running_out_of_sarvam_credit_stops_with_a_clear_message(tmp_path):
    rec = Recorder()
    engine = SarvamEngine(rec, audio_factory=fake_audio, capability_log=CapabilityLog(tmp_path / "m.jsonl"))
    chat = FakeChat([("error", 429, {"code": "insufficient_quota_error", "message": "Insufficient credits"})])
    fake = FakeSarvamSockets()

    async def scenario():
        await fake.say({"event": "vad.speech_start"}, {"event": "transcript.final", "text": "Hi"})
        await wait_until(lambda: rec.of("error"))
        await wait_until(lambda: rec.of("state")[-1] == (DISCONNECTED,))

    run(engine, chat, fake, scenario)
    assert rec.of("error")[0][0] == CREDIT_EXHAUSTED
    assert len(fake.stt_queries) == 1  # no reconnecting
    assert FakeMic.instances[-1].closed


def test_wake_word_mode_ignores_chatter(tmp_path):
    rec = Recorder()
    engine = SarvamEngine(rec, audio_factory=fake_audio, capability_log=CapabilityLog(tmp_path / "m.jsonl"))
    chat = FakeChat([[text_chunk("Here's an idea.")]])
    fake = FakeSarvamSockets()

    async def scenario():
        await fake.say({"event": "vad.speech_start"}, {"event": "transcript.final", "text": "Did you feed the cat?"})
        await fake.say({"event": "vad.speech_start"}, {"event": "transcript.final", "text": "Hey Naretta, what's a good movie?"})
        await wait_until(lambda: fake.tts_texts)
        engine.stop()
        await wait_until(lambda: rec.of("state")[-1] == (DISCONNECTED,))

    run(engine, chat, fake, scenario, listen_mode="wake")
    assert len(chat.requests) == 1
    assert chat.requests[0]["body"]["messages"][1:] == [{"role": "user", "content": "Hey Naretta, what's a good movie?"}]
    assert [u[1] for u in rec.of("user") if u[2] == "final"] == ["Hey Naretta, what's a good movie?"]
    assert rec.of("wake")[:2] == [(False,), (True,)]


def test_provider_engine_switches_services_and_hides_the_old_ones_callbacks():
    made = {}

    class Fake:
        def __init__(self, listener, history=None):
            self.listener, self.history, self.calls = listener, history, []
            made[type(self).__name__] = self

        def __getattr__(self, name):
            return lambda *args: self.calls.append(name)

    class OpenAIFake(Fake):
        pass

    class SarvamFake(Fake):
        pass

    rec = Recorder()
    engine = ProviderEngine(rec, engines={"openai": OpenAIFake, "sarvam": SarvamFake})
    assert made["OpenAIFake"].history is made["SarvamFake"].history  # one shared conversation
    engine.start(SessionConfig(api_key="k", provider="openai"))
    engine.restart(SessionConfig(api_key="k", provider="sarvam"))
    assert made["OpenAIFake"].calls == ["start", "stop"] and made["SarvamFake"].calls == ["start"]
    made["OpenAIFake"].listener.on_connection_state(DISCONNECTED)  # late, from the old service
    made["SarvamFake"].listener.on_connection_state(CONNECTING)
    assert rec.of("state") == [(CONNECTING,)]
