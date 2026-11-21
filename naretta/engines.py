"""Picks the OpenAI or the Sarvam engine for each conversation, behind one interface."""

from __future__ import annotations

from .config import SessionConfig
from .history import ConversationHistory
from .realtime import RealtimeEngine
from .sarvam import SarvamEngine

ENGINES = {"openai": RealtimeEngine, "sarvam": SarvamEngine}


class _ForActive:
    """Passes an engine's callbacks on only while it's the active one.

    Switching services stops the old engine in the background; without this its late
    "disconnected" would land after the new engine's "connecting".
    """

    def __init__(self, owner: "ProviderEngine", provider: str):
        self._owner = owner
        self._provider = provider

    def __getattr__(self, name):
        target = getattr(self._owner.listener, name)

        def forward(*args):
            if self._owner.provider == self._provider:
                target(*args)

        return forward


class ProviderEngine:
    """Same public API as RealtimeEngine; the conversation history is shared between services."""

    def __init__(self, listener, engines=ENGINES, **engine_kwargs):
        self.listener = listener
        self.history = ConversationHistory()
        self.provider = "openai"
        self._engines = {
            name: cls(_ForActive(self, name), history=self.history, **engine_kwargs) for name, cls in engines.items()
        }

    @property
    def active(self) -> RealtimeEngine:
        return self._engines[self.provider]

    def _switch(self, cfg: SessionConfig) -> bool:
        """Makes cfg's service the active one; True if that meant switching."""
        if cfg.provider == self.provider or cfg.provider not in self._engines:
            return False
        self.active.stop()
        self.provider = cfg.provider
        return True

    def start(self, cfg: SessionConfig) -> None:
        self._switch(cfg)
        self.active.start(cfg)

    def restart(self, cfg: SessionConfig) -> None:
        if self._switch(cfg):
            self.active.start(cfg)
        else:
            self.active.restart(cfg)

    def stop(self) -> None:
        self.active.stop()

    def set_muted(self, muted: bool) -> None:
        for engine in self._engines.values():
            engine.set_muted(muted)

    def new_conversation(self) -> None:
        self.active.new_conversation()

    def wake(self) -> None:
        self.active.wake()

    def levels(self) -> tuple[float, float]:
        return self.active.levels()

    def shutdown(self, timeout: float = 5.0) -> None:
        for engine in self._engines.values():
            engine.shutdown(timeout)
