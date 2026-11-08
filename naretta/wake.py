"""Spotting the wake word "Naretta" in transcripts, tolerating common mis-hearings."""

from __future__ import annotations

import re
import unicodedata

WAKE_WORD = "Naretta"
_TARGET = "naretta"
_ALIASES = {"naretta", "nareta", "narretta", "narreta", "neretta", "nauretta", "loretta", "narita", "nareeta"}
_WORD = re.compile(r"[^\W\d_]+")


def _distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def contains_wake_word(text: str) -> bool:
    plain = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(ch for ch in plain if not unicodedata.combining(ch))
    for word in _WORD.findall(plain):
        if word in _ALIASES:
            return True
        if 5 <= len(word) <= 9 and word[0] in "nl" and _distance(word, _TARGET) <= 2:
            return True
    return False
