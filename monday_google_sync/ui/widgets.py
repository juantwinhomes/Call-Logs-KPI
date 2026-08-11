"""Small shared widgets: cards, metric tiles, status pills, connection rows."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout,
                               QWidget)

from services.auth_service import ConnState, ConnStatus
from ui.style import pill_for_state


class Card(QFrame):
    """A white panel with an optional small caps title."""

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 14, 16, 14)
        self._layout.setSpacing(10)
        if title:
            lbl = QLabel(title.upper())
            lbl.setObjectName("CardTitle")
            self._layout.addWidget(lbl)

    def body(self) -> QVBoxLayout:
        return self._layout

    def add(self, widget: QWidget) -> QWidget:
        self._layout.addWidget(widget)
        return widget

    def add_layout(self, layout) -> None:
        self._layout.addLayout(layout)


class Pill(QLabel):
    """A coloured status chip."""

    def __init__(self, text: str = "", kind: str = "idle", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("pill", kind)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set_state(self, text: str, kind: str) -> None:
        self.setText(text)
        if self.property("pill") != kind:
            self.setProperty("pill", kind)
            # Re-apply the stylesheet so the property selector takes effect.
            self.style().unpolish(self)
            self.style().polish(self)


class Metric(QWidget):
    """A label above a large number."""

    def __init__(self, label: str, value: str = "0", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self._label = QLabel(label.upper())
        self._label.setObjectName("MetricLabel")
        self._label.setWordWrap(True)
        self._value = QLabel(value)
        self._value.setObjectName("MetricValue")
        lay.addWidget(self._label)
        lay.addWidget(self._value)

    def set_value(self, value: object) -> None:
        self._value.setText(str(value))

    def set_colour(self, css_colour: str | None) -> None:
        self._value.setStyleSheet(f"color: {css_colour};" if css_colour else "")


class ConnectionRow(QWidget):
    """Service name, status pill, detail line and a Connect / Reconnect button."""

    connect_clicked = Signal()
    disconnect_clicked = Signal()
    test_clicked = Signal()

    def __init__(self, service: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(10)
        name = QLabel(service)
        name.setObjectName("SectionTitle")
        self.pill = Pill("Not Connected", "idle")
        top.addWidget(name)
        top.addWidget(self.pill)
        top.addStretch(1)

        self.btn_connect = QPushButton(f"Connect {service}")
        self.btn_connect.setObjectName("Primary")
        self.btn_test = QPushButton("Test Connection")
        self.btn_disconnect = QPushButton("Disconnect")
        self.btn_disconnect.setObjectName("Danger")
        self.btn_disconnect.setVisible(False)
        for b in (self.btn_connect, self.btn_test, self.btn_disconnect):
            top.addWidget(b)
        outer.addLayout(top)

        self.account = QLabel("")
        self.account.setObjectName("Muted")
        self.account.setWordWrap(True)
        outer.addWidget(self.account)

        self.detail = QLabel("")
        self.detail.setObjectName("Hint")
        self.detail.setWordWrap(True)
        self.detail.setVisible(False)
        outer.addWidget(self.detail)

        self.btn_connect.clicked.connect(self.connect_clicked.emit)
        self.btn_test.clicked.connect(self.test_clicked.emit)
        self.btn_disconnect.clicked.connect(self.disconnect_clicked.emit)

    def set_status(self, status: ConnStatus) -> None:
        self.pill.set_state(status.state, pill_for_state(status.state))
        connected = status.state == ConnState.CONNECTED
        self.account.setText(status.account or "")
        self.account.setVisible(bool(status.account))
        self.detail.setText(status.detail or "")
        self.detail.setVisible(bool(status.detail))
        self.btn_disconnect.setVisible(status.state != ConnState.NOT_CONNECTED)
        self.btn_test.setEnabled(status.state != ConnState.NOT_CONNECTED)
        if connected:
            self.btn_connect.setText(f"Reconnect {self.service}")
            self.btn_connect.setObjectName("")
        elif status.state == ConnState.EXPIRED:
            self.btn_connect.setText(f"Reconnect {self.service}")
            self.btn_connect.setObjectName("Primary")
        else:
            self.btn_connect.setText(f"Connect {self.service}")
            self.btn_connect.setObjectName("Primary")
        self.btn_connect.style().unpolish(self.btn_connect)
        self.btn_connect.style().polish(self.btn_connect)

    def set_busy(self, busy: bool, text: str = "") -> None:
        for b in (self.btn_connect, self.btn_test, self.btn_disconnect):
            b.setEnabled(not busy)
        if busy and text:
            self.detail.setText(text)
            self.detail.setVisible(True)


def hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color: #e9eef3; background: #e9eef3; max-height: 1px;")
    return line
