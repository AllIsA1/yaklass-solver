from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget


class Card(QFrame):
    """Карточка с заголовком; содержимое добавляйте в `.body` (QVBoxLayout)."""

    def __init__(self, title: str = "", hint: str = ""):
        super().__init__()
        self.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(12)
        if title:
            t = QLabel(title)
            t.setObjectName("cardTitle")
            outer.addWidget(t)
        if hint:
            h = QLabel(hint)
            h.setObjectName("hint")
            h.setWordWrap(True)
            outer.addWidget(h)
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        outer.addLayout(self.body)


class Pill(QLabel):
    """Цветная «таблетка» статуса: tone = ok | warn | bad | '' ."""

    def __init__(self, text: str = "", tone: str = ""):
        super().__init__(text)
        self.setObjectName("pill")
        self.set(text, tone)

    def set(self, text: str, tone: str = "") -> None:
        self.setText(text)
        self.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)


def label(text: str, name: str = "", wrap: bool = True) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lb


def row(*widgets, spacing: int = 10, stretch_last: bool = False) -> QHBoxLayout:
    r = QHBoxLayout()
    r.setContentsMargins(0, 0, 0, 0)
    r.setSpacing(spacing)
    for w in widgets:
        if w is None:
            r.addStretch(1)
        else:
            r.addWidget(w)
    if stretch_last:
        r.addStretch(1)
    return r


def form_row(caption: str, widget: QWidget, hint: str = "") -> QWidget:
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    c = QLabel(caption)
    c.setObjectName("muted")
    lay.addWidget(c)
    lay.addWidget(widget)
    if hint:
        h = QLabel(hint)
        h.setObjectName("hint")
        h.setWordWrap(True)
        lay.addWidget(h)
    return box


class Bridge(QObject):
    """Мост поток агента -> поток GUI (сигналы Qt потокобезопасны)."""
    event = Signal(str, dict)
    done = Signal(object, object, object)       # callback, result, error


class NumField(QLineEdit):
    """Числовое поле (вместо QSpinBox: тот плохо стилизуется — число уезжает за границу поля).
    API как у спинбокса: value() / setValue(); значение всегда внутри [lo, hi]."""

    def __init__(self, lo: float, hi: float, value: float = 0, decimals: int = 1, width: int = 96):
        super().__init__()
        self.lo, self.hi, self.decimals = lo, hi, decimals
        v = QDoubleValidator(lo, hi, decimals)
        v.setNotation(QDoubleValidator.StandardNotation)
        self.setValidator(v)
        self.setFixedWidth(width)
        self.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.setValue(value)
        self.editingFinished.connect(lambda: self.setValue(self.value()))     # нормализуем после ввода

    def value(self) -> float | int:
        try:
            v = float(self.text().replace(",", ".").strip())
        except ValueError:
            v = self.lo
        v = min(self.hi, max(self.lo, v))
        return int(round(v)) if self.decimals == 0 else round(v, self.decimals)

    def setValue(self, v: float) -> None:
        v = min(self.hi, max(self.lo, float(v)))
        self.setText(str(int(round(v))) if self.decimals == 0 else f"{v:.{self.decimals}f}".rstrip("0").rstrip(".") or "0")
