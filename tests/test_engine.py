"""Drives RealtimeEngine against a local fake Realtime server with fake audio devices."""

import asyncio
import base64
import json
import threading
import time
from http import HTTPStatus

import numpy as np
import pytest
from websockets.asyncio.server import serve

from naretta import realtime
from naretta.audio import PlaybackBuffer
from naretta.config import SessionConfig
from naretta.realtime import CONNECTED, DISCONNECTED, RealtimeEngine

API_KEY = "sk-test-0123456789abcdefghij"


class Recorder:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = []

    def _add(self, *call):
        with self.lock:
            self.calls.append(call)

    def of(self, name):
        with self.lock:
            return [c[1:] for c in self.calls if c[0] == name]

    def on_connection_state(self, state):
        self._add("state", state)

    def on_speaking(self, speaking):
        self._add("speaking", speaking)

    def on_error(self, message):
        self._add("error", message)

    def on_notice(self, message):
        self._add("notice", message)

    def on_user_turn(self, item_id, text, status):
        self._add("user", item_id, text, status)

    def on_assistant_turn(self, item_id, text, status):
        self._add("assistant", item_id, text, status)

    def on_history_cleared(self):
        self._add("cleared")

    def on_wake_state(self, awake):
        self._add("wake", awake)


class FakeMic:
    instances = []

    def __init__(self, on_chunk):
        self.on_chunk = on_chunk
        self.closed = False
        FakeMic.instances.append(self)

    def start(self):
        pass

    def close(self):
        self.closed = True


class FakeSpeaker:
    instances = []

    def __init__(self, on_playing_changed):
        self.buffer = PlaybackBuffer(24000, on_playing_changed)
        self.closed = False
        FakeSpeaker.instances.append(self)

    def start(self):
        pass

    def advance(self, ms):
        self.buffer.read(int(24 * ms))

    def enqueue(self, item_id, pcm):
        self.buffer.enqueue(item_id, pcm)

    def interrupt(self):
        self.buffer.interrupt()

    def played_ms(self, item_id):
        return self.buffer.played_ms(item_id)

    def heard_ms(self, item_id):
        return self.buffer.played_ms(item_id)

    def forget(self):
        self.buffer.forget()

    def close(self):
        self.closed = True


def fake_audio(cfg, on_chunk, on_playing_changed, on_failure):
    return FakeMic(on_chunk), FakeSpeaker(on_playing_changed)


@pytest.fixture(autouse=True)
def fast_reconnect(monkeypatch):
    monkeypatch.setattr(realtime, "RECONNECT_DELAYS", (0.05, 0.05, 0.05))
    FakeMic.instances.clear()
    FakeSpeaker.instances.clear()


async def wait_until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met in time")


def audio_b64(ms):
    return base64.b64encode(np.full(24 * ms, 1000, dtype=np.int16).tobytes()).decode()


async def recv_type(ws, kind, timeout=5.0):
    while True:
        event = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if event["type"] == kind:
            return event


def config(port):
    return SessionConfig(api_key=API_KEY, url=f"ws://127.0.0.1:{port}", voice="cedar")


