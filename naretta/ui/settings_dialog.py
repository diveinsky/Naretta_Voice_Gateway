"""Settings: audio devices, voice, and the OpenAI API key."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from .. import credentials
from ..audio import AudioError, Microphone, list_devices, voice_level
from ..config import (
    ANIMATION_STYLES,
    LISTEN_MODES,
    MIC_MODES,
    THEMES,
    TRANSCRIBE_LANGUAGES,
    VOICE_VOLUMES,
    VOICES,
)
from ..settings import SettingsStore

SYSTEM_DEFAULT = "System default"


class SettingsDialog(QDialog):
    def __init__(self, store: SettingsStore, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Naretta — Settings")
        self.setMinimumWidth(480)
        self._store = store

        audio_box = QGroupBox("Audio")
        audio_form = QFormLayout(audio_box)
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

        voice_box = QGroupBox("Assistant")
        voice_form = QFormLayout(voice_box)
        self.voice_combo = QComboBox()
        for voice in VOICES:
            self.voice_combo.addItem(voice.capitalize(), voice)
        voice_form.addRow("Voice", self.voice_combo)
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

        key_box = QGroupBox("OpenAI API key")
        key_layout = QVBoxLayout(key_box)
        key_row = QHBoxLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("Paste a new key to replace the current one")
        self.key_edit.setClearButtonEnabled(True)
        self.remove_key_button = QPushButton("Remove saved key")
        self.remove_key_button.clicked.connect(self._remove_key)
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(self.remove_key_button)
        self.key_status = QLabel()
        self.key_status.setWordWrap(True)
        self.key_status.setObjectName("mutedText")
        key_layout.addLayout(key_row)
        key_layout.addWidget(self.key_status)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(audio_box)
        layout.addWidget(voice_box)
        layout.addWidget(key_box)
        layout.addWidget(buttons)

        self._select(self.voice_combo, store.voice)
        self._select(self.mic_mode_combo, store.mic_mode)
        self.echo_check.setChecked(store.echo_cancellation)
        self._select(self.volume_combo, store.voice_volume)
        self._select(self.animation_combo, store.animation_style)
        self._select(self.listen_combo, store.listen_mode)
        self._select(self.language_combo, store.transcribe_language)
        self._select(self.theme_combo, store.theme)
        self._load_devices()
        self._refresh_key_status()

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

    def _refresh_key_status(self) -> None:
        key, source = credentials.resolve_api_key()
        if key:
            self.key_status.setText(f"A key is set (from {source}). It is never shown here.")
        else:
            self.key_status.setText(
                "No key yet. Paste one from platform.openai.com/api-keys — it will be saved "
                "in Windows Credential Manager."
            )
        self.remove_key_button.setEnabled(credentials.load_saved_key() is not None)

    def _remove_key(self) -> None:
        try:
            credentials.delete_saved_key()
        except credentials.CredentialError as exc:
            QMessageBox.warning(self, "Naretta", str(exc))
        self._refresh_key_status()

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
        new_key = self.key_edit.text().strip()
        if new_key:
            if any(ch.isspace() for ch in new_key):
                QMessageBox.warning(self, "Naretta", "The API key shouldn't contain spaces.")
                return
            try:
                credentials.save_key(new_key)
            except credentials.CredentialError as exc:
                QMessageBox.warning(self, "Naretta", str(exc))
                return
            self.key_edit.clear()
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
