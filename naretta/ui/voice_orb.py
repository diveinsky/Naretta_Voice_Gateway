"""Voice-reactive animation painted in the middle of the round window.

It follows the microphone while Naretta listens and her own voice while she speaks; the
color and motion change with the connection state. Three looks: orb, rings and bars.
"""

from __future__ import annotations

import math
import time
from typing import Callable

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QRadialGradient

from ..config import ANIMATION_STYLES, DEFAULT_ANIMATION_STYLE

# state: (idle color, color at full voice level)
STATE_COLORS = {
    "disconnected": ("#64748b", "#94a3b8"),
    "connecting": ("#f59e0b", "#fbbf24"),
    "reconnecting": ("#f59e0b", "#fbbf24"),
    "listening": ("#14b8a6", "#34d399"),
    "sleeping": ("#6366f1", "#818cf8"),
    "speaking": ("#7c5cff", "#c084fc"),
    "muted": ("#ef4444", "#f87171"),
}

CAPTIONS = {
    "disconnected": "Tap the circle to start",
    "connecting": "Connecting…",
    "reconnecting": "Reconnecting…",
    "listening": "I’m listening",
    "sleeping": "Say \u201cNaretta\u201d to wake me",
    "speaking": "Speaking — talk anytime to interrupt",
    "muted": "Microphone muted",
}

FRAME_MS = 16


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )


