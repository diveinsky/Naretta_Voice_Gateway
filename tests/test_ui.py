"""Offscreen smoke tests for the Qt interface (no audio or network)."""

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from naretta import credentials
from naretta.settings import SettingsStore
from naretta.ui import settings_dialog
from naretta.ui.main_window import MainWindow
from naretta.ui.settings_dialog import SettingsDialog


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class FakeEngine:
    def __init__(self, listener):
        self.listener = listener
        self.calls = []

    def __getattr__(self, name):
        return lambda *args: self.calls.append((name, args))

    def levels(self):
        return (0.6, 0.8)


@pytest.fixture
def store(tmp_path):
    return SettingsStore(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))


@pytest.fixture
def fake_keyring(monkeypatch):
    saved = {}
    monkeypatch.setattr(credentials, "load_saved_key", lambda: saved.get("key"))
    monkeypatch.setattr(credentials, "save_key", lambda k: saved.__setitem__("key", k))
    monkeypatch.setattr(credentials, "delete_saved_key", lambda: saved.pop("key", None))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return saved


def test_window_status_and_chat(app, store, fake_keyring, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-key-1234567890")
    window = MainWindow(store, engine_factory=FakeEngine)
    assert window.windowTitle() == "Naretta"
    assert window.status_text == "Disconnected"

    window.start_conversation()
    name, args = window.engine.calls[-1]
    assert name == "start" and args[0].api_key == "sk-env-key-1234567890"
    assert window.status_text == "Connecting…"
    assert window.main_button.toolTip() == "Stop"  # Start turns into Stop while running

    bridge = window.bridge
    bridge.on_connection_state("connected")
    app.processEvents()
    assert window.status_text == "Listening"
    bridge.on_user_turn("u1", "", "pending")
    bridge.on_user_turn("u1", "Hello", "final")
    bridge.on_assistant_turn("a1", "Hi, I'm Naretta.", "partial")
    bridge.on_speaking(True)
    app.processEvents()
    assert window.chat.count() == 2
    assert window.chat.bubble("u1").label.text() == "Hello"
    assert window.status_text == "Speaking"
    assert window.orb.state == "speaking"
    for _ in range(20):
        window.orb.tick()
    assert window.orb.level > 0.7  # follows Naretta's voice level while she speaks

    window.mute_button.setChecked(True)
    assert ("set_muted", (True,)) in window.engine.calls
    assert window.status_text == "Speaking · mic muted"
    assert window.orb.caption() == "Speaking — your mic is muted"
    bridge.on_speaking(False)
    app.processEvents()
    assert window.status_text == "Muted"
    assert window.mute_button.toolTip() == "Unmute microphone"

    bridge.on_assistant_turn("a1", "Hi…", "interrupted")
    app.processEvents()
    assert not window.chat.bubble("a1").note.isHidden()

    bridge.on_error("OpenAI rejected the API key.")
    bridge.on_connection_state("disconnected")
    app.processEvents()
    assert not window.banner.isHidden()
    assert window.status_text == "Disconnected"
    assert window.main_button.toolTip() == "Start conversation"

    window.new_button.click()
    bridge.on_history_cleared()
    app.processEvents()
    assert window.chat.count() == 0
    window.close()
    assert ("shutdown", ()) in window.engine.calls


def test_settings_dialog_saves_choices_and_key(app, store, fake_keyring, monkeypatch):
    monkeypatch.setattr(
        settings_dialog,
        "list_devices",
        lambda kind: ["Headset Microphone"] if kind == "input" else ["Headphones", "Speakers"],
    )
    dialog = SettingsDialog(store)
    assert dialog.key_edit.echoMode() == dialog.key_edit.EchoMode.Password
    dialog.mic_combo.setCurrentIndex(dialog.mic_combo.findData("Headset Microphone"))
    dialog.speaker_combo.setCurrentIndex(dialog.speaker_combo.findData("Speakers"))
    dialog.voice_combo.setCurrentIndex(dialog.voice_combo.findData("sage"))
    dialog.animation_combo.setCurrentIndex(dialog.animation_combo.findData("bars"))
    dialog.key_edit.setText("  sk-new-key-abcdefghijkl  ")
    dialog._save()
    assert fake_keyring["key"] == "sk-new-key-abcdefghijkl"
    assert store.input_device == "Headset Microphone"
    assert store.output_device == "Speakers"
    assert store.voice == "sage"
    assert store.animation_style == "bars"
    assert dialog.key_edit.text() == ""

    # A device that has gone missing is kept and flagged rather than silently dropped.
    monkeypatch.setattr(settings_dialog, "list_devices", lambda kind: [])
    dialog2 = SettingsDialog(store)
    assert "not connected" in dialog2.mic_combo.currentText()


def test_round_window_paints_every_style_and_state(app, store):
    from naretta.ui.voice_orb import STATE_COLORS

    window = MainWindow(store, engine_factory=FakeEngine)
    window.bridge.on_connection_state("connected")
    app.processEvents()
    assert window.windowFlags() & window.windowFlags().FramelessWindowHint
    assert window.width() == window.height()  # round window
    from PySide6.QtCore import QPoint

    mask = window.mask()
    assert mask.contains(QPoint(window.width() // 2, window.height() // 2))
    assert not mask.contains(QPoint(3, 3))  # corners are outside the circular window
    assert not mask.contains(QPoint(window.width() - 3, window.height() - 3))
    for style in ("orb", "rings", "bars"):
        window.orb.set_animation_style(style)
        for state in STATE_COLORS:
            window.orb.set_state(state)
            window.orb.tick()
            assert not window.grab().isNull()
    window.orb.set_state("listening")
    for _ in range(20):
        window.orb.tick()
    assert window.orb.caption() == "Hearing you\u2026"
    window.close()


def test_main_button_toggles_start_and_stop(app, store, fake_keyring, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-key-1234567890")
    window = MainWindow(store, engine_factory=FakeEngine)
    window.main_button.click()
    assert window.engine.calls[-1][0] == "start"
    window.bridge.on_connection_state("connected")
    app.processEvents()
    window.main_button.click()
    assert window.engine.calls[-1][0] == "stop"
    window.close()


def test_microphone_test_meter(app, store, fake_keyring, monkeypatch):
    import numpy as np

    started = []

    class FakeMic:
        def __init__(self, device, on_chunk, on_failure):
            self.on_chunk = on_chunk
            started.append(self)

        def start(self):
            pass

        def close(self):
            self.closed = True

    monkeypatch.setattr(settings_dialog, "list_devices", lambda kind: [])
    monkeypatch.setattr(settings_dialog, "Microphone", FakeMic)
    dialog = SettingsDialog(store)
    dialog.mic_test_button.setChecked(True)
    started[0].on_chunk((np.sin(np.arange(960) / 4) * 12000).astype(np.int16).tobytes())
    dialog._update_meter()
    assert dialog.mic_meter.value() > 50
    assert "Good" in dialog.mic_meter_label.text()
    dialog.reject()
    assert started[0].closed  # the test mic is released when the dialog closes


def test_theme_orb_view_and_wake_status(app, store, fake_keyring):
    window = MainWindow(store, engine_factory=FakeEngine)
    assert store.theme == "dark"
    window.theme_button.click()
    assert store.theme == "light" and window.theme_button.toolTip() == "Dark background"
    assert "#ffffff" in window.surface._colors["disc_top"]
    window.theme_button.click()
    assert store.theme == "dark"

    window.show()
    window.orb_view_button.click()
    assert not window.middle.isVisible() and not window.controls.isVisible()
    window.bridge.on_error("Something broke")
    app.processEvents()
    assert window.middle.isVisible()  # errors bring the full view back

    window.bridge.on_connection_state("connected")
    window.bridge.on_wake_state(False)
    app.processEvents()
    assert window.status_key() == "sleeping"
    assert "Naretta" in window.status_text
    window.orb_area.clicked.emit()
    assert ("wake", ()) in window.engine.calls
    window.close()


def test_settings_saves_listening_language_and_background(app, store, fake_keyring, monkeypatch):
    monkeypatch.setattr(settings_dialog, "list_devices", lambda kind: [])
    dialog = SettingsDialog(store)
    dialog.listen_combo.setCurrentIndex(dialog.listen_combo.findData("wake"))
    dialog.language_combo.setCurrentIndex(dialog.language_combo.findData("hi"))
    dialog.theme_combo.setCurrentIndex(dialog.theme_combo.findData("light"))
    assert dialog.echo_check.isChecked()  # on by default
    dialog.echo_check.setChecked(False)
    assert dialog.volume_combo.currentData() == 3.0  # boosted by default
    dialog.volume_combo.setCurrentIndex(dialog.volume_combo.findData(4.0))
    dialog._save()
    assert store.session_config("sk-x-123456789").voice_volume == 4.0
    assert store.session_config("sk-x-123456789").echo_cancellation is False
    cfg = store.session_config("sk-x-123456789")
    assert (cfg.listen_mode, cfg.transcribe_language, store.theme) == ("wake", "hi", "light")
