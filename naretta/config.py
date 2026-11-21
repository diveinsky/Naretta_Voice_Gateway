"""Static configuration: models, audio format, voices and the personality prompt."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "Naretta"

REALTIME_URL = "wss://api.openai.com/v1/realtime"
DEFAULT_MODEL = "gpt-realtime-2.1"
# Used only to show the user's words in the chat panel (Naretta herself listens to the audio).
DEFAULT_TRANSCRIBE_MODEL = "gpt-4o-transcribe"
# Context for the transcriber; short clips are otherwise easy to mistake for another language.
TRANSCRIBE_PROMPT = "A casual spoken conversation with a friendly voice assistant named Naretta."

# The Realtime API's PCM format: 16-bit signed little-endian, mono, 24 kHz.
SAMPLE_RATE = 24000

# Which service listens, thinks and speaks for Naretta.
PROVIDERS = {"openai": "OpenAI (speech-to-speech)", "sarvam": "Sarvam AI (Indian languages)"}
DEFAULT_PROVIDER = "openai"

# Sarvam has no speech-to-speech model, so Naretta chains its speech-to-text, chat and
# text-to-speech services.
SARVAM_API_URL = "https://api.sarvam.ai"
SARVAM_WS_URL = "wss://api.sarvam.ai"
SARVAM_STT_MODEL = "saaras:v4"
SARVAM_STT_RATE = 16000  # the realtime speech-to-text accepts only 8 or 16 kHz
SARVAM_CHAT_MODEL = "sarvam-105b-conversations"  # tuned for voice; doesn't spend time reasoning
SARVAM_TTS_MODEL = "bulbul:v4-flash"
# Bulbul v4 voices; any of them can speak every supported language.
SARVAM_VOICES = {
    "simran_en_conversation": "Simran",
    "sophia_en_conversational": "Sophia",
    "amelia_en_conversational": "Amelia",
    "roopa_en_conversational": "Roopa",
    "ishita_enhi_companion": "Ishita (Hinglish)",
    "dev_en_conversational": "Dev",
    "deven_en_conversation": "Deven",
    "shubh_enhi_companion": "Shubh (Hinglish)",
    "rahul_hi_conversational": "Rahul (Hindi)",
}
DEFAULT_SARVAM_VOICE = "simran_en_conversation"
# Naretta's language codes that Sarvam supports, as Sarvam spells them.
SARVAM_LANGUAGES = {
    "en": "en-IN", "hi": "hi-IN", "ta": "ta-IN", "te": "te-IN", "kn": "kn-IN", "ml": "ml-IN",
    "mr": "mr-IN", "bn": "bn-IN", "gu": "gu-IN", "pa": "pa-IN", "ur": "ur-IN",
}
SARVAM_SPOKEN_LANGUAGES = set(SARVAM_LANGUAGES.values()) - {"ur-IN"}  # Bulbul can't speak Urdu

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


# Naretta has no tools yet. When she can't do something, she records the request through this
# function so it can become a tool or agent (or training data) later.
CAPABILITY_TOOL = {
    "type": "function",
    "name": "log_missing_capability",
    "description": (
        "Record a request you can't fulfil yet, such as live information, the current date or "
        "time, actions on the user's computer, files, accounts, timers, music, singing or senses "
        "like sight and smell, so it can be added to Naretta later."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "request": {"type": "string", "description": "What the user asked for, briefly, in their words."},
            "capability": {
                "type": "string",
                "description": "The missing ability in a few words, e.g. 'web search', 'set a timer', 'open apps', 'sing'.",
            },
            "reason": {"type": "string", "description": "Why you can't do it right now."},
        },
        "required": ["request", "capability"],
    },
}

CAPABILITY_INSTRUCTIONS = (
    "You have no access to the internet, live information, the current date or time, the user's "
    "computer, files or accounts. You can't set timers, play music, sing, or remember anything after "
    "this conversation ends, and you have no senses beyond hearing the user: you can't see, smell, "
    "taste or touch. Whenever your honest answer is that you can't do or sense something the user "
    "asks about, first call log_missing_capability once for that request without saying anything, "
    "then briefly tell them you can't and offer an alternative. Don't mention the log unless the "
    "user asks."
)

END_CONVERSATION_TOOL = {
    "type": "function",
    "name": "end_conversation",
    "description": (
        "End the conversation when the user says they want to stop, are done, or say goodbye. "
        "Say a short goodbye first; Naretta stops listening once it has played."
    ),
    "parameters": {"type": "object", "properties": {}},
}

LISTENING_INSTRUCTIONS = (
    "You hear the user through a microphone, and the transcript can be wrong. If what you heard is "
    "garbled, cut off, or doesn't fit the conversation, don't guess: say you didn't hear it properly "
    "and ask them to repeat, or check with a short question like \u201cDid you say \u2026?\u201d. If it "
    "sounds like background talk not meant for you, such as a TV or someone else nearby, briefly ask "
    "whether they want to carry on with what you were talking about or stop. If the user clearly "
    "changes the topic, just go with it. When the user says they want to stop or are done, say a "
    "short goodbye and call end_conversation."
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
    provider: str = DEFAULT_PROVIDER
    sarvam_voice: str = DEFAULT_SARVAM_VOICE
    sarvam_api_url: str = SARVAM_API_URL
    sarvam_ws_url: str = SARVAM_WS_URL

    @property
    def endpoint(self) -> str:
        return f"{self.url}?model={self.model}"


def model_from_env() -> str:
    return os.environ.get("NARETTA_MODEL", "").strip() or DEFAULT_MODEL


def transcribe_model_from_env() -> str:
    return os.environ.get("NARETTA_TRANSCRIBE_MODEL", "").strip() or DEFAULT_TRANSCRIBE_MODEL


def data_dir() -> Path:
    """Naretta's folder for logs and records (%LOCALAPPDATA%\\Naretta on Windows)."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME
