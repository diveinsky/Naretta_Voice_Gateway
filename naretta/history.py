"""Local copy of the conversation, so it survives Stop/Start and dropped connections."""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass
class Turn:
    item_id: str
    role: str  # "user" | "assistant"
    text: str = ""
    final: bool = False
    interrupted: bool = False


class ConversationHistory:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._turns: list[Turn] = []

    def upsert(self, item_id: str, role: str, **fields) -> Turn:
        with self._lock:
            for turn in self._turns:
                if turn.item_id == item_id:
                    break
            else:
                turn = Turn(item_id, role)
                self._turns.append(turn)
            for name, value in fields.items():
                setattr(turn, name, value)
            return turn

    def get(self, item_id: str) -> Turn | None:
        with self._lock:
            return next((t for t in self._turns if t.item_id == item_id), None)

    def unfinished(self, role: str) -> list[Turn]:
        with self._lock:
            return [t for t in self._turns if t.role == role and not t.final]

    def remove(self, item_id: str) -> None:
        with self._lock:
            self._turns = [t for t in self._turns if t.item_id != item_id]

    def clear(self) -> None:
        with self._lock:
            self._turns.clear()

    def is_empty(self) -> bool:
        with self._lock:
            return not any(t.text for t in self._turns)

    def replay_items(self) -> list[dict]:
        """Conversation items that recreate this history in a fresh Realtime session."""
        with self._lock:
            turns = [t for t in self._turns if t.text.strip()]
        items = []
        for turn in turns:
            content_type = "input_text" if turn.role == "user" else "output_text"
            items.append(
                {
                    "type": "message",
                    "role": turn.role,
                    "content": [{"type": content_type, "text": turn.text}],
                }
            )
        return items


def estimate_heard_text(text: str, heard_ms: float, received_ms: float) -> str:
    """Trims a reply's transcript to roughly the part that was played before an interruption.

    The Realtime API truncates the audio but not the transcript, so this is a proportional
    estimate cut back to a word boundary.
    """
    text = text.strip()
    if not text or heard_ms <= 0 or received_ms <= 0:
        return ""
    fraction = heard_ms / received_ms
    if fraction >= 0.97:
        return text
    cut = int(len(text) * fraction)
    space = text.rfind(" ", 0, cut + 1)
    if space <= 0:
        return ""
    return text[:space].rstrip(" ,;:") + "…"
