"""Non-secret preferences, stored per user via QSettings (the registry on Windows)."""

from __future__ import annotations

from PySide6.QtCore import QLocale, QSettings

from .config import (
    ANIMATION_STYLES,
    APP_NAME,
    DEFAULT_ANIMATION_STYLE,
    DEFAULT_LISTEN_MODE,
    DEFAULT_VOICE_VOLUME,
    DEFAULT_THEME,
    DEFAULT_MIC_MODE,
    DEFAULT_VOICE,
    MIC_MODES,
    VOICES,
    LISTEN_MODES,
    THEMES,
    TRANSCRIBE_LANGUAGES,
    VOICE_VOLUMES,
    SessionConfig,
    model_from_env,
    transcribe_model_from_env,
)


class SettingsStore:
    def __init__(self, qsettings: QSettings | None = None):
        self._s = qsettings or QSettings(APP_NAME, APP_NAME)

    def _get(self, key: str) -> str | None:
        value = self._s.value(key)
        return str(value) if value else None

    @property
    def input_device(self) -> str | None:
        return self._get("audio/input_device")

    @input_device.setter
    def input_device(self, name: str | None) -> None:
        self._s.setValue("audio/input_device", name or "")

    @property
    def output_device(self) -> str | None:
        return self._get("audio/output_device")

    @output_device.setter
    def output_device(self, name: str | None) -> None:
        self._s.setValue("audio/output_device", name or "")

    @property
    def voice(self) -> str:
        voice = self._get("assistant/voice")
        return voice if voice in VOICES else DEFAULT_VOICE

    @voice.setter
    def voice(self, voice: str) -> None:
        self._s.setValue("assistant/voice", voice)

    @property
    def mic_mode(self) -> str:
        mode = self._get("audio/mic_mode")
        return mode if mode in MIC_MODES else DEFAULT_MIC_MODE

    @mic_mode.setter
    def mic_mode(self, mode: str) -> None:
        self._s.setValue("audio/mic_mode", mode)

    @property
    def animation_style(self) -> str:
        style = self._get("appearance/animation")
        return style if style in ANIMATION_STYLES else DEFAULT_ANIMATION_STYLE

    @animation_style.setter
    def animation_style(self, style: str) -> None:
        self._s.setValue("appearance/animation", style)

    @property
    def echo_cancellation(self) -> bool:
        value = self._s.value("audio/echo_cancellation")
        return True if value is None else str(value).lower() in ("true", "1")

    @echo_cancellation.setter
    def echo_cancellation(self, on: bool) -> None:
        self._s.setValue("audio/echo_cancellation", bool(on))

    @property
    def voice_volume(self) -> float:
        try:
            value = float(self._s.value("audio/voice_volume"))
        except (TypeError, ValueError):
            return DEFAULT_VOICE_VOLUME
        return value if value in VOICE_VOLUMES else DEFAULT_VOICE_VOLUME

    @voice_volume.setter
    def voice_volume(self, value: float) -> None:
        self._s.setValue("audio/voice_volume", float(value))

    @property
    def listen_mode(self) -> str:
        mode = self._get("assistant/listen_mode")
        return mode if mode in LISTEN_MODES else DEFAULT_LISTEN_MODE

    @listen_mode.setter
    def listen_mode(self, mode: str) -> None:
        self._s.setValue("assistant/listen_mode", mode)

    @property
    def theme(self) -> str:
        theme = self._get("appearance/theme")
        return theme if theme in THEMES else DEFAULT_THEME

    @theme.setter
    def theme(self, theme: str) -> None:
        self._s.setValue("appearance/theme", theme)

    @property
    def transcribe_language(self) -> str:
        value = self._s.value("assistant/transcribe_language")
        if value is None:
            # Until chosen, use the Windows display language when it's in the list.
            system = QLocale.system().name().split("_")[0]
            return system if system in TRANSCRIBE_LANGUAGES else ""
        value = str(value)
        return value if value in TRANSCRIBE_LANGUAGES else ""

    @transcribe_language.setter
    def transcribe_language(self, code: str) -> None:
        self._s.setValue("assistant/transcribe_language", code)

    def session_config(self, api_key: str) -> SessionConfig:
        return SessionConfig(
            api_key=api_key,
            voice=self.voice,
            input_device=self.input_device,
            output_device=self.output_device,
            mic_mode=self.mic_mode,
            model=model_from_env(),
            transcribe_model=transcribe_model_from_env(),
            transcribe_language=self.transcribe_language,
            listen_mode=self.listen_mode,
            echo_cancellation=self.echo_cancellation,
            voice_volume=self.voice_volume,
        )