def _alpha(color: QColor, alpha: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


class VoiceAnimator(QObject):
    """Animation state plus painting; the owning widget repaints on `changed`."""

    changed = Signal()

    def __init__(self, level_source: Callable[[], tuple[float, float]], parent=None):
        super().__init__(parent)
        self._level_source = level_source
        self._state = "disconnected"
        self._style = DEFAULT_ANIMATION_STYLE
        self._level = 0.0
        self._caption_override: str | None = None
        self._color = QColor(STATE_COLORS["disconnected"][0])
        self._start = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self.tick)

    # ------------------------------------------------------------------ API

    @property
    def state(self) -> str:
        return self._state

    @property
    def level(self) -> float:
        return self._level

    @property
    def animation_style(self) -> str:
        return self._style

    def set_state(self, state: str) -> None:
        if state in STATE_COLORS and state != self._state:
            self._state = state
            self.changed.emit()

    def set_animation_style(self, style: str) -> None:
        if style in ANIMATION_STYLES:
            self._style = style
            self.changed.emit()

    def set_caption_override(self, text: str | None) -> None:
        self._caption_override = text

    def caption(self) -> str:
        if self._caption_override:
            return self._caption_override
        if self._state == "listening" and self._level > 0.3:
            return "Hearing you…"
        return CAPTIONS[self._state]

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    # ------------------------------------------------------------------ animation

    def _target_level(self) -> float:
        mic, voice = self._level_source() or (0.0, 0.0)
        if self._state == "speaking":
            return voice
        if self._state == "listening":
            return mic
        if self._state == "sleeping":
            return mic * 0.5  # a gentle sign that she can hear, without looking engaged
        return 0.0

    def tick(self) -> None:
        target = self._target_level()
        # Fast attack, slower release, so the motion follows syllables without jitter.
        rate = 0.45 if target > self._level else 0.10
        self._level += (target - self._level) * rate
        idle, active = (QColor(c) for c in STATE_COLORS[self._state])
        self._color = _mix(self._color, _mix(idle, active, self._level * 1.4), 0.15)
        self.changed.emit()

    # ------------------------------------------------------------------ painting

    def paint(self, p: QPainter, center: QPointF, radius: float) -> None:
        t = time.monotonic() - self._start
        color = self._color
        if self._state == "muted":
            color = _mix(color, QColor("#475569"), 0.45)
        self._paint_glow(p, center, radius, self._level, color)
        painter = {"orb": self._paint_orb, "rings": self._paint_rings, "bars": self._paint_bars}
        painter[self._style](p, center, radius, self._level, color, t)
        if self._state in ("connecting", "reconnecting"):
            self._paint_spinner(p, center, radius, color, t)

    @staticmethod
    def _paint_glow(p, c, r, level, color) -> None:
        glow_r = r * (2.3 + 0.7 * level)
        glow = QRadialGradient(c, glow_r)
        glow.setColorAt(0.0, _alpha(color, 0.35 + 0.35 * level))
        glow.setColorAt(0.45, _alpha(color, 0.10 + 0.15 * level))
        glow.setColorAt(1.0, _alpha(color, 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(c, glow_r, glow_r)

    @staticmethod
    def _sphere_brush(c: QPointF, r: float, color: QColor) -> QRadialGradient:
        brush = QRadialGradient(QPointF(c.x() - r * 0.35, c.y() - r * 0.4), r * 1.5)
        brush.setColorAt(0.0, color.lighter(165))
        brush.setColorAt(0.5, color)
        brush.setColorAt(1.0, color.darker(190))
        return brush

    def _paint_orb(self, p, c, r, level, color, t) -> None:
        breathe = 0.025 * math.sin(t * 1.6)
        wobble = 0.03 + 0.20 * level
        path = QPainterPath()
        steps = 120
        for i in range(steps + 1):
            a = 2 * math.pi * i / steps
            d = (
                0.5 * math.sin(3 * a + t * 2.1)
                + 0.3 * math.sin(5 * a - t * 1.7)
                + 0.2 * math.sin(2 * a + t * 3.3)
            )
            rr = r * (1 + breathe + 0.12 * level + wobble * d)
            point = QPointF(c.x() + rr * math.cos(a), c.y() + rr * math.sin(a))
            if i == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)
        path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._sphere_brush(c, r, color))
        p.drawPath(path)
        p.setBrush(_alpha(QColor("white"), 0.18))
        p.drawEllipse(QPointF(c.x() - r * 0.3, c.y() - r * 0.35), r * 0.28, r * 0.18)

    def _paint_rings(self, p, c, r, level, color, t) -> None:
        speed = 0.25 + 0.9 * level
        for k in range(4):
            phase = (t * speed + k / 4) % 1.0
            rr = r * (0.7 + 1.1 * phase)
            alpha = (1 - phase) * (0.25 + 0.65 * level)
            p.setPen(QPen(_alpha(color.lighter(130), alpha), 2.0 + 3.0 * level * (1 - phase)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, rr, rr)
        core = r * (0.62 + 0.04 * math.sin(t * 1.6) + 0.22 * level)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._sphere_brush(c, core, color))
        p.drawEllipse(c, core, core)
        p.setPen(QPen(_alpha(QColor("white"), 0.35), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(c, core, core)

    def _paint_bars(self, p, c, r, level, color, t) -> None:
        bars = 56
        inner = r * 0.78
        for i in range(bars):
            a = 2 * math.pi * i / bars - math.pi / 2
            n = (
                0.5
                + 0.25 * math.sin(t * (3.1 + (i % 7) * 0.45) + i * 1.3)
                + 0.25 * math.sin(t * 5.3 - i * 0.7)
            )
            length = r * (0.07 + 0.03 * math.sin(t * 1.6 + i * 0.4) + level * 0.75 * n)
            start = QPointF(c.x() + inner * math.cos(a), c.y() + inner * math.sin(a))
            end = QPointF(c.x() + (inner + length) * math.cos(a), c.y() + (inner + length) * math.sin(a))
            pen = QPen(_alpha(color.lighter(100 + int(40 * n)), 0.55 + 0.45 * level), max(2.5, r * 0.06))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(start, end)
        core = r * (0.6 + 0.03 * math.sin(t * 1.6) + 0.08 * level)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._sphere_brush(c, core, color))
        p.drawEllipse(c, core, core)

    @staticmethod
    def _paint_spinner(p, c, r, color, t) -> None:
        rr = r * 1.45
        pen = QPen(_alpha(color.lighter(130), 0.9), 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        start = int(-t * 300 * 16) % (360 * 16)
        p.drawArc(QRectF(c.x() - rr, c.y() - rr, 2 * rr, 2 * rr), start, 100 * 16)
