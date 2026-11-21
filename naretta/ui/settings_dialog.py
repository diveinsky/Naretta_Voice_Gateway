"""Settings: audio devices, voice service and voice, and the API keys."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import credentials
from ..audio import AudioError, Microphone, list_devices, voice_level
from ..config import (
    ANIMATION_STYLES,
    LISTEN_MODES,
    MIC_MODES,
    PROVIDERS,
    SARVAM_VOICES,
    THEMES,
    TRANSCRIBE_LANGUAGES,
    VOICE_VOLUMES,
    VOICES,
)
from ..settings import SettingsStore

SYSTEM_DEFAULT = "System default"
# The dialog never grows taller than this share of the screen; the rest scrolls.
MAX_SCREEN_SHARE = 0.85


class Section(QFrame):
    """A titled panel that folds away to one header line, showing a summary while folded."""

    def __init__(self, title: str, expanded: bool = False, on_toggled=None):
        super().__init__()
        self.setObjectName("section")
        self._title = title
        self._summary = ""
        self._on_toggled = on_toggled
        self.header = QPushButton()  # (a QToolButton ignores left-aligned text in style sheets)
        self.header.setObjectName("sectionHeader")
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.body = QWidget()
        self.body.setVisible(expanded)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(2)
        layout.addWidget(self.header)
        layout.addWidget(self.body)
        self.header.toggled.connect(self.set_expanded)
        self._update_header()

    def is_expanded(self) -> bool:
        return self.header.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        self.header.setChecked(expanded)
        self.body.setVisible(expanded)
        self.updateGeometry()  # Qt caches sizes while a window is hidden
        self._update_header()
        if self._on_toggled is not None:
            self._on_toggled()

    def set_summary(self, summary: str) -> None:
        self._summary = summary
        self._update_header()

    def _update_header(self) -> None:
        expanded = self.is_expanded()
        arrow = "\u25be" if expanded else "\u25b8"
        text = f"{arrow}  {self._title}"
        if self._summary and not expanded:
            text += f"   \u00b7   {self._summary}"
        self.header.setText(text)
        self.header.setToolTip("Hide" if expanded else "Show")


# provider: (group title, where to get a key)
KEY_HELP = {
    "openai": ("OpenAI API key", "platform.openai.com/api-keys"),
    "sarvam": ("Sarvam API key", "dashboard.sarvam.ai"),
}


class SettingsDialog(QDialog):
    def __init__(self, store: SettingsStore, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")  # Qt adds " - Naretta"
        self.setMinimumWidth(480)
        self._store = store

        self.audio_section = Section("Audio", on_toggled=self._fit_height)
        audio_form = QFormLayout(self.audio_section.body)
        audio_form.setContentsMargins(4, 6, 4, 6)
        self.mic_combo = QComboBox()
        self.speaker_combo = QComboBox()
        refresh = QPushButton("Refresh devices")
        refresh.clicked.connect(self._load_devices)
        self.mic_mode_combo = QComboBox()
        for mode, label in MIC_MODES.items():
            self.mic_mode_combo.addItem(label, mode)
        self.device_error = QLabel()
        self.device_error.setObjectName("errorText")
        self.device_error.setWordWrap(True)
        self.device_error.hide()
        audio_form.addRow("Microphone", self.mic_combo)
        audio_form.addRow("Speaker", self.speaker_combo)
        self.volume_combo = QComboBox()
        for value, label in VOICE_VOLUMES.items():
            self.volume_combo.addItem(label, value)
        self.volume_combo.setToolTip("Boosts Naretta's voice; loud peaks are softened instead of crackling.")
        audio_form.addRow("Voice volume", self.volume_combo)
        audio_form.addRow("Setup", self.mic_mode_combo)
        self.echo_check = QCheckBox("Echo cancellation and voice cleanup (recommended)")
        self.echo_check.setToolTip(
            "Removes Naretta's own voice picked up from the speakers, reduces background noise and "
            "keeps her from interrupting herself."
        )
        audio_form.addRow("", self.echo_check)
        audio_form.addRow("", refresh)
        self.mic_test_button = QPushButton("Test microphone")
        self.mic_test_button.setCheckable(True)
        self.mic_test_button.toggled.connect(self._toggle_mic_test)
        self.mic_meter = QProgressBar()
        self.mic_meter.setRange(0, 100)
        self.mic_meter.setTextVisible(False)
        self.mic_meter.setFixedHeight(10)
        self.mic_meter_label = QLabel("Click and speak \u2014 the bar should move with your voice.")
        self.mic_meter_label.setObjectName("mutedText")
        self.mic_meter_label.setWordWrap(True)
        test_row = QHBoxLayout()
        test_row.addWidget(self.mic_test_button)
        test_row.addWidget(self.mic_meter, 1)
        audio_form.addRow("", test_row)
        audio_form.addRow("", self.mic_meter_label)
        self._test_mic: Microphone | None = None
        self._test_level = 0.0
        self._test_peak = 0
        self._test_timer = QTimer(self)
        self._test_timer.setInterval(50)
        self._test_timer.timeout.connect(self._update_meter)
        audio_form.addRow(self.device_error)

        self.assistant_section = Section("Assistant", expanded=True, on_toggled=self._fit_height)
        voice_form = QFormLayout(self.assistant_section.body)
        voice_form.setContentsMargins(4, 6, 4, 6)
        self._voice_form = voice_form
        self.provider_combo = QComboBox()
        for provider, label in PROVIDERS.items():
            self.provider_combo.addItem(label, provider)
        self.provider_combo.setToolTip(
            "OpenAI hears and speaks in one step and replies fastest. Sarvam AI chains "
            "speech-to-text, chat and text-to-speech, with Indian voices and languages."
        )
        voice_form.addRow("Voice service", self.provider_combo)
        self.voice_combo = QComboBox()
        for voice in VOICES:
            self.voice_combo.addItem(voice.capitalize(), voice)
        voice_form.addRow("Voice", self.voice_combo)
        self.sarvam_voice_combo = QComboBox()
        for voice, label in SARVAM_VOICES.items():
            self.sarvam_voice_combo.addItem(label, voice)
        voice_form.addRow("Voice", self.sarvam_voice_combo)
        self.provider_combo.currentIndexChanged.connect(self._show_provider_voices)
        self.animation_combo = QComboBox()
        for style, label in ANIMATION_STYLES.items():
            self.animation_combo.addItem(label, style)
        voice_form.addRow("Animation", self.animation_combo)
        self.listen_combo = QComboBox()
        for mode, label in LISTEN_MODES.items():
            self.listen_combo.addItem(label, mode)
        self.listen_combo.setToolTip(
            "Wake word: Naretta waits until a sentence contains \u201cNaretta\u201d, then talks "
            "until things go quiet for a while."
        )
        voice_form.addRow("Listening", self.listen_combo)
        self.language_combo = QComboBox()
        for code, label in TRANSCRIBE_LANGUAGES.items():
            self.language_combo.addItem(label, code)
        self.language_combo.setToolTip("Used to write down what you say in the chat panel.")
        voice_form.addRow("Your language", self.language_combo)
        self.theme_combo = QComboBox()
        for theme, label in THEMES.items():
            self.theme_combo.addItem(label, theme)
        voice_form.addRow("Background", self.theme_combo)

        self.key_edits: dict[str, QLineEdit] = {}
        self.key_statuses: dict[str, QLabel] = {}
        self.remove_key_buttons: dict[str, QPushButton] = {}
        self.keys_section = Section("API keys", on_toggled=self._fit_height)
        keys_layout = QVBoxLayout(self.keys_section.body)
        keys_layout.setContentsMargins(4, 6, 4, 6)
        for provider in KEY_HELP:
            keys_layout.addWidget(self._key_box(provider))
        self.key_edit = self.key_edits["openai"]
        self.sarvam_key_edit = self.key_edits["sarvam"]

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        content = QWidget()
        content.setObjectName("settingsContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 6, 0)
        content_layout.setSpacing(10)
        for section in (self.audio_section, self.assistant_section, self.keys_section):
            content_layout.addWidget(section)
        content_layout.addStretch(1)
        self._content = content
        self._scroll = QScrollArea()
        self._scroll.setObjectName("settingsScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setWidget(content)
        self._buttons = buttons

        layout = QVBoxLayout(self)
        layout.addWidget(self._scroll, 1)
        layout.addWidget(buttons)

        self._select(self.provider_combo, store.provider)
        self._select(self.voice_combo, store.voice)
        self._select(self.sarvam_voice_combo, store.sarvam_voice)
        self._show_provider_voices()
        self._select(self.mic_mode_combo, store.mic_mode)
        self.echo_check.setChecked(store.echo_cancellation)
        self._select(self.volume_combo, store.voice_volume)
        self._select(self.animation_combo, store.animation_style)
        self._select(self.listen_combo, store.listen_mode)
        self._select(self.language_combo, store.transcribe_language)
        self._select(self.theme_combo, store.theme)
        self._load_devices()
        for provider in KEY_HELP:
            self._refresh_key_status(provider)
        for combo in (self.mic_combo, self.speaker_combo, self.provider_combo, self.voice_combo, self.sarvam_voice_combo):
            combo.currentIndexChanged.connect(self._update_summaries)
        self._update_summaries()
        self._fit_height()

    def _update_summaries(self) -> None:
        """What a folded section shows in its header line."""
        self.audio_section.set_summary(self.mic_combo.currentText())
        sarvam = self.provider_combo.currentData() == "sarvam"
        voice = (self.sarvam_voice_combo if sarvam else self.voice_combo).currentText()
        self.assistant_section.set_summary(f"{self.provider_combo.currentText().split(' (')[0]}, {voice}")
        keys = [
            f"{KEY_HELP[p][0].split(' ')[0]} {'set' if credentials.resolve_api_key(p)[0] else 'missing'}"
            for p in KEY_HELP
        ]
        self.keys_section.set_summary(", ".join(keys))

    def _fit_height(self) -> None:
        """Grows or shrinks with the open sections, up to most of the screen; the rest scrolls."""
        if not hasattr(self, "_content"):
            return  # still being built
        self._content.layout().invalidate()
        layout = self.layout()
        margins = layout.contentsMargins()
        wanted = (
            self._content.sizeHint().height() + self._buttons.sizeHint().height()
            + layout.spacing() + margins.top() + margins.bottom() + 4
        )
        screen = self.screen()
        cap = int(screen.availableGeometry().height() * MAX_SCREEN_SHARE) if screen else 700
        # Leave room for the scrollbar so it never covers the right edge of the fields.
        width = (
            self._content.sizeHint().width() + self._scroll.verticalScrollBar().sizeHint().width()
            + margins.left() + margins.right() + 8
        )
        self.setMinimumWidth(max(480, width))
        self.resize(max(self.width(), self.minimumWidth()), min(wanted, cap))

    def _key_box(self, provider: str) -> QWidget:
        title, _ = KEY_HELP[provider]
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 8)
        heading = QLabel(title)
        heading.setObjectName("keyHeading")
        layout.addWidget(heading)
        row = QHBoxLayout()
        edit = QLineEdit()
        edit.setEchoMode(QLineEdit.EchoMode.Password)
        edit.setPlaceholderText("Paste a new key to replace the current one")
        edit.setClearButtonEnabled(True)
        remove = QPushButton("Remove saved key")
        remove.clicked.connect(lambda: self._remove_key(provider))
        row.addWidget(edit, 1)
        row.addWidget(remove)
        status = QLabel()
        status.setWordWrap(True)
        status.setObjectName("mutedText")
        layout.addLayout(row)
        layout.addWidget(status)
        self.key_edits[provider], self.key_statuses[provider], self.remove_key_buttons[provider] = edit, status, remove
        return box

    def _show_provider_voices(self) -> None:
        sarvam = self.provider_combo.currentData() == "sarvam"
        self._voice_form.setRowVisible(self.voice_combo, not sarvam)
        self._voice_form.setRowVisible(self.sarvam_voice_combo, sarvam)

    @staticmethod
    def _select(combo: QComboBox, data) -> None:
        index = combo.findData(data)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _load_devices(self) -> None:
        self.device_error.hide()
        for combo, kind, current in (
            (self.mic_combo, "input", self.mic_combo.currentData() if self.mic_combo.count() else self._store.input_device),
            (self.speaker_combo, "output", self.speaker_combo.currentData() if self.speaker_combo.count() else self._store.output_device),
        ):
            combo.clear()
            combo.addItem(SYSTEM_DEFAULT, None)
            try:
                names = list_devices(kind)
            except AudioError as exc:
                names = []
                self.device_error.setText(str(exc))
                self.device_error.show()
            for name in names:
                combo.addItem(name, name)
            if current and combo.findData(current) < 0:
                combo.addItem(f"{current} (not connected)", current)
            self._select(combo, current)

    def _refresh_key_status(self, provider: str) -> None:
        key, source = credentials.resolve_api_key(provider)
        if key:
            self.key_statuses[provider].setText(f"A key is set (from {source}). It is never shown here.")
        else:
            self.key_statuses[provider].setText(
                f"No key yet. Paste one from {KEY_HELP[provider][1]} \u2014 it will be saved "
                "in Windows Credential Manager."
            )
        self.remove_key_buttons[provider].setEnabled(credentials.load_saved_key(provider) is not None)
        if hasattr(self, "keys_section"):
            self._update_summaries()

    def _remove_key(self, provider: str) -> None:
        try:
            credentials.delete_saved_key(provider)
        except credentials.CredentialError as exc:
            QMessageBox.warning(self, "Naretta", str(exc))
        self._refresh_key_status(provider)

    # ------------------------------------------------------------------ microphone test

    def _toggle_mic_test(self, on: bool) -> None:
        if not on:
            self._stop_mic_test()
            return
        self._test_level, self._test_peak = 0.0, 0
        self._test_mic = Microphone(self.mic_combo.currentData(), self._on_test_chunk, lambda _m: None)
        try:
            self._test_mic.start()
        except AudioError as exc:
            self._test_mic = None
            self.mic_meter_label.setText(str(exc))
            self.mic_test_button.setChecked(False)
            return
        self.mic_test_button.setText("Stop test")
        self._test_timer.start()

    def _on_test_chunk(self, chunk: bytes) -> None:  # audio thread
        samples = np.frombuffer(chunk, dtype=np.int16)
        if samples.size:
            self._test_level = voice_level(samples)
            self._test_peak = max(self._test_peak, int(np.abs(samples.astype(np.int32)).max()))

    def _update_meter(self) -> None:
        self.mic_meter.setValue(round(self._test_level * 100))
        peak_db = 20 * math.log10(max(self._test_peak, 1) / 32768)
        if self._test_peak >= 3000:
            verdict = "Good \u2014 Naretta can hear you."
        elif self._test_peak >= 500:
            verdict = "Quiet \u2014 speak up or raise the input level in Windows Sound settings."
        else:
            verdict = "Almost silent \u2014 check the mic-mute key and Windows input level."
        self.mic_meter_label.setText(f"Loudest so far: {peak_db:.0f} dBFS. {verdict}")

    def _stop_mic_test(self) -> None:
        self._test_timer.stop()
        if self._test_mic is not None:
            self._test_mic.close()
            self._test_mic = None
        self.mic_test_button.setText("Test microphone")
        self.mic_meter.setValue(0)

    def done(self, result: int) -> None:
        self._stop_mic_test()
        super().done(result)

    def _save(self) -> None:
        for provider, edit in self.key_edits.items():
            new_key = edit.text().strip()
            if not new_key:
                continue
            if any(ch.isspace() for ch in new_key):
                QMessageBox.warning(self, "Naretta", "An API key shouldn't contain spaces.")
                return
            try:
                credentials.save_key(new_key, provider)
            except credentials.CredentialError as exc:
                QMessageBox.warning(self, "Naretta", str(exc))
                return
            edit.clear()
        self._store.provider = self.provider_combo.currentData()
        self._store.sarvam_voice = self.sarvam_voice_combo.currentData()
        self._store.input_device = self.mic_combo.currentData()
        self._store.output_device = self.speaker_combo.currentData()
        self._store.voice = self.voice_combo.currentData()
        self._store.mic_mode = self.mic_mode_combo.currentData()
        self._store.echo_cancellation = self.echo_check.isChecked()
        self._store.voice_volume = self.volume_combo.currentData()
        self._store.animation_style = self.animation_combo.currentData()
        self._store.listen_mode = self.listen_combo.currentData()
        self._store.transcribe_language = self.language_combo.currentData()
        self._store.theme = self.theme_combo.currentData()
        self.accept()
