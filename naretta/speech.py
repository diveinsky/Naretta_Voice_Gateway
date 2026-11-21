"""Speaks short messages with Windows' built-in voice.

Used for errors, when OpenAI can't speak for Naretta (no credit, no internet, rejected key).
It needs no network and no API credit.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
import threading

log = logging.getLogger(__name__)

# The text arrives on stdin, so nothing in it is ever run as a command.
_SCRIPT = (
    "[Console]::InputEncoding = [Text.Encoding]::UTF8; "
    "Add-Type -AssemblyName System.Speech; "
    "$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    "$voice.Speak([Console]::In.ReadToEnd())"
)


def first_sentence(text: str) -> str:
    """The headline of a message ("Your OpenAI credit has run out."), short enough to say aloud."""
    match = re.match(r"(.+?[.!?])(\s|$)", text.strip())
    return match.group(1) if match else text.strip()


def speak_locally(text: str) -> None:
    """Says `text` in the background; does nothing outside Windows."""
    if sys.platform != "win32" or not text.strip():
        return
    threading.Thread(target=_speak, args=(text,), name="naretta-local-voice", daemon=True).start()


def _speak(text: str) -> None:
    try:
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT],
            input=text,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("Couldn't speak the message with the Windows voice: %s", exc)
