"""Keeps API keys out of logs and user-facing error messages."""

from __future__ import annotations

import logging
import re
import threading

_KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_\-]{6,}")
_BEARER_PATTERN = re.compile(r"(Bearer\s+)\S+", re.IGNORECASE)

_lock = threading.Lock()
_secrets: set[str] = set()


def register_secret(secret: str | None) -> None:
    if secret and len(secret) >= 8:
        with _lock:
            _secrets.add(secret)


def redact(text: object) -> str:
    text = str(text)
    with _lock:
        secrets = list(_secrets)
    for secret in secrets:
        text = text.replace(secret, "[redacted]")
    text = _BEARER_PATTERN.sub(r"\1[redacted]", text)
    return _KEY_PATTERN.sub("sk-[redacted]", text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if record.exc_info:
            message += "\n" + logging.Formatter().formatException(record.exc_info)
            record.exc_info = None
            record.exc_text = None
        record.msg = redact(message)
        record.args = None
        return True
