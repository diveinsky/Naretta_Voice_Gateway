"""Round main window: status, voice animation, chat panel and conversation controls."""

from __future__ import annotations

from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QFontDatabase,
    QGradient,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRegion,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import credentials
from ..realtime import CONNECTED, CONNECTING, DISCONNECTED, RECONNECTING, RealtimeEngine
from ..settings import SettingsStore
from .chat_view import ChatView
from .settings_dialog import SettingsDialog
from .style import SURFACE_COLORS, build_stylesheet
from .voice_orb import VoiceAnimator

DIAMETER = 600
ORB_HEIGHT = 172
ORB_VIEW_HEIGHT = 500  # orb-only view: the animation fills the circle
MARGIN = 1  # keeps the disc's anti-aliased edge inside the circular window mask


class EngineBridge(QObject):
    """Receives engine callbacks on the engine thread and re-emits them as queued Qt signals."""

    connection_state = Signal(str)
    speaking = Signal(bool)
    error = Signal(str)
    notice = Signal(str)
    user_turn = Signal(str, str, str)
    assistant_turn = Signal(str, str, str)
    history_cleared = Signal()
    wake_state = Signal(bool)

    def on_connection_state(self, state: str) -> None:
        self.connection_state.emit(state)

    def on_speaking(self, speaking: bool) -> None:
        self.speaking.emit(speaking)

    def on_error(self, message: str) -> None:
        self.error.emit(message)

    def on_notice(self, message: str) -> None:
        self.notice.emit(message)

    def on_user_turn(self, item_id: str, text: str, status: str) -> None:
        self.user_turn.emit(item_id, text, status)

    def on_assistant_turn(self, item_id: str, text: str, status: str) -> None:
        self.assistant_turn.emit(item_id, text, status)

    def on_history_cleared(self) -> None:
        self.history_cleared.emit()

    def on_wake_state(self, awake: bool) -> None:
        self.wake_state.emit(awake)


# Short state names for screen readers and the title tooltip; the caption under the orb
# is what people see.
STATUS_TEXT = {
    "disconnected": "Disconnected",
    "connecting": "Connecting\u2026",
    "reconnecting": "Reconnecting\u2026",
    "listening": "Listening",
    "sleeping": "Waiting for \u201cNaretta\u201d",
    "speaking": "Speaking",
    "muted": "Muted",
}

# Segoe Fluent Icons (Windows 11) / Segoe MDL2 Assets (Windows 10) code points, with
# plain-text fallbacks for systems without either font.
ICONS = {
    "play": ("", "▶"),
    "stop": ("", "■"),
    "mic": ("", "Mic"),
    "mic_off": ("", "Off"),
    "new": ("", "+"),
    "settings": ("", "⚙"),
    "minimize": ("", "–"),
    "close": ("", "✕"),
    "sun": ("\ue706", "\u2600"),
    "moon": ("\ue708", "\u263e"),
    "expand": ("\ue740", "\u2922"),
    "collapse": ("\ue73f", "\u2921"),
}


def _icon_family() -> str | None:
    families = set(QFontDatabase.families())
    for family in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
        if family in families:
            return family
    return None


class RoundButton(QPushButton):
    """Circular icon button with a tooltip and accessible name."""

    def __init__(self, icon: str, tooltip: str, size: int, object_name: str = "roundButton"):
        super().__init__()
        self.setObjectName(object_name)
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._family = _icon_family()
        # A widget-level style sheet, because the app-wide font-family rule would win over setFont().
        family = f'font-family: "{self._family}";' if self._family else ""
        self.setStyleSheet(f"{family} font-size: {round(size * 0.36)}px;")
        self.set_icon(icon, tooltip)

    def set_icon(self, icon: str, tooltip: str) -> None:
        glyph, fallback = ICONS[icon]
        self.setText(glyph if self._family else fallback)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.icon_name = icon


class OrbArea(QWidget):
    """Transparent space in the layout where the animation is painted; clickable."""

    clicked = Signal()
    double_clicked = Signal()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)


