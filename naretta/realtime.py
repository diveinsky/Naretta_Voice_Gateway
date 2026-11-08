"""Speech-to-speech session with the OpenAI Realtime API over WebSocket.

The engine runs its own asyncio loop on a background thread so the Qt interface never
blocks. Public methods are thread-safe and return immediately; progress is reported
through a Listener, whose methods are called on the engine thread.
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import json
import logging
import math
import threading
from typing import Callable, Protocol

import numpy as np
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

from .audio import AudioError, Microphone, Speaker, VoiceProcessor, voice_level
from .config import PERSONALITY, SAMPLE_RATE, TRANSCRIBE_PROMPT, SessionConfig
from .history import ConversationHistory, estimate_heard_text
from .redact import redact, register_secret
from .wake import contains_wake_word

log = logging.getLogger(__name__)

# Connection states reported to the listener.
DISCONNECTED = "disconnected"
CONNECTING = "connecting"
RECONNECTING = "reconnecting"
CONNECTED = "connected"

RECONNECT_DELAYS = (1, 2, 4)
# In wake-word mode, Naretta goes back to waiting after this much quiet.
WAKE_IDLE_SECONDS = 20
QUIET_MIC_WARNING_SECONDS = 10
# A chunk peaking above this (about -36 dBFS) counts as real sound; speech peaks far higher,
# while a muted or gated microphone stays far below it.
SOUND_PEAK = 500
MIC_STATS_SECONDS = 10
# A chunk peaking here is clipped; if many are, the mic level in Windows is set too high.
CLIP_PEAK = 32000
CLIP_WARNING_RATIO = 0.05


def _turn_detection(responds: bool) -> dict:
    # Wake-word mode keeps voice detection running while waiting, but without auto-replies.
    return {
        "type": "semantic_vad",
        "eagerness": "auto",
        "create_response": responds,
        "interrupt_response": responds,
    }


def _dbfs(peak: int) -> float:
    return 20 * math.log10(max(peak, 1) / 32768)


class Listener(Protocol):
    def on_connection_state(self, state: str) -> None: ...
    def on_speaking(self, speaking: bool) -> None: ...
    def on_error(self, message: str) -> None: ...
    def on_notice(self, message: str) -> None: ...
    # status: pending | partial | final | failed | empty
    def on_user_turn(self, item_id: str, text: str, status: str) -> None: ...
    # status: partial | final | interrupted | empty
    def on_assistant_turn(self, item_id: str, text: str, status: str) -> None: ...
    def on_history_cleared(self) -> None: ...
    # Wake-word mode only: True while Naretta is actively conversing.
    def on_wake_state(self, awake: bool) -> None: ...


class FatalSessionError(Exception):
    """An error that retrying won't fix (bad key, no model access, ...)."""


class _SessionReset(Exception):
    pass


def describe_http_status(status: int, model: str) -> tuple[str, bool]:
    """Returns (user-facing message, retryable) for a rejected WebSocket handshake."""
    if status == 401:
        return ("OpenAI rejected the API key. Open Settings and paste a valid key.", False)
    if status == 403:
        return (
            f"This API key doesn't have access to the realtime model “{model}”. "
            "Check the key's project permissions on platform.openai.com.",
            False,
        )
    if status == 404:
        return (f"The realtime model “{model}” wasn't found.", False)
    if status == 429:
        return (
            "OpenAI says the rate limit or quota was exceeded. Check your plan and billing, "
            "then try again.",
            False,
        )
    if status >= 500:
        return (f"OpenAI's realtime service is having trouble (HTTP {status}).", True)
    return (f"OpenAI refused the connection (HTTP {status}).", False)


AudioFactory = Callable[..., tuple[Microphone, Speaker]]


def default_audio_factory(cfg: SessionConfig, on_chunk, on_playing_changed, on_failure):
    processor = VoiceProcessor.create() if cfg.echo_cancellation else None
    return (
        Microphone(cfg.input_device, on_chunk, on_failure, processor),
        Speaker(cfg.output_device, on_playing_changed, on_failure, processor, cfg.voice_volume),
    )