def test_conversation_and_barge_in():
    rec = Recorder()
    engine = RealtimeEngine(rec, audio_factory=fake_audio)
    seen = {}

    async def handler(ws):
        seen["auth"] = ws.request.headers["Authorization"]
        update = await recv_type(ws, "session.update")
        seen["session"] = update["session"]
        send = lambda e: ws.send(json.dumps(e))

        # The mic streams audio to the server.
        FakeMic.instances[-1].on_chunk(b"\x01\x00" * 960)
        append = await recv_type(ws, "input_audio_buffer.append")
        seen["append"] = append["audio"]

        await send({"type": "input_audio_buffer.speech_started", "item_id": "u1", "audio_start_ms": 0})
        await send({"type": "conversation.item.input_audio_transcription.completed", "item_id": "u1", "content_index": 0, "transcript": "Hello there"})
        await send({"type": "response.created", "response": {"id": "r1", "status": "in_progress"}})
        await send({"type": "response.output_item.added", "response_id": "r1", "output_index": 0, "item": {"id": "a1", "type": "message", "role": "assistant"}})
        await send({"type": "response.output_audio_transcript.delta", "response_id": "r1", "item_id": "a1", "output_index": 0, "content_index": 0, "delta": "Hi, I'm Naretta. What would you like to talk about today?"})
        await send({"type": "response.output_audio.delta", "response_id": "r1", "item_id": "a1", "output_index": 0, "content_index": 0, "delta": audio_b64(1000)})

        speaker = FakeSpeaker.instances[-1]
        await wait_until(lambda: speaker.buffer.playing)
        speaker.advance(300)  # the user hears 300 ms, then talks over Naretta

        await send({"type": "input_audio_buffer.speech_started", "item_id": "u2", "audio_start_ms": 2000})
        seen["truncate"] = await recv_type(ws, "conversation.item.truncate")
        # Late audio from the cancelled response must not be played.
        await send({"type": "response.output_audio.delta", "response_id": "r1", "item_id": "a1", "output_index": 0, "content_index": 0, "delta": audio_b64(500)})
        await send({"type": "response.done", "response": {"id": "r1", "status": "cancelled", "status_details": {"type": "cancelled", "reason": "turn_detected"}}})
        await send({"type": "conversation.item.input_audio_transcription.completed", "item_id": "u2", "content_index": 0, "transcript": "Wait, one question"})
        seen["done"] = True
        await ws.wait_closed()

    async def scenario():
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            engine.start(config(port))
            await wait_until(lambda: seen.get("done"))
            await wait_until(lambda: rec.of("user") and rec.of("user")[-1][2] == "final")
            await asyncio.sleep(0.1)
            speaker = FakeSpeaker.instances[-1]
            assert not speaker.buffer.read(240).any()  # late audio was discarded
            engine.stop()
            await wait_until(lambda: rec.of("state") and rec.of("state")[-1] == (DISCONNECTED,))

    try:
        asyncio.run(scenario())
    finally:
        engine.shutdown()

    assert seen["auth"] == f"Bearer {API_KEY}"
    session = seen["session"]
    assert session["type"] == "realtime"
    assert session["output_modalities"] == ["audio"]
    assert session["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert session["audio"]["input"]["turn_detection"]["interrupt_response"] is True
    assert session["audio"]["output"]["voice"] == "cedar"
    assert "Hi, I’m Naretta." in session["instructions"]
    assert base64.b64decode(seen["append"]) == b"\x01\x00" * 960

    truncate = seen["truncate"]
    assert truncate["item_id"] == "a1"
    assert truncate["content_index"] == 0
    assert truncate["audio_end_ms"] == 300

    assistant = rec.of("assistant")
    assert assistant[-1][0] == "a1" and assistant[-1][2] == "interrupted"
    heard = assistant[-1][1]
    assert heard.startswith("Hi,") and heard.endswith("…")

    turns = [(t.item_id, t.role, t.text) for t in engine.history._turns]
    assert turns == [("u1", "user", "Hello there"), ("a1", "assistant", heard), ("u2", "user", "Wait, one question")]
    assert (CONNECTED,) in rec.of("state")
    assert FakeMic.instances[-1].closed and FakeSpeaker.instances[-1].closed


def test_invalid_api_key_is_reported_without_the_key():
    rec = Recorder()
    engine = RealtimeEngine(rec, audio_factory=fake_audio)

    def reject(connection, request):
        return connection.respond(HTTPStatus.UNAUTHORIZED, "Incorrect API key provided\n")

    async def handler(ws):
        pass

    async def scenario():
        async with serve(handler, "127.0.0.1", 0, process_request=reject) as server:
            engine.start(config(server.sockets[0].getsockname()[1]))
            await wait_until(lambda: rec.of("error"))
            await wait_until(lambda: rec.of("state")[-1] == (DISCONNECTED,))

    try:
        asyncio.run(scenario())
    finally:
        engine.shutdown()
    message = rec.of("error")[0][0]
    assert "rejected the API key" in message
    assert API_KEY not in message
    assert FakeMic.instances[-1].closed  # devices released


def test_dropped_connection_reconnects_and_replays_history():
    rec = Recorder()
    engine = RealtimeEngine(rec, audio_factory=fake_audio)
    connections = []

    async def handler(ws):
        connections.append(ws)
        await recv_type(ws, "session.update")
        send = lambda e: ws.send(json.dumps(e))
        if len(connections) == 1:
            await send({"type": "input_audio_buffer.speech_started", "item_id": "u1"})
            await send({"type": "conversation.item.input_audio_transcription.completed", "item_id": "u1", "transcript": "My name is Sam"})
            await send({"type": "response.created", "response": {"id": "r1"}})
            await send({"type": "response.output_item.added", "response_id": "r1", "item": {"id": "a1", "type": "message", "role": "assistant"}})
            await send({"type": "response.output_audio_transcript.done", "response_id": "r1", "item_id": "a1", "transcript": "Nice to meet you, Sam!"})
            await send({"type": "response.done", "response": {"id": "r1", "status": "completed"}})
            await asyncio.sleep(0.1)
            await ws.close(1011, "server hiccup")
            return
        replayed = [await recv_type(ws, "conversation.item.create") for _ in range(2)]
        connections.append([r["item"] for r in replayed])
        await ws.wait_closed()

    async def scenario():
        async with serve(handler, "127.0.0.1", 0) as server:
            engine.start(config(server.sockets[0].getsockname()[1]))
            await wait_until(lambda: len(connections) == 3)
            engine.stop()
            await wait_until(lambda: rec.of("state")[-1] == (DISCONNECTED,))

    try:
        asyncio.run(scenario())
    finally:
        engine.shutdown()
    replayed = connections[2]
    assert replayed[0]["role"] == "user" and replayed[0]["content"][0]["text"] == "My name is Sam"
    assert replayed[1]["role"] == "assistant"
    assert replayed[1]["content"][0] == {"type": "output_text", "text": "Nice to meet you, Sam!"}
    assert ("reconnecting",) in rec.of("state")
    assert not rec.of("error")


def test_new_conversation_starts_a_fresh_session():
    rec = Recorder()
    engine = RealtimeEngine(rec, audio_factory=fake_audio)
    connections = []

    async def handler(ws):
        connections.append(ws)
        await recv_type(ws, "session.update")
        if len(connections) == 1:
            await ws.send(json.dumps({"type": "input_audio_buffer.speech_started", "item_id": "u1"}))
            await ws.send(json.dumps({"type": "conversation.item.input_audio_transcription.completed", "item_id": "u1", "transcript": "Remember the number 42"}))
        else:
            try:
                extra = await asyncio.wait_for(ws.recv(), 0.5)
                connections.append(json.loads(extra))
            except asyncio.TimeoutError:
                connections.append(None)
        await ws.wait_closed()

    async def scenario():
        async with serve(handler, "127.0.0.1", 0) as server:
            engine.start(config(server.sockets[0].getsockname()[1]))
            await wait_until(lambda: rec.of("user") and rec.of("user")[-1][2] == "final")
            engine.new_conversation()
            await wait_until(lambda: len(connections) == 3)
            engine.stop()
            await wait_until(lambda: rec.of("state")[-1] == (DISCONNECTED,))

    try:
        asyncio.run(scenario())
    finally:
        engine.shutdown()
    assert rec.of("cleared")
    assert engine.history.is_empty()
    assert connections[2] is None  # nothing replayed into the new session


def test_quiet_microphone_warning():
    rec = Recorder()
    engine = RealtimeEngine(rec, audio_factory=fake_audio)
    engine._mic_frames = 0
    engine._mic_heard_signal = engine._mic_warning_sent = False
    engine._stats_frames = engine._stats_peak = engine._stats_chunks = engine._stats_clipped = 0
    engine._clip_warning_sent = False
    try:
        for _ in range(260):  # ~10.4 s of near-silence (peak 20)
            engine._check_mic_signal(20, 960)
        assert len(rec.of("notice")) == 1 and "barely hear" in rec.of("notice")[0][0]
    finally:
        engine.shutdown()


def test_wake_word_mode():
    rec = Recorder()
    engine = RealtimeEngine(rec, audio_factory=fake_audio)
    seen = {}

    async def handler(ws):
        update = await recv_type(ws, "session.update")
        seen["start_td"] = update["session"]["audio"]["input"]["turn_detection"]
        send = lambda e: ws.send(json.dumps(e))
        # Background chatter is forgotten.
        await send({"type": "input_audio_buffer.speech_started", "item_id": "u1"})
        await send({"type": "conversation.item.input_audio_transcription.completed", "item_id": "u1", "transcript": "Did you feed the cat?"})
        seen["deleted"] = (await recv_type(ws, "conversation.item.delete"))["item_id"]
        # The wake word wakes Naretta and she answers that sentence.
        await send({"type": "input_audio_buffer.speech_started", "item_id": "u2"})
        await send({"type": "conversation.item.input_audio_transcription.completed", "item_id": "u2", "transcript": "Hey Naretta, what's a good movie?"})
        wake_update = await recv_type(ws, "session.update")
        seen["wake_td"] = wake_update["session"]["audio"]["input"]["turn_detection"]
        await recv_type(ws, "response.create")
        seen["done"] = True
        await ws.wait_closed()

    async def scenario():
        async with serve(handler, "127.0.0.1", 0) as server:
            cfg = SessionConfig(api_key=API_KEY, url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}", listen_mode="wake")
            engine.start(cfg)
            await wait_until(lambda: seen.get("done"))
            await asyncio.sleep(0.05)
            engine.stop()
            await wait_until(lambda: rec.of("state")[-1] == (DISCONNECTED,))

    try:
        asyncio.run(scenario())
    finally:
        engine.shutdown()
    assert seen["start_td"]["create_response"] is False  # waiting: no automatic replies
    assert seen["deleted"] == "u1"
    assert seen["wake_td"]["create_response"] is True and seen["wake_td"]["interrupt_response"] is True
    assert rec.of("wake")[:2] == [(False,), (True,)]
    assert [u[0] for u in rec.of("user") if u[2] == "final"] == ["u2"]  # chatter never shown
    assert [t.item_id for t in engine.history._turns] == ["u2"]


def test_clipping_microphone_warning():
    rec = Recorder()
    engine = RealtimeEngine(rec, audio_factory=fake_audio)
    engine._mic_frames = 0
    engine._mic_heard_signal, engine._mic_warning_sent, engine._clip_warning_sent = True, False, False
    engine._stats_frames = engine._stats_peak = engine._stats_chunks = engine._stats_clipped = 0
    try:
        for i in range(260):  # ~10 s; one block in five clips
            engine._check_mic_signal(32767 if i % 5 == 0 else 9000, 960)
        assert len(rec.of("notice")) == 1 and "too loud" in rec.of("notice")[0][0]
    finally:
        engine.shutdown()
