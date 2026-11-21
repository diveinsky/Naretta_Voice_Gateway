"""A record of requests Naretta couldn't handle, kept for adding tools or training later.

One JSON object per line, so the file can be read with any tool or loaded as a dataset.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from .config import data_dir
from .redact import redact

log = logging.getLogger(__name__)

FILE_NAME = "missing_capabilities.jsonl"


def default_path() -> Path:
    return data_dir() / FILE_NAME


class CapabilityLog:
    def __init__(self, path: Path | None = None):
        self.path = path or default_path()

    def record(self, request: str, capability: str, reason: str = "", reply: str = "") -> bool:
        entry = {
            "time": datetime.now().astimezone().isoformat(timespec="seconds"),
            "capability": redact(capability).strip(),
            "request": redact(request).strip(),
            "reason": redact(reason).strip(),
            "reply": redact(reply).strip(),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("Couldn't save a missing-capability record: %s", exc)
            return False
        return True

    def entries(self) -> list[dict]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        entries = []
        for line in lines:
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue  # a line cut short by a crash; skip it
        return entries