class CircleSurface(QWidget):
    """Paints the round window: disc, voice animation and caption. Drag to move."""

    def __init__(self, animator: VoiceAnimator, orb_area: QWidget):
        super().__init__()
        self.setObjectName("surface")
        self._animator = animator
        self._orb_area = orb_area
        self._colors = SURFACE_COLORS["dark"]
        animator.changed.connect(self.update)

    def set_colors(self, colors: dict) -> None:
        self._colors = colors
        self.update()

    def disc_rect(self) -> QRectF:
        return QRectF(MARGIN, MARGIN, DIAMETER, DIAMETER)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        disc = self.disc_rect()

        background = QLinearGradient(disc.topLeft(), disc.bottomRight())
        background.setColorAt(0, QColor(self._colors["disc_top"]))
        background.setColorAt(1, QColor(self._colors["disc_bottom"]))
        clip = QPainterPath()
        clip.addEllipse(disc)
        p.fillPath(clip, background)
        p.setClipPath(clip)

        area = self._orb_area.geometry()
        orb_center = QPointF(area.center().x(), area.center().y() - 12)
        radius = min(area.width(), area.height()) * 0.25
        self._animator.paint(p, orb_center, radius)

        p.setPen(QColor(self._colors["caption"]))
        p.drawText(
            QRectF(area.left(), area.bottom() - 24, area.width(), 24),
            Qt.AlignmentFlag.AlignCenter,
            self._animator.caption(),
        )
        p.setClipping(False)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(*self._colors["outer_ring"]), 1.5))
        p.drawEllipse(disc.adjusted(0.75, 0.75, -0.75, -0.75))
        p.setPen(QPen(QColor(*self._colors["inner_ring"]), 1))
        p.drawEllipse(disc.adjusted(9, 9, -9, -9))
        p.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
        super().mousePressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, store: SettingsStore | None = None, engine_factory=RealtimeEngine):
        super().__init__()
        self.setWindowTitle("Naretta")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(QSize(DIAMETER + 2 * MARGIN, DIAMETER + 2 * MARGIN))
        # Make the window itself a circle: nothing outside it is drawn or clickable.
        self.setMask(QRegion(0, 0, DIAMETER + 2 * MARGIN, DIAMETER + 2 * MARGIN, QRegion.RegionType.Ellipse))

        self.store = store or SettingsStore()
        self.bridge = EngineBridge()
        self.engine = engine_factory(self.bridge)
        self._connection = DISCONNECTED
        self._speaking = False
        self._muted = False
        self._awake = True  # wake-word mode: False while waiting for "Naretta"
        self._orb_view = False

        self.orb = VoiceAnimator(self._levels, self)
        self.orb.set_animation_style(self.store.animation_style)
        self.orb_area = OrbArea()
        self.orb_area.setFixedHeight(ORB_HEIGHT)
        self.orb_area.setToolTip("Double-click to switch the orb-only view")
        self.orb_area.clicked.connect(self._on_orb_clicked)
        self.orb_area.double_clicked.connect(self.toggle_orb_view)
        self.surface = CircleSurface(self.orb, self.orb_area)

        # Window controls along the top of the circle
        self.settings_button = RoundButton("settings", "Settings", 32, "chromeButton")
        self.theme_button = RoundButton("sun", "White background", 32, "chromeButton")
        self.orb_view_button = RoundButton("expand", "Orb-only view", 32, "chromeButton")
        self.minimize_button = RoundButton("minimize", "Minimize", 32, "chromeButton")
        self.close_button = RoundButton("close", "Close", 32, "closeButton")
        self.settings_button.clicked.connect(self.open_settings)
        self.theme_button.clicked.connect(self.toggle_theme)
        self.orb_view_button.clicked.connect(self.toggle_orb_view)
        self.minimize_button.clicked.connect(self.showMinimized)
        self.close_button.clicked.connect(self.close)
        chrome = QHBoxLayout()
        chrome.setSpacing(8)
        chrome.addStretch(1)
        for button in (
            self.settings_button,
            self.theme_button,
            self.orb_view_button,
            self.minimize_button,
            self.close_button,
        ):
            chrome.addWidget(button)
        chrome.addStretch(1)

        self.title = QLabel("Naretta")
        self.title.setObjectName("title")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.heading = QWidget()
        heading = QHBoxLayout(self.heading)
        heading.setContentsMargins(0, 0, 0, 0)
        heading.addWidget(self.title)
        self.status_text = ""

        # Message banner for errors and notices
        self.banner = QFrame()
        self.banner.setObjectName("banner")
        banner_layout = QHBoxLayout(self.banner)
        banner_layout.setContentsMargins(14, 6, 6, 6)
        self.banner_text = QLabel()
        self.banner_text.setObjectName("bannerText")
        self.banner_text.setWordWrap(True)
        self.banner_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        close_banner = QPushButton("✕")
        close_banner.setObjectName("bannerClose")
        close_banner.setFixedSize(24, 24)
        close_banner.setToolTip("Dismiss")
        close_banner.clicked.connect(self.banner.hide)
        banner_layout.addWidget(self.banner_text, 1)
        banner_layout.addWidget(close_banner, 0, Qt.AlignmentFlag.AlignTop)
        self.banner.hide()

        self.chat = ChatView(bubble_width=300)
        # Fade older messages out at the top instead of cutting them off with a straight edge.
        fade = QLinearGradient(0, 0, 0, 1)
        fade.setCoordinateMode(QGradient.CoordinateMode.ObjectBoundingMode)
        fade.setColorAt(0.0, QColor(0, 0, 0, 0))
        fade.setColorAt(0.22, QColor(0, 0, 0, 255))
        fade.setColorAt(1.0, QColor(0, 0, 0, 255))
        fade_effect = QGraphicsOpacityEffect(self.chat)
        fade_effect.setOpacity(1.0)  # default is 0.7
        fade_effect.setOpacityMask(fade)
        self.chat.setGraphicsEffect(fade_effect)
        self.middle = middle = QWidget()
        middle.setFixedSize(440, 200)
        middle_layout = QVBoxLayout(middle)
        middle_layout.setContentsMargins(0, 0, 0, 0)
        middle_layout.setSpacing(6)
        middle_layout.addWidget(self.banner)
        middle_layout.addWidget(self.chat, 1)

        # Conversation controls
        self.mute_button = RoundButton("mic", "Mute microphone", 50)
        self.mute_button.setCheckable(True)
        self.main_button = RoundButton("play", "Start conversation", 70, "mainButton")
        self.new_button = RoundButton("new", "New conversation", 50)
        self.mute_button.toggled.connect(self.set_muted)
        self.main_button.clicked.connect(self._on_main_clicked)
        self.new_button.clicked.connect(self.new_conversation)
        self.mute_label = QLabel("Mute")
        self.main_label = QLabel("Start")
        new_label = QLabel("New chat")
        self.controls = QWidget()
        controls = QHBoxLayout(self.controls)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(22)
        controls.addStretch(1)
        for button, label in (
            (self.mute_button, self.mute_label),
            (self.main_button, self.main_label),
            (self.new_button, new_label),
        ):
            label.setObjectName("controlLabel")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setMinimumWidth(60)
            column = QVBoxLayout()
            column.setSpacing(3)
            column.addStretch(1)
            column.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
            column.addWidget(label)
            controls.addLayout(column)
        controls.addStretch(1)

        layout = QVBoxLayout(self.surface)
        layout.setContentsMargins(MARGIN + 70, MARGIN + 24, MARGIN + 70, MARGIN + 34)
        layout.setSpacing(6)
        layout.addLayout(chrome)
        layout.addWidget(self.heading)
        layout.addWidget(self.orb_area)
        layout.addWidget(middle, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)
        layout.addWidget(self.controls)
        self.setCentralWidget(self.surface)

        self.bridge.connection_state.connect(self._on_connection_state)
        self.bridge.speaking.connect(self._on_speaking)
        self.bridge.error.connect(lambda message: self.show_banner(message, error=True))
        self.bridge.notice.connect(lambda message: self.show_banner(message, error=False))
        self.bridge.user_turn.connect(lambda i, t, s: self.chat.upsert(i, "user", t, s))
        self.bridge.assistant_turn.connect(lambda i, t, s: self.chat.upsert(i, "assistant", t, s))
        self.bridge.history_cleared.connect(self.chat.clear)
        self.bridge.wake_state.connect(self._on_wake_state)

        self.apply_theme(self.store.theme)
        self._refresh()

    # ------------------------------------------------------------------ actions

    def start_conversation(self) -> None:
        key, _ = credentials.resolve_api_key()
        if not key:
            QMessageBox.information(
                self,
                "Naretta",
                "Naretta needs an OpenAI API key. Paste it in Settings, or set the "
                "OPENAI_API_KEY environment variable.",
            )
            self.open_settings()
            key, _ = credentials.resolve_api_key()
            if not key:
                return
        self.banner.hide()
        self._connection = CONNECTING
        self._refresh()
        self.engine.set_muted(self._muted)
        self.engine.start(self.store.session_config(key))

    def stop_conversation(self) -> None:
        self.engine.stop()

    def _on_main_clicked(self) -> None:
        if self._connection == DISCONNECTED:
            self.start_conversation()
        else:
            self.stop_conversation()

    def _on_orb_clicked(self) -> None:
        if self._connection == DISCONNECTED:
            self.start_conversation()
        elif self.status_key() == "sleeping":
            self.engine.wake()

    def toggle_orb_view(self) -> None:
        self.set_orb_view(not self._orb_view)

    def set_orb_view(self, on: bool) -> None:
        """Orb-only view: hide the chat and controls so the animation fills the circle."""
        self._orb_view = on
        for widget in (self.heading, self.middle, self.controls):
            widget.setVisible(not on)
        self.orb_area.setFixedHeight(ORB_VIEW_HEIGHT if on else ORB_HEIGHT)
        self.orb_view_button.set_icon(
            "collapse" if on else "expand", "Show chat and controls" if on else "Orb-only view"
        )

    def apply_theme(self, theme: str) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_stylesheet(theme))
        self.surface.set_colors(SURFACE_COLORS.get(theme, SURFACE_COLORS["dark"]))
        dark = theme != "light"
        self.theme_button.set_icon("sun" if dark else "moon", "White background" if dark else "Dark background")

    def toggle_theme(self) -> None:
        self.store.theme = "light" if self.store.theme == "dark" else "dark"
        self.apply_theme(self.store.theme)

    def _levels(self) -> tuple[float, float]:
        if self._connection != CONNECTED:
            return (0.0, 0.0)
        return self.engine.levels() or (0.0, 0.0)

    def set_muted(self, muted: bool) -> None:
        self._muted = muted
        self.mute_button.set_icon(
            "mic_off" if muted else "mic", "Unmute microphone" if muted else "Mute microphone"
        )
        self.mute_label.setText("Unmute" if muted else "Mute")
        self.engine.set_muted(muted)
        self._refresh()

    def new_conversation(self) -> None:
        self.banner.hide()
        self.engine.new_conversation()

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.store, self)
        if dialog.exec() != SettingsDialog.DialogCode.Accepted:
            return
        self.orb.set_animation_style(self.store.animation_style)
        self.apply_theme(self.store.theme)
        if self._connection != DISCONNECTED:
            key, _ = credentials.resolve_api_key()
            if key:
                # Reopen devices and the session with the new settings; history is kept.
                self.engine.restart(self.store.session_config(key))

    def show_banner(self, message: str, error: bool) -> None:
        if self._orb_view:
            self.set_orb_view(False)  # make sure the message is visible
        self.banner.setProperty("error", error)
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.banner_text.style().unpolish(self.banner_text)
        self.banner_text.style().polish(self.banner_text)
        self.banner_text.setText(message)
        self.banner.show()

    # ------------------------------------------------------------------ state

    def _on_connection_state(self, state: str) -> None:
        self._connection = state
        if state == CONNECTED and self.banner.property("error") is False:
            self.banner.hide()  # clear "Reconnecting…" notices
        if state != CONNECTED:
            self._speaking = False
        if state == DISCONNECTED:
            self._awake = True
        self._refresh()

    def _on_wake_state(self, awake: bool) -> None:
        self._awake = awake
        self._refresh()

    def _on_speaking(self, speaking: bool) -> None:
        self._speaking = speaking
        self._refresh()

    def status_key(self) -> str:
        if self._connection == DISCONNECTED:
            return "disconnected"
        if self._connection == CONNECTING:
            return "connecting"
        if self._connection == RECONNECTING:
            return "reconnecting"
        if self._speaking:
            return "speaking"
        if self._muted:
            return "muted"
        return "listening" if self._awake else "sleeping"

    def _refresh(self) -> None:
        key = self.status_key()
        text = STATUS_TEXT[key]
        if key == "speaking" and self._muted:
            text += " \u00b7 mic muted"
        self.status_text = text
        self.title.setToolTip(f"Pronounced nah-RET-tah \u00b7 {text}")
        self.surface.setAccessibleDescription(text)
        self.orb.set_state(key)
        self.orb.set_caption_override(
            "Speaking \u2014 your mic is muted" if key == "speaking" and self._muted else None
        )
        running = self._connection != DISCONNECTED
        self.main_button.set_icon(
            "stop" if running else "play", "Stop" if running else "Start conversation"
        )
        self.main_button.setProperty("running", running)
        self.main_button.style().unpolish(self.main_button)
        self.main_button.style().polish(self.main_button)
        self.main_label.setText("Stop" if running else "Start")
        clickable = not running or key == "sleeping"
        self.orb_area.setCursor(
            Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor
        )

    def showEvent(self, event) -> None:
        self.orb.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self.orb.stop()
        super().hideEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.orb.stop()
        self.engine.shutdown()
        super().closeEvent(event)
