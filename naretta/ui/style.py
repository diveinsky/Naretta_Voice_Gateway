"""Style sheets for the dark and white themes."""

from __future__ import annotations

from string import Template

# Colors the round window paints itself (the rest is in the style sheet below).
SURFACE_COLORS = {
    "dark": {
        "disc_top": "#1e2245",
        "disc_bottom": "#0c0f22",
        "outer_ring": (255, 255, 255, 40),
        "inner_ring": (255, 255, 255, 16),
        "caption": "#cbd5e1",
    },
    "light": {
        "disc_top": "#ffffff",
        "disc_bottom": "#e6e9f2",
        "outer_ring": (15, 23, 42, 38),
        "inner_ring": (15, 23, 42, 14),
        "caption": "#475569",
    },
}

_TOKENS = {
    "dark": {
        "text": "#e2e8f0",
        "title": "#ffffff",
        "muted": "#94a3b8",
        "faint": "#64748b",
        "glass": "rgba(255, 255, 255, 0.08)",
        "glass_hover": "rgba(255, 255, 255, 0.16)",
        "glass_border": "rgba(255, 255, 255, 0.16)",
        "chrome_text": "#cbd5e1",
        "assistant_bubble": "rgba(255, 255, 255, 0.10)",
        "scroll_handle": "rgba(255, 255, 255, 0.22)",
        "scroll_handle_hover": "rgba(255, 255, 255, 0.35)",
        "banner_bg": "rgba(251, 191, 36, 0.16)",
        "banner_border": "rgba(251, 191, 36, 0.45)",
        "banner_text": "#fde68a",
        "error_bg": "rgba(239, 68, 68, 0.18)",
        "error_border": "rgba(239, 68, 68, 0.55)",
        "error_text": "#fecaca",
        "mute_on_text": "#fca5a5",
        "dialog_bg": "#151830",
        "group_bg": "#1e2245",
        "group_border": "rgba(255, 255, 255, 0.10)",
        "input_bg": "#0f1226",
        "input_border": "rgba(255, 255, 255, 0.18)",
        "button_bg": "rgba(255, 255, 255, 0.08)",
        "button_hover": "rgba(255, 255, 255, 0.15)",
        "button_pressed": "rgba(255, 255, 255, 0.22)",
        "button_border": "rgba(255, 255, 255, 0.18)",
        "disabled_text": "#5b6478",
        "selection": "#3b3270",
        "meter_bg": "rgba(255, 255, 255, 0.10)",
        "dialog_error": "#fca5a5",
    },
    "light": {
        "text": "#1f2328",
        "title": "#111827",
        "muted": "#64748b",
        "faint": "#94a3b8",
        "glass": "rgba(15, 23, 42, 0.05)",
        "glass_hover": "rgba(15, 23, 42, 0.10)",
        "glass_border": "rgba(15, 23, 42, 0.12)",
        "chrome_text": "#334155",
        "assistant_bubble": "#eceff5",
        "scroll_handle": "rgba(15, 23, 42, 0.20)",
        "scroll_handle_hover": "rgba(15, 23, 42, 0.32)",
        "banner_bg": "#fff8e1",
        "banner_border": "#f0d58a",
        "banner_text": "#8a5a00",
        "error_bg": "#fdecec",
        "error_border": "#f2b8b5",
        "error_text": "#b42318",
        "mute_on_text": "#b42318",
        "dialog_bg": "#f3f4f8",
        "group_bg": "#ffffff",
        "group_border": "#dde0e6",
        "input_bg": "#ffffff",
        "input_border": "#d3d7de",
        "button_bg": "#ffffff",
        "button_hover": "#f0f1f5",
        "button_pressed": "#e6e8ee",
        "button_border": "#d3d7de",
        "disabled_text": "#a3a8b0",
        "selection": "#ece8ff",
        "meter_bg": "#eef0f5",
        "dialog_error": "#b42318",
    },
}

