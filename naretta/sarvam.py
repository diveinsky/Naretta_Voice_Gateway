"""Naretta on Sarvam AI: speech-to-text, chat and text-to-speech chained into a conversation.

Sarvam has no speech-to-speech model, so each turn runs through three services:
  microphone -> Saaras realtime speech-to-text (WebSocket, with voice activity events)
             -> sarvam-105b-conversations chat (streamed over HTTP)
             -> Bulbul text-to-speech (WebSocket, streamed as the reply is written)
             -> speaker
The speech-to-text connection is the session; it reconnects like the OpenAI one. Barge-in,
history, wake word, tools and error handling reuse RealtimeEngine.
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import json
import logging
import threading
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from .audio import LinearResampler
from .config import (
    CAPABILITY_INSTRUCTIONS,
    CAPABILITY_TOOL,
    END_CONVERSATION_TOOL,
    LISTENING_INSTRUCTIONS,
    PERSONALITY,
    SAMPLE_RATE,
    SARVAM_CHAT_MODEL,
    SARVAM_LANGUAGES,
    SARVAM_SPOKEN_LANGUAGES,
    SARVAM_STT_MODEL,
    SARVAM_STT_RATE,
    SARVAM_TTS_MODEL,
    SessionConfig,
)
from .realtime import FatalSessionError, RealtimeEngine
from .redact import redact
from .wake import contains_wake_word

log = logging.getLogger(__name__)

KEY_REJECTED = "Sarvam rejected the API key. Open Settings and paste a valid Sarvam key."
CREDIT_EXHAUSTED = (
    "Your Sarvam credit has run out, so Naretta can't reply. Add credit in your Sarvam AI "
    "dashboard, then press Start."
)
RATE_LIMITED = "Sarvam is getting too many requests from this key right now. Wait a minute, then try again."

SPOKEN_STYLE = (
    "Your replies are spoken aloud by a text-to-speech voice, so write plain spoken sentences: "
    "no lists, headings, markdown, emoji or URLs. Keep most replies to two or three sentences; "
    "give step-by-step detail only when the user asks for it."
)
GOODBYE = "The conversation is ending. Say a short, warm goodbye in one sentence."
# Sarvam's VAD waits this long after you stop before ending your turn, which leaves room for a
# pause mid-sentence without making replies feel slow.
SILENCE_MS = 800
STT_CHUNK_BYTES = 3200  # 100 ms of 16 kHz 16-bit audio, as Sarvam recommends
MAX_TOOL_ROUNDS = 3


def _chat_tool(tool: dict) -> dict:
    """A Realtime API tool definition in chat-completions form."""
    return {
        "type": "function",
        "function": {"name": tool["name"], "description": tool["description"], "parameters": tool["parameters"]},
    }


TOOLS = [_chat_tool(CAPABILITY_TOOL), _chat_tool(END_CONVERSATION_TOOL)]


def describe_sarvam_error(status: int | None, code: str, message: str) -> tuple[str, bool]:
    """Returns (user-facing message, fatal) for an error from any Sarvam API."""
    text = f"{code} {message}".lower()
    if "quota" in text or "credit" in text or "balance" in text or "billing" in text:
        return (CREDIT_EXHAUSTED, True)
    if status in (401, 403) or "invalid_api_key" in text or "subscription_key" in text or "authentication" in text:
        return (KEY_REJECTED, True)
    if status == 429 or "rate_limit" in text:
        return (RATE_LIMITED, False)
    return (f"Sarvam reported a problem: {redact(message or code or status)}", False)


def _language(cfg: SessionConfig) -> str | None:
    return SARVAM_LANGUAGES.get(cfg.transcribe_language)


class SarvamEngine(RealtimeEngine):
    """Same public API and Listener callbacks as RealtimeEngine."""

    # Sarvam's connection times out after 60 s without audio, so muted chunks are still sent,
    # as silence.
    _SEND_WHILE_MUTED = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ids = itertools.count(1)

    # ------------------------------------------------------------------ connection

    @staticmethod
    def _stt_url(cfg: SessionConfig) -> str:
        query = {
            "model": SARVAM_STT_MODEL,
            "language_code": _language(cfg) or "auto",
            "sample_rate": SARVAM_STT_RATE,
            "encoding": "linear16",
            "silence_duration_ms": SILENCE_MS,
            "keyterms": json.dumps(["Naretta"]),  # helps it hear the wake word
        }
        return f"{cfg.sarvam_ws_url}/speech-to-text-realtime/ws?{urllib.parse.urlencode(query)}"

    @classmethod
    def _connect(cls, cfg: SessionConfig):
        return connect(
            cls._stt_url(cfg),
            additional_headers={"api-subscription-key": cfg.api_key},
            max_size=None,
            open_timeout=15,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=2,
        )

    @staticmethod
    def _describe_http_status(status: int, cfg: SessionConfig) -> tuple[str, bool]:
        message, fatal = describe_sarvam_error(status, "", "")
        if status >= 500:
            return (f"Sarvam's speech service is having trouble (HTTP {status}).", True)
        return (message if fatal or status == 429 else f"Sarvam refused the connection (HTTP {status}).", False)

    @staticmethod
    def _describe_close(closed) -> str | None:
        # 1003: bad key, rate limit or quota; 4000: invalid parameter or account not enabled.
        if closed.code not in (1003, 4000):
            return None
        message, fatal = describe_sarvam_error(None, "", closed.reason or "")
        return message if fatal else f"Sarvam closed the connection: {redact(closed.reason or closed.code)}"

    async def _begin_session(self, cfg: SessionConfig) -> None:
        self._resampler = LinearResampler(SAMPLE_RATE, SARVAM_STT_RATE)
        self._stt_buffer = bytearray()

    async def _apply_awake(self, awake: bool) -> None:
        pass  # replies are started here, not by the server, so nothing to tell it

    async def _discard_pending_input(self) -> None:
        pass  # muting sends silence, which ends any half-spoken turn

    def _reset_session_state(self) -> None:
        super()._reset_session_state()
        self._reply_task: asyncio.Task | None = None
        self._user_item: str | None = None
        self._detected_language: str | None = None

    def _end_session(self) -> None:
        task = getattr(self, "_reply_task", None)
        if task is not None and not task.done():
            task.cancel()
        super()._end_session()

    # ------------------------------------------------------------------ microphone

    async def _send_mic_loop(self, ws) -> None:
        queue = self._mic_queue
        while True:
            chunk = await queue.get()
            samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
            if self._muted:
                samples = np.zeros_like(samples)  # keeps the connection alive without speech
            pcm = np.clip(np.round(self._resampler.process(samples)), -32768, 32767).astype(np.int16)
            self._stt_buffer += pcm.tobytes()
            while len(self._stt_buffer) >= STT_CHUNK_BYTES:
                piece = bytes(self._stt_buffer[:STT_CHUNK_BYTES])
                del self._stt_buffer[:STT_CHUNK_BYTES]
                await ws.send(json.dumps({"event": "audio_input", "audio": base64.b64encode(piece).decode()}))

    # ------------------------------------------------------------------ speech-to-text events

    async def _handle_event(self, event: dict) -> None:
        kind = event.get("event", "")
        handler = self._SARVAM_HANDLERS.get(kind)
        if handler is not None:
            await handler(self, event)
        else:
            log.debug("Sarvam event %s", kind)

    def _new_user_item(self) -> str:
        item_id = f"sv_u{next(self._ids)}"
        self._user_item = item_id
        if self._responds():
            self.history.upsert(item_id, "user")
            self._listener.on_user_turn(item_id, "", "pending")
        else:
            self._sleep_items.add(item_id)  # shown only if it contains the wake word
        return item_id

    async def _on_stt_speech_start(self, event: dict) -> None:
        log.info("Speech detected")
        self._user_speaking = True
        self._end_pending = False
        self._touch()
        await self._barge_in()
        if self._user_item is None:
            self._new_user_item()

    async def _on_stt_speech_end(self, event: dict) -> None:
        log.info("Speech ended")
        self._user_speaking = False
        self._touch()

    async def _on_stt_partial(self, event: dict) -> None:
        item_id = self._user_item or self._new_user_item()
        text = (event.get("text") or "").strip()
        if item_id in self._sleep_items or not text:
            return
        self.history.upsert(item_id, "user", text=text)
        self._listener.on_user_turn(item_id, text, "partial")

    async def _on_stt_final(self, event: dict) -> None:
        item_id = self._user_item or self._new_user_item()
        self._user_item = None
        text = (event.get("text") or "").strip()
        if event.get("language"):
            self._detected_language = event["language"]
        if item_id in self._sleep_items:
            self._sleep_items.discard(item_id)
            if text and contains_wake_word(text):
                self.history.upsert(item_id, "user", text=text, final=True)
                self._listener.on_user_turn(item_id, text, "final")
                await self._set_awake(True)
                self._start_reply()
            return
        if not text:
            self.history.remove(item_id)
            self._listener.on_user_turn(item_id, "", "empty")
            return
        self.history.upsert(item_id, "user", text=text, final=True)
        self._listener.on_user_turn(item_id, text, "final")
        self._touch()
        if self._responds():
            self._start_reply()

    async def _on_stt_error(self, event: dict) -> None:
        code = event.get("code") or ""
        message = event.get("message") or ""
        log.warning("Sarvam speech-to-text error %s: %s", code, redact(message))
        if code == "inactivity_timeout":
            raise ConnectionClosed(None, None)  # reconnect
        user_message, fatal = describe_sarvam_error(event.get("status_code"), code, message)
        if fatal:
            raise FatalSessionError(user_message)
        if event.get("is_fatal"):
            raise FatalSessionError(user_message)
        if code != "chunk_too_large":
            self._listener.on_notice(user_message)

    async def _on_stt_session(self, event: dict) -> None:
        if event.get("event") == "session.end":
            log.info("Sarvam speech-to-text session ended (%s s of audio)", event.get("audio_duration_s"))
        else:
            log.info("Server: %s", event.get("event"))

    # ------------------------------------------------------------------ barge-in

    async def _barge_in(self) -> None:
        """The user started talking: silence Naretta, stop the reply, keep what was heard."""
        speaker = self._speaker
        if speaker is not None:
            speaker.interrupt()
        task = self._reply_task
        if task is not None and not task.done():
            task.cancel()
        for item_id in list(self._assistant_text):
            if item_id in self._interrupted:
                continue
            received = self._received_ms.get(item_id, 0.0)
            played = speaker.played_ms(item_id) if speaker else 0.0
            turn = self.history.get(item_id)
            if turn is not None and turn.final and item_id in self._audio_done and played >= received - 1:
                self._assistant_text.pop(item_id, None)  # heard in full
                self._received_ms.pop(item_id, None)
                continue
            self._mark_interrupted(item_id, speaker.heard_ms(item_id) if speaker else 0.0)

    # ------------------------------------------------------------------ replies

    def _start_reply(self) -> None:
        task = self._reply_task
        if task is not None and not task.done():
            task.cancel()
        self._reply_task = asyncio.create_task(self._reply())

    def _chat_messages(self) -> list[dict]:
        system = "\n\n".join((PERSONALITY, LISTENING_INSTRUCTIONS, CAPABILITY_INSTRUCTIONS, SPOKEN_STYLE))
        return [{"role": "system", "content": system}] + self.history.chat_messages()

    def _spoken_language(self) -> str:
        cfg = self._cfg
        chosen = _language(cfg) if cfg is not None else None
        for code in (chosen, self._detected_language):
            if code in SARVAM_SPOKEN_LANGUAGES:
                return code
        return "en-IN"

    async def _reply(self) -> None:
        response_id = f"sv_r{next(self._ids)}"
        self._active_response = response_id
        messages = self._chat_messages()
        spoken: list[str] = []
        ending = False
        try:
            for round_number in range(MAX_TOOL_ROUNDS):
                last = ending or round_number == MAX_TOOL_ROUNDS - 1
                item_id = f"sv_a{next(self._ids)}"
                text, calls = await self._say(item_id, messages, tool_choice="none" if last else "auto")
                if text:
                    spoken.append(item_id)
                if not calls:
                    break
                messages.append({"role": "assistant", "content": text or None, "tool_calls": calls})
                for call in calls:
                    name = call["function"]["name"]
                    if name == CAPABILITY_TOOL["name"]:
                        result = {"ok": self._record_missing_capability(call["function"]["arguments"], text)}
                    elif name == END_CONVERSATION_TOOL["name"]:
                        ending = True
                        result = {"ok": True, "next": GOODBYE}
                    else:
                        result = {"ok": False, "error": f"Unknown function {name}"}
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)})
                if ending and text:
                    break  # the goodbye was said before the call
            log.info("Reply completed")
        except asyncio.CancelledError:
            log.info("Reply cancelled")
            raise
        except Exception as exc:  # a reply is its own task, so report what would otherwise vanish
            log.warning("Reply failed: %s", redact(exc))
            if self._session_failure is None:
                self._listener.on_notice("Naretta couldn't finish that reply. Try saying it again.")
            return
        finally:
            if self._active_response == response_id:
                self._active_response = None
            self._touch()
        if ending:
            await self._end_after_goodbye(spoken)

    async def _say(self, item_id: str, messages: list[dict], tool_choice: str) -> tuple[str, list[dict]]:
        """Streams one chat completion, speaking its text as it arrives. Returns (text, tool calls)."""
        cfg = self._cfg
        text = ""
        calls: dict[int, dict] = {}
        tts: _Speech | None = None
        try:
            async for chunk in self._chat_stream(cfg, messages, tool_choice):
                if chunk.get("usage"):
                    log.info("Chat used %s prompt and %s reply tokens",
                             chunk["usage"].get("prompt_tokens"), chunk["usage"].get("completion_tokens"))
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    for call in delta.get("tool_calls") or []:
                        slot = calls.setdefault(call.get("index", 0), {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        slot["id"] = call.get("id") or slot["id"]
                        function = call.get("function") or {}
                        slot["function"]["name"] += function.get("name") or ""
                        slot["function"]["arguments"] += function.get("arguments") or ""
                    piece = delta.get("content")
                    if not piece:
                        continue
                    if tts is None:
                        tts = await _Speech.open(self, cfg, item_id, self._spoken_language())
                        self._assistant_text[item_id] = ""
                        self.history.upsert(item_id, "assistant")
                    text += piece
                    self._assistant_text[item_id] = text
                    self.history.upsert(item_id, "assistant", text=text)
                    self._listener.on_assistant_turn(item_id, text, "partial")
                    await tts.send(piece)
            if tts is not None:
                await tts.finish()
                self._audio_done.add(item_id)
                text = text.strip()
                self._assistant_text[item_id] = text
                self.history.upsert(item_id, "assistant", text=text, final=True)
                self._listener.on_assistant_turn(item_id, text, "final")
                log.info("Spoke %d characters", len(text))
        finally:
            if tts is not None:
                await tts.close()
        return text.strip(), [calls[i] for i in sorted(calls)]

    async def _chat_stream(self, cfg: SessionConfig, messages: list[dict], tool_choice: str):
        """Yields the chat completion's stream chunks; the HTTP call runs on a worker thread."""
        body = {
            "model": SARVAM_CHAT_MODEL,
            "messages": messages,
            "stream": True,
            "tools": TOOLS,
            "tool_choice": tool_choice,
            "max_tokens": 600,
        }
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        stop = threading.Event()
        put = lambda item: loop.call_soon_threadsafe(queue.put_nowait, item)
        worker = threading.Thread(
            target=_stream_chat,
            args=(f"{cfg.sarvam_api_url}/v1/chat/completions", cfg.api_key, body, put, stop),
            name="naretta-sarvam-chat",
            daemon=True,
        )
        worker.start()
        try:
            while True:
                kind, value = await queue.get()
                if kind == "chunk":
                    yield value
                elif kind == "error":
                    status, code, message = value
                    log.warning("Sarvam chat error %s %s: %s", status, code, redact(message))
                    user_message, fatal = describe_sarvam_error(status, code, message)
                    if fatal:
                        self._fail_session(FatalSessionError(user_message))
                    else:
                        self._listener.on_notice(user_message)
                    return
                else:
                    return
        finally:
            stop.set()  # a cancelled reply stops reading at the next chunk

    _SARVAM_HANDLERS = {
        "session.begin": _on_stt_session,
        "session.end": _on_stt_session,
        "vad.speech_start": _on_stt_speech_start,
        "vad.speech_end": _on_stt_speech_end,
        "transcript.partial": _on_stt_partial,
        "transcript.final": _on_stt_final,
        "error": _on_stt_error,
    }


