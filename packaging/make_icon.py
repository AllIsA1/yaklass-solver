"""Рисует иконку приложения: yaklass_app/assets/icon.png (512×512), icon.ico (16…256) и icon-small.png.

Тёплый терракотовый квадрат со скруглением, тетрадная «клетка», «Я» с засечками (отсылка к ЯКлассу),
значок-галочка («решено») и искра. Запуск из корня репозитория:
    QT_QPA_PLATFORM=offscreen python packaging/make_icon.py
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QGuiApplication, QImage, QLinearGradient,  # noqa: E402
                           QPainter, QPainterPath, QPen, QRadialGradient)

S = 1024                                   # рисуем крупно, затем уменьшаем
SERIFS = ["Georgia", "Cambria", "IBM Plex Serif", "Noto Serif", "Liberation Serif", "DejaVu Serif"]


def serif_family() -> str:
    have = set(QFontDatabase.families())
    return next((f for f in SERIFS if f in have), "serif")


def squircle(r: QRectF, k: float = 0.235) -> QPainterPath:
    p = QPainterPath()
    p.addRoundedRect(r, r.width() * k, r.height() * k)
    return p


def sparkle(cx: float, cy: float, R: float, r: float) -> QPainterPath:
    """Четырёхлучевая искра с вогнутыми сторонами."""
    p = QPainterPath(QPointF(cx, cy - R))
    for (x1, y1), (x2, y2) in (((cx + r, cy - r), (cx + R, cy)), ((cx + r, cy + r), (cx, cy + R)),
                               ((cx - r, cy + r), (cx - R, cy)), ((cx - r, cy - r), (cx, cy - R))):
        p.quadTo(QPointF(x1, y1), QPointF(x2, y2))
    return p


def draw(simple: bool = False) -> QImage:
    """simple — для 16…32 px: без клетки, искр и галочки, буква крупнее."""
    img = QImage(S, S, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)

    m = S * 0.055                                          # поле вокруг, как у системных иконок
    box = QRectF(m, m, S - 2 * m, S - 2 * m)
    shape = squircle(box)

    # тень
    for i, a in enumerate((26, 18, 10)):
        p.fillPath(squircle(box.translated(0, S * (0.008 + 0.006 * i))), QColor(60, 25, 10, a))

    # фон: тёплый градиент
    g = QLinearGradient(box.topLeft(), box.bottomRight())
    g.setColorAt(0.0, QColor("#ee9a74"))
    g.setColorAt(0.55, QColor("#d9714f"))
    g.setColorAt(1.0, QColor("#b5492f"))
    p.fillPath(shape, g)

    p.save()
    p.setClipPath(shape)
    # мягкий блик сверху слева
    hl = QRadialGradient(QPointF(box.left() + box.width() * 0.25, box.top() + box.height() * 0.15), S * 0.75)
    hl.setColorAt(0.0, QColor(255, 255, 255, 45))
    hl.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.fillRect(box, hl)
    if not simple:
        # тетрадная клетка
        step = box.width() / 11
        p.setPen(QPen(QColor(255, 248, 240, 34), S * 0.0035))
        x = box.left() + step
        while x < box.right():
            p.drawLine(QPointF(x, box.top()), QPointF(x, box.bottom()))
            x += step
        y = box.top() + step
        while y < box.bottom():
            p.drawLine(QPointF(box.left(), y), QPointF(box.right(), y))
            y += step
        # поля тетради — тонкая линия слева
        p.setPen(QPen(QColor(255, 236, 225, 70), S * 0.006))
        p.drawLine(QPointF(box.left() + step * 1.6, box.top()), QPointF(box.left() + step * 1.6, box.bottom()))
    p.restore()

    # «Я»
    f = QFont(serif_family())
    f.setPixelSize(int(S * (0.78 if simple else 0.62)))
    f.setWeight(QFont.Bold)
    glyph = QPainterPath()
    glyph.addText(0, 0, f, "Я")
    gb = glyph.boundingRect()
    cx, cy = (S * 0.50, S * 0.51) if simple else (S * 0.47, S * 0.50)
    glyph.translate(cx - gb.center().x(), cy - gb.center().y())
    p.fillPath(glyph.translated(0, S * 0.012), QColor(110, 35, 15, 70))     # мягкая тень буквы
    p.fillPath(glyph, QColor("#fff8f1"))
    if simple:
        p.end()
        return img

    # значок-галочка снизу справа
    bc, br = QPointF(S * 0.725, S * 0.725), S * 0.135
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(110, 35, 15, 60))
    p.drawEllipse(bc + QPointF(0, S * 0.01), br, br)
    p.setBrush(QColor("#fff8f1"))
    p.drawEllipse(bc, br, br)
    p.setBrush(QBrush(QColor("#d9714f")))
    p.drawEllipse(bc, br * 0.80, br * 0.80)
    chk = QPainterPath(QPointF(bc.x() - br * 0.40, bc.y() + br * 0.02))
    chk.lineTo(QPointF(bc.x() - br * 0.10, bc.y() + br * 0.32))
    chk.lineTo(QPointF(bc.x() + br * 0.42, bc.y() - br * 0.30))
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor("#fff8f1"), br * 0.20, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPath(chk)

    # искра сверху справа
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#fff8f1"))
    p.drawPath(sparkle(S * 0.755, S * 0.255, S * 0.075, S * 0.012))
    p.setBrush(QColor(255, 248, 241, 190))
    p.drawPath(sparkle(S * 0.665, S * 0.165, S * 0.032, S * 0.006))
    p.end()
    return img


def png_bytes(img: QImage, size: int) -> bytes:
    small = img.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    small.save(buf, "PNG")
    return bytes(ba)


def write_ico(path: Path, img: QImage, sizes=(16, 24, 32, 48, 64, 128, 256)) -> None:
    """ICO с PNG внутри (поддерживается Windows Vista и новее)."""
    tiny = draw(simple=True)
    blobs = [png_bytes(tiny if s <= 32 else img, s) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(sizes))
    off = 6 + 16 * len(sizes)
    entries = b""
    for s, b in zip(sizes, blobs):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(b), off)
        off += len(b)
    path.write_bytes(head + entries + b"".join(blobs))


def main() -> None:
    _app = QGuiApplication(sys.argv)  # noqa: F841 — нужен для шрифтов
    out = Path(__file__).resolve().parent.parent / "yaklass_app" / "assets"
    img = draw()
    (out / "icon.png").write_bytes(png_bytes(img, 512))
    write_ico(out / "icon.ico", img)
    (out / "icon-small.png").write_bytes(png_bytes(draw(simple=True), 128))     # для боковой панели
    print("готово:", out / "icon.png", out / "icon.ico", out / "icon-small.png")


if __name__ == "__main__":
    main()