_TEMPLATE = Template("""
QWidget { font-family: "Segoe UI", sans-serif; font-size: 10.5pt; color: $text; }
QDialog { background: $dialog_bg; }
QMainWindow { background: transparent; }

/* ---------------------------------------------------------------- round main window */
QWidget#surface { background: transparent; }
QWidget#surface QLabel { color: $text; background: transparent; }
QWidget#surface QLabel#title { font-size: 17pt; font-weight: 600; color: $title; }
QWidget#surface QLabel#controlLabel { color: $muted; font-size: 8.5pt; }

QPushButton#roundButton, QPushButton#chromeButton, QPushButton#closeButton {
    background: $glass; border: 1px solid $glass_border; color: $text; padding: 0; }
QPushButton#roundButton { border-radius: 25px; }
QPushButton#chromeButton, QPushButton#closeButton { border-radius: 16px; color: $chrome_text; }
QPushButton#roundButton:hover, QPushButton#chromeButton:hover { background: $glass_hover; }
QPushButton#closeButton:hover { background: #e5534b; border-color: #e5534b; color: #ffffff; }
QPushButton#roundButton:checked { background: rgba(239, 68, 68, 0.22); border-color: #ef4444; color: $mute_on_text; }
QPushButton#mainButton { border-radius: 35px; padding: 0; color: #ffffff;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #9b7bff, stop:1 #6a48f5); border: 2px solid rgba(255, 255, 255, 0.25); }
QPushButton#mainButton:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ab8fff, stop:1 #7c5cff); }
QPushButton#mainButton[running="true"] { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #f87171, stop:1 #dc2626); }
QPushButton#mainButton[running="true"]:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #fb8f8f, stop:1 #ef4444); }

QWidget#surface QScrollArea#chatView { background: transparent; border: none; }
QScrollArea#chatView > QWidget#qt_scrollarea_viewport { background: transparent; }
QWidget#chatContainer { background: transparent; }
QWidget#surface QLabel#emptyHint { color: $faint; padding: 20px; }
QFrame#userBubble { background: #7c5cff; border-radius: 16px; }
QWidget#surface QFrame#userBubble QLabel { color: #ffffff; }
QFrame#assistantBubble { background: $assistant_bubble; border-radius: 16px; }
QWidget#surface QFrame#assistantBubble QLabel { color: $text; }
QWidget#surface QLabel[placeholder="true"] { font-style: italic; color: $muted; }
QWidget#surface QFrame#userBubble QLabel[placeholder="true"] { color: #e3dcff; }
QWidget#surface QLabel#bubbleNote { font-size: 8pt; color: $muted; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 14px 4px 14px 0; }
QScrollBar::handle:vertical { background: $scroll_handle; border-radius: 3px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: $scroll_handle_hover; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

QFrame#banner { background: $banner_bg; border: 1px solid $banner_border; border-radius: 14px; }
QFrame#banner[error="true"] { background: $error_bg; border-color: $error_border; }
QWidget#surface QLabel#bannerText { color: $banner_text; font-size: 9pt; }
QWidget#surface QFrame#banner[error="true"] QLabel#bannerText { color: $error_text; }
QPushButton#bannerClose { background: transparent; border: none; border-radius: 12px; color: $text; padding: 0; }
QPushButton#bannerClose:hover { background: $glass_hover; }

/* ---------------------------------------------------------------- settings dialog */
QLabel#errorText { color: $dialog_error; }
QLabel#mutedText { color: $muted; }
QDialog QPushButton { background: $button_bg; border: 1px solid $button_border; border-radius: 17px; padding: 7px 18px; min-height: 20px; }
QDialog QPushButton:hover { background: $button_hover; }
QDialog QPushButton:pressed, QDialog QPushButton:checked { background: $button_pressed; }
QDialog QPushButton:disabled { color: $disabled_text; }
QGroupBox { font-weight: 600; border: 1px solid $group_border; border-radius: 18px; margin-top: 12px; padding: 14px 12px 12px 12px; background: $group_bg; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 4px; }
QLineEdit, QComboBox { background: $input_bg; border: 1px solid $input_border; border-radius: 14px; padding: 5px 12px; min-height: 18px; }
QLineEdit:focus, QComboBox:focus { border-color: #7c5cff; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView { border: 1px solid $input_border; border-radius: 10px; padding: 4px; background: $input_bg; selection-background-color: $selection; selection-color: $text; }
QProgressBar { background: $meter_bg; border: none; border-radius: 5px; }
QProgressBar::chunk { background: #34d399; border-radius: 5px; }
""")


def build_stylesheet(theme: str) -> str:
    return _TEMPLATE.substitute(_TOKENS.get(theme, _TOKENS["dark"]))
