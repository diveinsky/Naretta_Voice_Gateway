"""Static configuration: models, audio format, voices and the personality prompt."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

APP_NAME = "Naretta"

REALTIME_URL = "wss://api.openai.com/v1/realtime"
DEFAULT_MODEL = "gpt-realtime-2.1"
# Used only to show the user's words in the chat panel (Naretta herself listens to the audio).
DEFAULT_TRANSCRIBE_MODEL = "gpt-4o-transcribe"
# Context for the transcriber; short clips are otherwise easy to mistake for another language.
TRANSCRIBE_PROMPT = "A casual spoken conversation with a friendly voice assistant named Naretta."

# The Realtime API's PCM format: 16-bit signed little-endian, mono, 24 kHz.
SAMPLE_RATE = 24000

VOICES = ["marin", "cedar", "alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse"]
DEFAULT_VOICE = "marin"

# Maps to session.audio.input.noise_reduction.type.
MIC_MODES = {
    "near_field": "Headphones or headset microphone",
    "far_field": "Speakers with laptop or desk microphone",
}
DEFAULT_MIC_MODE = "near_field"

ANIMATION_STYLES = {"orb": "Orb", "rings": "Rings", "bars": "Bars"}
DEFAULT_ANIMATION_STYLE = "orb"

# Naretta's voice arrives well below full scale; these boost it, with a soft limiter.
VOICE_VOLUMES = {1.0: "100% (as sent by OpenAI)", 2.0: "200%", 3.0: "300% (recommended)", 4.0: "400%"}
DEFAULT_VOICE_VOLUME = 3.0

LISTEN_MODES = {"always": "Always listening", "wake": "Wake word: \u201cNaretta\u201d"}
DEFAULT_LISTEN_MODE = "always"

THEMES = {"dark": "Dark", "light": "White"}
DEFAULT_THEME = "dark"

# ISO-639-1 codes for the transcriber; "" lets it detect the language from each clip.
TRANSCRIBE_LANGUAGES = {
    "": "Detect automatically",
    "en": "English",
    "hi": "Hindi",
    "ta": "Tamil",
    "te": "Telugu",
    "kn": "Kannada",
    "ml": "Malayalam",
    "mr": "Marathi",
    "bn": "Bengali",
    "gu": "Gujarati",
    "pa": "Punjabi",
    "ur": "Urdu",
    "ar": "Arabic",
    "zh": "Chinese",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "pt": "Portuguese",
    "ru": "Russian",
    "es": "Spanish",
}

PERSONALITY = (
    "You are Naretta, pronounced nah-RET-tah. Introduce yourself once per new conversation "
    "with “Hi, I’m Naretta.” Speak like a friendly conversation partner. Match the "
    "user’s language, keep everyday replies short, and ask a follow-up when it feels natural."
)


@dataclass(frozen=True)
class SessionConfig:
    api_key: str = field(repr=False)
    voice: str = DEFAULT_VOICE
    input_device: str | None = None  # device name; None = system default
    output_device: str | None = None
    mic_mode: str = DEFAULT_MIC_MODE
    model: str = DEFAULT_MODEL
    transcribe_model: str = DEFAULT_TRANSCRIBE_MODEL
    transcribe_language: str = ""  # "" = detect automatically
    listen_mode: str = "always"  # "always" | "wake"
    echo_cancellation: bool = True  # WebRTC echo cancellation and noise suppression
    voice_volume: float = 3.0  # playback gain for Naretta's voice (OpenAI's audio peaks around -12 dBFS)
    url: str = REALTIME_URL

    @property
    def endpoint(self) -> str:
        return f"{self.url}?model={self.model}"


def model_from_env() -> str:
    return os.environ.get("NARETTA_MODEL", "").strip() or DEFAULT_MODEL


def transcribe_model_from_env() -> str:
    return os.environ.get("NARETTA_TRANSCRIBE_MODEL", "").strip() or DEFAULT_TRANSCRIBE_MODEL
