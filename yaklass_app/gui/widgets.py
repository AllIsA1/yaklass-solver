"""Компоненты интерфейса: карточки, значки состояния, переключатели, карточки-варианты, строки настроек, иконки."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QObject, QPointF, QRect, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QDoubleValidator, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QRadioButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from . import theme

_THEME = {"name": "dark"}


def palette() -> dict:
    return theme.colors(_THEME["name"])


def set_theme_name(name: str) -> None:
    _THEME["name"] = name if name in theme.PALETTES else "dark"


# ============================================================ иконки
def pixmap(name: str, color: str, size: int = 18) -> QPixmap:
    pm = QPixmap()
    pm.loadFromData(theme.icon_svg(name, color, size * 2), "SVG")       # вдвое крупнее — чётко на HiDPI
    pm.setDevicePixelRatio(2.0)
    return pm


def set_icon(w: QWidget, name: str, role: str = "muted", size: int = 18) -> None:
    """Иконка кнопки или QLabel по имени; цвет берётся из темы (role — ключ палитры) и обновляется при её смене."""
    w.setProperty("ykIcon", name)
    w.setProperty("ykIconRole", role)
    w.setProperty("ykIconSize", size)
    _apply_icon(w)


def _apply_icon(w: QWidget) -> None:
    name, role, size = w.property("ykIcon"), w.property("ykIconRole"), int(w.property("ykIconSize") or 18)
    if not name:
        return
    pm = pixmap(name, palette().get(role, role), size)
    if isinstance(w, QAbstractButton):
        w.setIcon(QIcon(pm))
        w.setIconSize(QSize(size, size))
    elif isinstance(w, QLabel):
        w.setPixmap(pm)


def refresh_icons(root: QWidget) -> None:
    for w in [root, *root.findChildren(QWidget)]:
        _apply_icon(w)
        if isinstance(w, (Pill, Toggle, OptionCard)):
            w.update()


def icon_label(name: str, role: str = "muted", size: int = 18) -> QLabel:
    lb = QLabel()
    lb.setFixedSize(size, size)
    set_icon(lb, name, role, size)
    return lb


# ============================================================ простые блоки
class Card(QFrame):
    """Карточка с заголовком (и иконкой); содержимое — в `.body` (QVBoxLayout), справа от заголовка — `.head`."""

    def __init__(self, title: str = "", hint: str = "", icon: str = "", name: str = "card"):
        super().__init__()
        self.setObjectName(name)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(10)
        self.head = QHBoxLayout()
        self.head.setSpacing(10)
        if icon:
            self.head.addWidget(icon_label(icon, "muted", 18))
        if title:
            t = QLabel(title)
            t.setObjectName("cardTitle")
            self.head.addWidget(t)
        self.head.addStretch(1)
        if icon or title:
            outer.addLayout(self.head)
        if hint:
            h = QLabel(hint)
            h.setObjectName("hint")
            h.setWordWrap(True)
            outer.addWidget(h)
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        outer.addLayout(self.body)


def divider() -> QFrame:
    d = QFrame()
    d.setObjectName("divider")
    return d


def section(text: str) -> QLabel:
    lb = QLabel(text.upper())
    lb.setObjectName("section")
    return lb


class Pill(QLabel):
    """Значок состояния: цветная точка + текст. tone = ok | warn | bad | '' (серый)."""

    def __init__(self, text: str = "", tone: str = ""):
        super().__init__(text)
        self.setObjectName("pill")
        self.tone = ""
        self.set(text, tone)

    def set(self, text: str, tone: str = "") -> None:
        self.tone = tone
        self.setText(text)
        self.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def paintEvent(self, e) -> None:  # noqa: N802
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = palette()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor({"ok": c["ok"], "warn": c["warn"], "bad": c["danger"]}.get(self.tone, c["faint"])))
        p.drawEllipse(QPointF(12.5, self.height() / 2), 3.5, 3.5)


def label(text: str, name: str = "", wrap: bool = True) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lb


def row(*widgets, spacing: int = 10, stretch_last: bool = False, fill: QWidget | None = None) -> QHBoxLayout:
    """None — растяжка. `fill` — виджет, которому достаётся всё свободное место (для текста с переносом строк:
    без этого такая подпись в одной строке с растяжкой сжимается до минимальной ширины и ломается на строки)."""
    r = QHBoxLayout()
    r.setContentsMargins(0, 0, 0, 0)
    r.setSpacing(spacing)
    for w in widgets:
        if w is None:
            r.addStretch(1)
        elif w is fill:
            r.addWidget(w, 1)
        else:
            r.addWidget(w)
    if stretch_last:
        r.addStretch(1)
    return r


def form_row(caption: str, widget: QWidget, hint: str = "") -> QWidget:
    """Подпись сверху, поле под ней (для широких полей)."""
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(5)
    c = QLabel(caption)
    c.setObjectName("rowTitle")
    lay.addWidget(c)
    lay.addWidget(widget)
    if hint:
        h = QLabel(hint)
        h.setObjectName("hint")
        h.setWordWrap(True)
        lay.addWidget(h)
    return box


class SettingRow(QWidget):
    """Строка настроек: название и пояснение слева, элемент управления справа (или под текстом — stacked).
    Щелчок по тексту строки с переключателем переключает его."""

    def __init__(self, title: str, control: QWidget | None = None, hint: str = "", stacked: bool = False,
                 control_width: int | None = None):
        super().__init__()
        self.control = control
        lay = QVBoxLayout(self) if stacked else QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(8 if stacked else 24)
        text = QVBoxLayout()
        text.setSpacing(3)
        t = QLabel(title)
        t.setObjectName("rowTitle")
        t.setWordWrap(True)
        text.addWidget(t)
        if hint:
            h = QLabel(hint)
            h.setObjectName("hint")
            h.setWordWrap(True)
            text.addWidget(h)
        lay.addLayout(text, 1)
        if control is not None:
            if control_width:
                control.setFixedWidth(control_width)
            if stacked:
                lay.addWidget(control)
            else:
                lay.addWidget(control, 0, Qt.AlignVCenter | Qt.AlignRight)
        if isinstance(control, Toggle):
            self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if isinstance(self.control, Toggle) and self.control.isEnabled() and e.button() == Qt.LeftButton:
            self.control.toggle()
        super().mouseReleaseEvent(e)


# ============================================================ переключатель
class Toggle(QCheckBox):
    """Переключатель вместо галочки (API как у QCheckBox: isChecked / setChecked / toggled)."""
    W, H = 38, 22

    def __init__(self, text: str = ""):
        super().__init__(text)
        self.setCursor(Qt.PointingHandCursor)
        self._pos = 0.0
        self._anim = QVariantAnimation(self, duration=140, easingCurve=QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._moved)
        self.toggled.connect(self._animate)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def _moved(self, v) -> None:
        self._pos = float(v)
        self.update()

    def _animate(self, on: bool) -> None:
        if not self.isVisible():
            self._pos = 1.0 if on else 0.0
            return
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def setChecked(self, on: bool) -> None:  # noqa: N802
        super().setChecked(on)
        if not self.isVisible():
            self._pos = 1.0 if on else 0.0

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.W, self.H)

    def hitButton(self, pos) -> bool:  # noqa: N802
        return self.rect().contains(pos)

    def paintEvent(self, e) -> None:  # noqa: N802
        c = palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0.5, 0.5, self.W - 1, self.H - 1)
        off, on = QColor(c["border2"]), QColor(c["accent"])
        track = QColor(int(off.red() + (on.red() - off.red()) * self._pos),
                       int(off.green() + (on.green() - off.green()) * self._pos),
                       int(off.blue() + (on.blue() - off.blue()) * self._pos))
        if not self.isEnabled():
            track.setAlpha(110)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(r, self.H / 2, self.H / 2)
        d = self.H - 6
        x = 3 + (self.W - 6 - d) * self._pos
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(x, 3, d, d))


# ============================================================ карточка-вариант (вместо радиокнопки)
class OptionCard(QRadioButton):
    """Выбор одного из вариантов карточкой: заголовок, пояснение, иконка. API как у QRadioButton."""
    PAD = 14

    def __init__(self, title: str, desc: str = "", icon: str = ""):
        super().__init__(title)
        self.title, self.desc, self.icon_name = title, desc, icon
        self.setCursor(Qt.PointingHandCursor)
        sp = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self.setMinimumWidth(140)

    def _fonts(self) -> tuple[QFont, QFont]:
        tf = QFont(self.font())
        tf.setWeight(QFont.DemiBold)
        df = QFont(self.font())
        df.setPixelSize(12)
        return tf, df

    def _text_width(self, w: int) -> int:
        return max(60, w - 2 * self.PAD - 30 - (26 if self.icon_name else 0))

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, w: int) -> int:  # noqa: N802
        tf, df = self._fonts()
        from PySide6.QtGui import QFontMetrics
        tw = self._text_width(w)
        th = QFontMetrics(tf).boundingRect(QRect(0, 0, tw, 10_000), Qt.TextWordWrap, self.title).height()
        dh = QFontMetrics(df).boundingRect(QRect(0, 0, tw, 10_000), Qt.TextWordWrap, self.desc).height() if self.desc else 0
        return 2 * self.PAD + th + (4 + dh if dh else 0)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(260, self.heightForWidth(260))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(140, self.heightForWidth(max(self.width(), 140)))

    def hitButton(self, pos) -> bool:  # noqa: N802
        return self.rect().contains(pos)

    def paintEvent(self, e) -> None:  # noqa: N802
        c = palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        on = self.isChecked()
        hover = self.underMouse()
        p.setPen(QPen(QColor(c["accent"] if on else (c["faint"] if hover else c["border2"])), 1.5 if on else 1))
        bg = QColor(c["surface"])
        if on:
            bg = QColor(c["accent"])
            bg.setAlpha(30)
        elif hover:
            bg = QColor(c["hover"])
        p.setBrush(bg)
        p.drawRoundedRect(r, 12, 12)
        x = self.PAD
        if self.icon_name:
            p.drawPixmap(x, self.PAD, pixmap(self.icon_name, c["accent"] if on else c["muted"], 18))
            x += 26
        tf, df = self._fonts()
        tw = self._text_width(self.width())
        p.setPen(QColor(c["text"]))
        p.setFont(tf)
        tr = p.boundingRect(QRect(x, self.PAD - 1, tw, 10_000), Qt.TextWordWrap, self.title)
        p.drawText(tr, Qt.TextWordWrap, self.title)
        if self.desc:
            p.setPen(QColor(c["muted"]))
            p.setFont(df)
            p.drawText(QRect(x, tr.bottom() + 5, tw, 10_000), Qt.TextWordWrap, self.desc)
        # кружок выбора справа сверху
        cx, cy = self.width() - self.PAD - 8, self.PAD + 8
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(c["accent"] if on else c["border2"]), 1.6))
        p.drawEllipse(QPointF(cx, cy), 8, 8)
        if on:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(c["accent"]))
            p.drawEllipse(QPointF(cx, cy), 4.2, 4.2)

    def enterEvent(self, e) -> None:  # noqa: N802
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(e)


class BrandMark(QWidget):
    """Квадратная метка приложения с буквой (рисуется, без файлов)."""

    def __init__(self, letter: str = "Y", size: int = 28):
        super().__init__()
        self.letter = letter
        self.setFixedSize(size, size)

    def paintEvent(self, e) -> None:  # noqa: N802
        c = palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 8, 8)
        p.fillPath(path, QColor(c["accent"]))
        f = QFont(self.font())
        f.setFamilies(["Georgia", "Cambria", "IBM Plex Serif", "Noto Serif", "DejaVu Serif"])
        f.setPixelSize(int(self.height() * 0.58))
        f.setWeight(QFont.Bold)
        p.setFont(f)
        p.setPen(QColor(c["accent_t"]))
        p.drawText(self.rect(), Qt.AlignCenter, self.letter)


# ============================================================ мост и числовое поле
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

    def setValue(self, v: float) -> None:  # noqa: N802
        v = min(self.hi, max(self.lo, float(v)))
        self.setText(str(int(round(v))) if self.decimals == 0 else f"{v:.{self.decimals}f}".rstrip("0").rstrip(".") or "0")