def _stream_chat(url: str, key: str, body: dict, put, stop: threading.Event) -> None:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"api-subscription-key": key, "Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            for raw in response:
                if stop.is_set():
                    return
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    return
                try:
                    put(("chunk", json.loads(payload)))
                except ValueError:
                    log.warning("Ignoring a malformed chat chunk")
    except urllib.error.HTTPError as exc:
        try:
            error = (json.loads(exc.read() or b"{}").get("error") or {})
        except ValueError:
            error = {}
        put(("error", (exc.code, error.get("code") or "", error.get("message") or exc.reason or "")))
    except (OSError, ValueError) as exc:
        put(("error", (None, "network_error", "Can't reach Sarvam. Check your internet connection.")))
        log.warning("Sarvam chat request failed: %s", type(exc).__name__)
    finally:
        put(("end", None))


class _Speech:
    """One reply's text-to-speech stream: text goes in as it's written, audio goes to the speaker."""

    def __init__(self, engine: SarvamEngine, ws, item_id: str):
        self._engine = engine
        self._ws = ws
        self._item_id = item_id
        self._done = asyncio.Event()
        self._pending = ""  # text not sent yet
        self._sent = False
        self._reader = asyncio.create_task(self._read())

    @classmethod
    async def open(cls, engine: SarvamEngine, cfg: SessionConfig, item_id: str, language: str) -> "_Speech":
        url = f"{cfg.sarvam_ws_url}/text-to-speech/ws?model={SARVAM_TTS_MODEL}&send_completion_event=true"
        try:
            ws = await connect(url, additional_headers={"api-subscription-key": cfg.api_key}, max_size=None, open_timeout=15)
        except InvalidStatus as exc:
            message, fatal = describe_sarvam_error(exc.response.status_code, "", "")
            if fatal:
                engine._fail_session(FatalSessionError(message))
            raise
        config = {
            "language_code": language,
            "speaker": cfg.sarvam_voice,
            "speech_sample_rate": SAMPLE_RATE,
            "output_audio_codec": "linear16",
            "min_buffer_size": 30,  # start speaking after the first few words
            "max_chunk_length": 150,
        }
        await ws.send(json.dumps({"type": "config", "data": config}))
        return cls(engine, ws, item_id)

    async def send(self, text: str) -> None:
        # Bulbul rejects a message with no letters in it (a lone comma or space from the stream),
        # so pieces wait until they include a word.
        self._pending += text
        if any(ch.isalpha() for ch in self._pending):
            await self._ws.send(json.dumps({"type": "text", "data": {"text": self._pending}}))
            self._pending = ""
            self._sent = True

    async def finish(self) -> None:
        """Flushes the last words and waits until all their audio has arrived."""
        if not self._sent:
            return  # nothing speakable, so no audio will come
        await self._ws.send(json.dumps({"type": "flush"}))
        try:
            await asyncio.wait_for(self._done.wait(), 30)
        except asyncio.TimeoutError:
            log.warning("Sarvam text-to-speech didn't finish in time")

    async def close(self) -> None:
        self._reader.cancel()
        await asyncio.gather(self._reader, return_exceptions=True)
        await self._ws.close()

    async def _read(self) -> None:
        engine, item_id = self._engine, self._item_id
        try:
            async for message in self._ws:
                event = json.loads(message)
                kind = event.get("type")
                data = event.get("data") or {}
                if kind == "audio":
                    if item_id in engine._interrupted:
                        continue
                    pcm = base64.b64decode(data.get("audio", ""))
                    engine._received_ms[item_id] = engine._received_ms.get(item_id, 0.0) + len(pcm) / 2 / SAMPLE_RATE * 1000
                    if engine._speaker is not None:
                        engine._speaker.enqueue(item_id, pcm)
                elif kind == "event" and data.get("event_type") == "final":
                    self._done.set()
                elif kind == "error":
                    log.warning("Sarvam text-to-speech error %s: %s", data.get("code"), redact(data.get("message")))
                    message, fatal = describe_sarvam_error(data.get("code"), "", data.get("message") or "")
                    if fatal:
                        engine._fail_session(FatalSessionError(message))
                    else:
                        engine._listener.on_notice(message)
                    self._done.set()
        except (ConnectionClosed, ValueError):
            pass
        finally:
            self._done.set()