class RealtimeEngine:
    def __init__(self, listener: Listener, audio_factory: AudioFactory = default_audio_factory):
        self._listener = listener
        self._audio_factory = audio_factory
        self.history = ConversationHistory()

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="naretta-realtime", daemon=True
        )
        self._thread.start()

        self._task: asyncio.Task | None = None
        self._muted = False
        self._input_level = 0.0
        self._event_ids = itertools.count(1)
        self._session_tasks: tuple[asyncio.Task, ...] = ()
        self._reset_requested = False
        self._cfg: SessionConfig | None = None
        self._awake = True
        self._last_activity = 0.0
        self._mic: Microphone | None = None
        self._speaker: Speaker | None = None
        self._ws = None
        self._mic_queue: asyncio.Queue[bytes] | None = None
        self._reset_session_state()

    # ------------------------------------------------------------------ public API

    def start(self, cfg: SessionConfig) -> None:
        self._submit(self._start(cfg))

    def stop(self) -> None:
        self._submit(self._stop())

    def restart(self, cfg: SessionConfig) -> None:
        """Reopens audio devices and the session with new settings, keeping the history."""
        self._submit(self._restart(cfg))

    def set_muted(self, muted: bool) -> None:
        self._submit(self._set_muted(muted))

    def new_conversation(self) -> None:
        self._submit(self._new_conversation())

    def wake(self) -> None:
        """Wake-word mode: start talking without saying the wake word."""
        self._submit(self._set_awake(True))

    def levels(self) -> tuple[float, float]:
        """(microphone, Naretta's voice) loudness on a 0..1 scale, for the voice animation."""
        speaker = self._speaker
        return (self._input_level, speaker.level if speaker is not None else 0.0)

    def shutdown(self, timeout: float = 5.0) -> None:
        """Stops everything and releases audio devices. Blocks up to `timeout` seconds."""
        if not self._thread.is_alive():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._stop(), self._loop).result(timeout)
        except Exception as exc:
            log.warning("Shutdown did not finish cleanly: %s", redact(exc))
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout)

    # ------------------------------------------------------------------ loop-side commands

    def _submit(self, coro) -> None:
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        future.add_done_callback(self._log_failure)

    @staticmethod
    def _log_failure(future) -> None:
        if not future.cancelled() and future.exception() is not None:
            log.error("Engine command failed: %s", redact(future.exception()))

    async def _start(self, cfg: SessionConfig) -> None:
        if self._task is not None and not self._task.done():
            return
        register_secret(cfg.api_key)
        self._task = asyncio.create_task(self._run(cfg))

    async def _stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _restart(self, cfg: SessionConfig) -> None:
        await self._stop()
        await self._start(cfg)

    async def _set_muted(self, muted: bool) -> None:
        self._muted = muted
        if muted and self._ws is not None:
            # Discard any half-spoken turn so muting doesn't trigger a reply.
            await self._send({"type": "input_audio_buffer.clear"}, quiet=True)
            for turn in self.history.unfinished("user"):
                if not turn.text:
                    self.history.remove(turn.item_id)
                    self._listener.on_user_turn(turn.item_id, "", "empty")

    async def _new_conversation(self) -> None:
        self.history.clear()
        if self._speaker is not None:
            self._speaker.interrupt()
        if self._cfg is not None and self._cfg.listen_mode == "wake":
            self._awake = False  # applied when the fresh session starts
        self._listener.on_history_cleared()
        if self._session_tasks:
            # Reconnect to get a fresh server-side conversation.
            self._reset_requested = True
            for task in self._session_tasks:
                task.cancel()

    # ------------------------------------------------------------------ connection lifecycle

    async def _run(self, cfg: SessionConfig) -> None:
        self._cfg = cfg
        self._awake = cfg.listen_mode != "wake"
        self._listener.on_connection_state(CONNECTING)
        try:
            self._open_audio(cfg)
            failures = 0
            while True:
                try:
                    async with connect(
                        cfg.endpoint,
                        additional_headers={"Authorization": f"Bearer {cfg.api_key}"},
                        max_size=None,
                        open_timeout=15,
                        ping_interval=20,
                        ping_timeout=20,
                        close_timeout=2,
                    ) as ws:
                        failures = 0
                        await self._session(ws, cfg)
                    # Session reset (New conversation): reconnect right away.
                    self._listener.on_connection_state(CONNECTING)
                    continue
                except InvalidStatus as exc:
                    message, retryable = describe_http_status(exc.response.status_code, cfg.model)
                    if not retryable:
                        raise FatalSessionError(message) from None
                    reason = message
                except FatalSessionError:
                    raise
                except (ConnectionClosed, InvalidHandshake, OSError, asyncio.TimeoutError) as exc:
                    reason = self._describe_network_error(exc)
                log.warning("Connection problem: %s", redact(reason))
                failures += 1
                if failures > len(RECONNECT_DELAYS):
                    raise FatalSessionError(f"{reason} Gave up after several attempts.")
                self._end_session()
                self._listener.on_connection_state(RECONNECTING)
                self._listener.on_notice(f"{reason} Reconnecting…")
                await asyncio.sleep(RECONNECT_DELAYS[failures - 1])
        except asyncio.CancelledError:
            pass
        except AudioError as exc:
            self._listener.on_error(redact(exc))
        except FatalSessionError as exc:
            self._listener.on_error(redact(exc))
        except Exception as exc:
            log.exception("Unexpected engine error")
            self._listener.on_error(f"Something went wrong: {redact(exc)}")
        finally:
            self._end_session()
            self._close_audio()
            self._listener.on_connection_state(DISCONNECTED)

    @staticmethod
    def _describe_network_error(exc: Exception) -> str:
        if isinstance(exc, ConnectionClosed):
            return "The connection to OpenAI dropped."
        if isinstance(exc, asyncio.TimeoutError):
            return "OpenAI didn't respond in time."
        return "Can't reach OpenAI. Check your internet connection."

    def _open_audio(self, cfg: SessionConfig) -> None:
        loop = self._loop
        mic, speaker = self._audio_factory(
            cfg,
            lambda chunk: loop.call_soon_threadsafe(self._on_mic_chunk, chunk),
            lambda playing: loop.call_soon_threadsafe(self._on_playing_changed, playing),
            lambda message: loop.call_soon_threadsafe(self._on_audio_failure, message),
        )
        self._mic, self._speaker = mic, speaker
        self._mic_frames = 0
        self._mic_heard_signal = False
        self._mic_warning_sent = False
        self._stats_frames = 0
        self._stats_peak = 0
        self._stats_chunks = 0
        self._stats_clipped = 0
        self._clip_warning_sent = False
        speaker.start()
        mic.start()

    def _close_audio(self) -> None:
        mic, speaker = self._mic, self._speaker
        self._mic = self._speaker = None
        self._input_level = 0.0
        for device in (mic, speaker):
            if device is not None:
                device.close()

    def _on_audio_failure(self, message: str) -> None:
        self._listener.on_error(message)
        if self._task is not None:
            self._task.cancel()

    async def _session(self, ws, cfg: SessionConfig) -> None:
        self._reset_session_state()
        self._ws = ws
        self._mic_queue = asyncio.Queue(maxsize=100)
        await self._send(self._session_update(cfg, self._responds()))
        for item in self.history.replay_items():
            await self._send({"type": "conversation.item.create", "item": item}, quiet=True)
        self._listener.on_connection_state(CONNECTED)
        if cfg.listen_mode == "wake":
            self._listener.on_wake_state(self._awake)
        self._touch()

        receiver = asyncio.create_task(self._receive_loop(ws))
        sender = asyncio.create_task(self._send_mic_loop(ws))
        self._session_tasks = (receiver, sender)
        if cfg.listen_mode == "wake":
            self._session_tasks += (asyncio.create_task(self._idle_watch()),)
        try:
            done, _ = await asyncio.wait(self._session_tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in self._session_tasks:
                task.cancel()
            await asyncio.gather(*self._session_tasks, return_exceptions=True)
            self._session_tasks = ()
            self._end_session()
        if self._reset_requested:
            self._reset_requested = False
            return
        for task in done:
            if not task.cancelled() and task.exception() is not None:
                raise task.exception()
        raise ConnectionClosed(None, None)

    def _end_session(self) -> None:
        self._ws = None
        self._mic_queue = None
        if self._speaker is not None:
            self._speaker.interrupt()
        self._finish_open_turns()
        self._drop_pending_user_turns()
        if self._speaker is not None:
            self._speaker.forget()
        self._reset_session_state()

    def _reset_session_state(self) -> None:
        self._active_response: str | None = None
        self._dropped_responses: set[str] = set()
        self._interrupted: set[str] = set()
        self._received_ms: dict[str, float] = {}
        self._audio_done: set[str] = set()
        self._assistant_text: dict[str, str] = {}
        self._response_items: dict[str, list[str]] = {}
        self._sleep_items: set[str] = set()  # turns heard while waiting for the wake word
        self._user_speaking = False

    def _finish_open_turns(self) -> None:
        """Settles assistant bubbles that were mid-stream when a session ended."""
        for item_id in list(self._assistant_text):
            turn = self.history.get(item_id)
            if turn is None or turn.final:
                continue
            heard = self._speaker.heard_ms(item_id) if self._speaker else 0.0
            self._mark_interrupted(item_id, heard)

    def _drop_pending_user_turns(self) -> None:
        """Removes "listening…" bubbles whose transcript will never arrive."""
        for turn in self.history.unfinished("user"):
            self.history.remove(turn.item_id)
            self._listener.on_user_turn(turn.item_id, "", "empty")

    # ------------------------------------------------------------------ wake word

    def _responds(self) -> bool:
        """Whether the server should answer the user's turns on its own right now."""
        return self._cfg is None or self._cfg.listen_mode != "wake" or self._awake

    def _touch(self) -> None:
        self._last_activity = self._loop.time()

    async def _set_awake(self, awake: bool) -> None:
        cfg = self._cfg
        if cfg is None or cfg.listen_mode != "wake" or awake == self._awake:
            return
        self._awake = awake
        self._touch()
        log.info("Wake word mode: %s", "awake" if awake else "waiting for the wake word")
        if self._ws is not None:
            await self._send(
                {
                    "type": "session.update",
                    "session": {"type": "realtime", "audio": {"input": {"turn_detection": _turn_detection(awake)}}},
                },
                quiet=True,
            )
            self._listener.on_wake_state(awake)

    async def _idle_watch(self) -> None:
        while True:
            await asyncio.sleep(1)
            playing = self._speaker is not None and self._speaker.buffer is not None and self._speaker.buffer.playing
            if playing or self._user_speaking or self._active_response is not None:
                self._touch()
            elif self._awake and self._loop.time() - self._last_activity >= WAKE_IDLE_SECONDS:
                await self._set_awake(False)

    @staticmethod
    def _session_update(cfg: SessionConfig, responds: bool = True) -> dict:
        transcription = {"model": cfg.transcribe_model, "prompt": TRANSCRIBE_PROMPT}
        if cfg.transcribe_language:
            transcription["language"] = cfg.transcribe_language
        return {
            "type": "session.update",
            "session": {
                "type": "realtime",
                "model": cfg.model,
                "instructions": PERSONALITY,
                "output_modalities": ["audio"],
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                        "transcription": transcription,
                        "noise_reduction": {"type": cfg.mic_mode},
                        "turn_detection": _turn_detection(responds),
                    },
                    "output": {
                        "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                        "voice": cfg.voice,
                    },
                },
            },
        }

    async def _send(self, event: dict, quiet: bool = False) -> None:
        ws = self._ws
        if ws is None:
            return
        event.setdefault("event_id", f"{'quiet' if quiet else 'evt'}_{next(self._event_ids)}")
        try:
            await ws.send(json.dumps(event))
        except ConnectionClosed:
            if not quiet:
                raise

    # ------------------------------------------------------------------ microphone

    def _on_mic_chunk(self, chunk: bytes) -> None:
        samples = np.frombuffer(chunk, dtype=np.int16)
        peak = int(np.abs(samples.astype(np.int32)).max()) if samples.size else 0
        self._check_mic_signal(peak, samples.size)
        self._input_level = 0.0 if self._muted else voice_level(samples)
        queue = self._mic_queue
        if queue is None or self._muted:
            return
        if queue.full():
            queue.get_nowait()  # fall behind gracefully instead of growing latency
        queue.put_nowait(chunk)

    def _check_mic_signal(self, peak: int, frames: int) -> None:
        """Logs mic loudness periodically and warns once if the mic stays (nearly) silent."""
        if self._muted:
            return
        self._stats_peak = max(self._stats_peak, peak)
        self._stats_frames += frames
        self._stats_chunks += 1
        self._stats_clipped += peak >= CLIP_PEAK
        if self._stats_frames >= SAMPLE_RATE * MIC_STATS_SECONDS:
            clipped = self._stats_clipped / self._stats_chunks
            log.info(
                "Microphone peak over the last %ds: %.0f dBFS (%.0f%% of blocks clipped)",
                MIC_STATS_SECONDS, _dbfs(self._stats_peak), clipped * 100,
            )
            if clipped >= CLIP_WARNING_RATIO and not self._clip_warning_sent:
                self._clip_warning_sent = True
                self._listener.on_notice(
                    "Your microphone is too loud and distorting, which can make Naretta mishear you. "
                    "Lower its level in Windows Sound settings \u203a Input (try about 60\u201370%)."
                )
            self._stats_frames = self._stats_peak = self._stats_chunks = self._stats_clipped = 0

        if self._mic_heard_signal or self._mic_warning_sent:
            return
        if peak >= SOUND_PEAK:
            self._mic_heard_signal = True
            return
        self._mic_frames += frames
        if self._mic_frames >= SAMPLE_RATE * QUIET_MIC_WARNING_SECONDS:
            self._mic_warning_sent = True
            self._listener.on_notice(
                "Naretta can barely hear your microphone. If you're talking, check the laptop's "
                "mic-mute key, the input level in Windows Sound settings, and the microphone "
                "chosen in Settings (Settings \u203a Test microphone shows a live meter)."
            )

    async def _send_mic_loop(self, ws) -> None:
        queue = self._mic_queue
        while True:
            chunk = await queue.get()
            if self._muted:
                continue
            await ws.send(
                json.dumps(
                    {"type": "input_audio_buffer.append", "audio": base64.b64encode(chunk).decode()}
                )
            )

    # ------------------------------------------------------------------ playback

    def _on_playing_changed(self, playing: bool) -> None:
        self._touch()
        self._listener.on_speaking(playing)

    # ------------------------------------------------------------------ server events

    async def _receive_loop(self, ws) -> None:
        async for message in ws:
            if isinstance(message, bytes):
                continue
            try:
                event = json.loads(message)
            except ValueError:
                log.warning("Ignoring a malformed server message")
                continue
            await self._handle_event(event)

    async def _handle_event(self, event: dict) -> None:
        kind = event.get("type", "")
        handler = self._HANDLERS.get(kind)
        if handler is not None:
            await handler(self, event)
        elif kind not in ("rate_limits.updated",):
            log.debug("Server event %s", kind)

    async def _on_session_event(self, event: dict) -> None:
        log.info("Server: %s", event["type"])

    async def _on_speech_started(self, event: dict) -> None:
        log.info("Speech detected")
        self._user_speaking = True
        self._touch()
        await self._barge_in()
        item_id = event.get("item_id")
        if item_id and not self._responds():
            self._sleep_items.add(item_id)  # shown only if it contains the wake word
        elif item_id:
            self.history.upsert(item_id, "user")
            self._listener.on_user_turn(item_id, "", "pending")

    async def _barge_in(self) -> None:
        """The user started talking: silence Naretta now and tell the server what was heard."""
        if self._speaker is not None:
            self._speaker.interrupt()
        if self._active_response:
            self._dropped_responses.add(self._active_response)
        finished = set()
        for item_id, received in list(self._received_ms.items()):
            if item_id in self._interrupted:
                continue
            played = self._speaker.played_ms(item_id) if self._speaker else 0.0
            if item_id in self._audio_done and played >= received - 1:
                finished.add(item_id)  # heard in full; nothing to truncate
                continue
            heard = self._speaker.heard_ms(item_id) if self._speaker else 0.0
            audio_end_ms = int(min(heard, received))
            await self._send(
                {
                    "type": "conversation.item.truncate",
                    "item_id": item_id,
                    "content_index": 0,
                    "audio_end_ms": audio_end_ms,
                },
                quiet=True,
            )
            self._mark_interrupted(item_id, heard)
        # Only assistant items that are still being played matter for future barge-ins.
        for item_id in finished:
            self._received_ms.pop(item_id, None)

    def _mark_interrupted(self, item_id: str, heard_ms: float) -> None:
        self._interrupted.add(item_id)
        received = self._received_ms.get(item_id, 0.0)
        text = estimate_heard_text(self._assistant_text.get(item_id, ""), heard_ms, received)
        if text:
            self.history.upsert(item_id, "assistant", text=text, final=True, interrupted=True)
            self._listener.on_assistant_turn(item_id, text, "interrupted")
        else:
            self.history.remove(item_id)
            self._listener.on_assistant_turn(item_id, "", "empty")

    async def _on_user_transcript_delta(self, event: dict) -> None:
        item_id = event.get("item_id")
        turn = self.history.get(item_id) if item_id else None
        if turn is None or turn.final:
            return
        turn = self.history.upsert(item_id, "user", text=turn.text + event.get("delta", ""))
        self._listener.on_user_turn(item_id, turn.text, "partial")

    async def _on_user_transcript_done(self, event: dict) -> None:
        item_id = event.get("item_id")
        if not item_id:
            return
        text = (event.get("transcript") or "").strip()
        if item_id in self._sleep_items:
            await self._on_sleep_transcript(item_id, text)
            return
        if text:
            self.history.upsert(item_id, "user", text=text, final=True)
            self._listener.on_user_turn(item_id, text, "final")
        else:
            self.history.remove(item_id)
            self._listener.on_user_turn(item_id, "", "empty")

    async def _on_sleep_transcript(self, item_id: str, text: str) -> None:
        """A turn heard while waiting: wake up on the wake word, otherwise forget it."""
        self._sleep_items.discard(item_id)
        if text and contains_wake_word(text):
            self.history.upsert(item_id, "user", text=text, final=True)
            self._listener.on_user_turn(item_id, text, "final")
            await self._set_awake(True)
            await self._send({"type": "response.create"})
        else:
            await self._send({"type": "conversation.item.delete", "item_id": item_id}, quiet=True)

    async def _on_user_transcript_failed(self, event: dict) -> None:
        item_id = event.get("item_id")
        if item_id in self._sleep_items:
            self._sleep_items.discard(item_id)
            await self._send({"type": "conversation.item.delete", "item_id": item_id}, quiet=True)
            return
        if item_id:
            self.history.remove(item_id)
            self._listener.on_user_turn(item_id, "", "failed")

    async def _on_response_created(self, event: dict) -> None:
        response_id = (event.get("response") or {}).get("id")
        self._active_response = response_id
        if response_id:
            self._response_items[response_id] = []

    async def _on_output_item_added(self, event: dict) -> None:
        response_id = event.get("response_id")
        item = event.get("item") or {}
        if response_id in self._dropped_responses:
            return
        if item.get("type") == "message" and item.get("role") == "assistant" and item.get("id"):
            self._response_items.setdefault(response_id, []).append(item["id"])
            self._assistant_text.setdefault(item["id"], "")
            self.history.upsert(item["id"], "assistant")

    def _accepts(self, event: dict) -> bool:
        return (
            event.get("response_id") not in self._dropped_responses
            and event.get("item_id") not in self._interrupted
        )

    async def _on_audio_delta(self, event: dict) -> None:
        item_id = event.get("item_id")
        if not item_id or not self._accepts(event):
            return
        pcm = base64.b64decode(event.get("delta", ""))
        self._received_ms[item_id] = self._received_ms.get(item_id, 0.0) + len(pcm) / 2 / SAMPLE_RATE * 1000
        if self._speaker is not None:
            self._speaker.enqueue(item_id, pcm)

    async def _on_audio_done(self, event: dict) -> None:
        if event.get("item_id"):
            self._audio_done.add(event["item_id"])

    async def _on_audio_transcript_delta(self, event: dict) -> None:
        item_id = event.get("item_id")
        if not item_id or not self._accepts(event):
            return
        text = self._assistant_text.get(item_id, "") + event.get("delta", "")
        self._assistant_text[item_id] = text
        self.history.upsert(item_id, "assistant", text=text)
        self._listener.on_assistant_turn(item_id, text, "partial")

    async def _on_audio_transcript_done(self, event: dict) -> None:
        item_id = event.get("item_id")
        if not item_id or not self._accepts(event):
            return
        text = (event.get("transcript") or self._assistant_text.get(item_id, "")).strip()
        self._assistant_text[item_id] = text
        self.history.upsert(item_id, "assistant", text=text, final=True)
        self._listener.on_assistant_turn(item_id, text, "final")

    async def _on_speech_stopped(self, event: dict) -> None:
        log.info("Speech ended")
        self._user_speaking = False
        self._touch()

    async def _on_response_done(self, event: dict) -> None:
        response = event.get("response") or {}
        log.info("Reply %s", response.get("status"))
        self._touch()
        response_id = response.get("id")
        if response_id == self._active_response:
            self._active_response = None
        status = response.get("status")
        if status == "failed":
            error = (response.get("status_details") or {}).get("error") or {}
            message = error.get("message") or "Naretta couldn't finish that reply."
            self._listener.on_notice(redact(message))
        # Settle any bubble that never got a final transcript (e.g. cut off before audio arrived).
        for item_id in self._response_items.pop(response_id, []):
            turn = self.history.get(item_id)
            if turn is not None and not turn.final and item_id not in self._interrupted:
                if turn.text:
                    self.history.upsert(item_id, "assistant", final=True)
                    self._listener.on_assistant_turn(item_id, turn.text, "final")
                else:
                    self.history.remove(item_id)
                    self._listener.on_assistant_turn(item_id, "", "empty")
        self._dropped_responses.discard(response_id)

    async def _on_error(self, event: dict) -> None:
        error = event.get("error") or {}
        code = error.get("code") or ""
        message = redact(error.get("message") or "Unknown error")
        log.warning("Server error %s: %s", code, message)
        if code == "invalid_api_key":
            raise FatalSessionError("OpenAI rejected the API key. Open Settings and paste a valid key.")
        if code == "session_expired":
            raise ConnectionClosed(None, None)
        if str(error.get("event_id") or "").startswith("quiet_"):
            return  # e.g. truncating audio that already finished; harmless
        self._listener.on_notice(f"OpenAI reported a problem: {message}")

    _HANDLERS = {
        "session.created": _on_session_event,
        "session.updated": _on_session_event,
        "input_audio_buffer.speech_started": _on_speech_started,
        "input_audio_buffer.speech_stopped": _on_speech_stopped,
        "conversation.item.input_audio_transcription.delta": _on_user_transcript_delta,
        "conversation.item.input_audio_transcription.completed": _on_user_transcript_done,
        "conversation.item.input_audio_transcription.failed": _on_user_transcript_failed,
        "response.created": _on_response_created,
        "response.output_item.added": _on_output_item_added,
        "response.output_audio.delta": _on_audio_delta,
        "response.output_audio.done": _on_audio_done,
        "response.output_audio_transcript.delta": _on_audio_transcript_delta,
        "response.output_audio_transcript.done": _on_audio_transcript_done,
        "response.done": _on_response_done,
        "error": _on_error,
    }
