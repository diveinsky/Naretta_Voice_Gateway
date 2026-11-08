"""Scrolling chat panel showing transcripts as they arrive."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

EMPTY_HINT = "Your conversation will appear here."


class Bubble(QFrame):
    def __init__(self, role: str):
        super().__init__()
        self.role = role
        self.setObjectName("userBubble" if role == "user" else "assistantBubble")
        self.setMinimumHeight(36)  # keeps the 16px corner radius on one-line bubbles
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.note = QLabel()
        self.note.setObjectName("bubbleNote")
        self.note.hide()
        layout.addWidget(self.label)
        layout.addWidget(self.note)

    def set_content(self, text: str, status: str) -> None:
        placeholder = status in ("pending", "partial") and not text
        if placeholder:
            text = "Listening…" if self.role == "user" else "…"
        elif status == "failed":
            text = "(Couldn't transcribe this part.)"
        self.label.setText(text)
        self.label.setProperty("placeholder", placeholder or status == "failed")
        self.label.style().unpolish(self.label)
        self.label.style().polish(self.label)
        if status == "interrupted":
            self.note.setText("Interrupted — shows roughly what you heard")
            self.note.show()
        else:
            self.note.hide()


class ChatView(QScrollArea):
    def __init__(self, bubble_width: int = 560):
        super().__init__()
        self._bubble_width = bubble_width
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setObjectName("chatView")
        container = QWidget()
        container.setObjectName("chatContainer")
        self._layout = QVBoxLayout(container)
        self._layout.setContentsMargins(14, 12, 14, 12)
        self._layout.setSpacing(10)
        self._hint = QLabel(EMPTY_HINT)
        self._hint.setObjectName("emptyHint")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(self._hint)
        self._layout.addStretch(1)
        self.setWidget(container)
        self._rows: dict[str, tuple[QWidget, Bubble]] = {}
        # Follow new messages unless the user has scrolled up to read older ones.
        self._stick_to_bottom = True
        bar = self.verticalScrollBar()
        bar.valueChanged.connect(self._on_scrolled)
        bar.rangeChanged.connect(self._on_range_changed)

    def upsert(self, item_id: str, role: str, text: str, status: str) -> None:
        if status == "empty":
            self.remove(item_id)
            return
        if item_id not in self._rows:
            self._add_row(item_id, role)
        self._rows[item_id][1].set_content(text, status)

    def remove(self, item_id: str) -> None:
        row = self._rows.pop(item_id, None)
        if row is not None:
            row[0].deleteLater()
        self._hint.setVisible(not self._rows)

    def clear(self) -> None:
        for item_id in list(self._rows):
            self.remove(item_id)

    def count(self) -> int:
        return len(self._rows)

    def bubble(self, item_id: str) -> Bubble | None:
        row = self._rows.get(item_id)
        return row[1] if row else None

    def _add_row(self, item_id: str, role: str) -> None:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        bubble = Bubble(role)
        bubble.setMaximumWidth(self._bubble_width)
        if role == "user":
            row_layout.addStretch(1)
            row_layout.addWidget(bubble)
        else:
            row_layout.addWidget(bubble)
            row_layout.addStretch(1)
        # Insert before the trailing stretch.
        self._layout.insertWidget(self._layout.count() - 1, row)
        self._rows[item_id] = (row, bubble)
        self._hint.hide()

    def _on_scrolled(self, value: int) -> None:
        self._stick_to_bottom = value >= self.verticalScrollBar().maximum() - 40

    def _on_range_changed(self, _minimum: int, maximum: int) -> None:
        if self._stick_to_bottom:
            self.verticalScrollBar().setValue(maximum)
