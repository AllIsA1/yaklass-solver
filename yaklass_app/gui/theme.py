"""Тёмная и светлая темы (QSS)."""
from __future__ import annotations

PALETTES = {
    "dark": dict(bg="#0e1120", side="#121629", surface="#171c33", surface2="#1f2542", border="#2a3252",
                 text="#e9ecf8", muted="#8f98ba", accent="#6f7cff", accent_h="#8590ff", accent_t="#ffffff",
                 ok="#3ddc97", warn="#ffb454", danger="#ff6b81", input="#10142a", sel="#2b3566"),
    "light": dict(bg="#f2f4fb", side="#ffffff", surface="#ffffff", surface2="#eef1fb", border="#dde2f1",
                  text="#1a1f3a", muted="#69718f", accent="#4f5df0", accent_h="#6674f5", accent_t="#ffffff",
                  ok="#14a76c", warn="#d98a1a", danger="#e5484d", input="#f7f8fd", sel="#dfe4ff"),
}


def _icons() -> str:
    """SVG-галочка для чекбоксов (QSS умеет только url(файл)); пишем один раз во временную папку."""
    import tempfile
    from pathlib import Path
    d = Path(tempfile.gettempdir()) / "yaklass-solver-icons"
    d.mkdir(exist_ok=True)
    f = d / "check.svg"
    if not f.exists():
        f.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20">'
                     '<path d="M5 10.5l3.2 3.2L15 6.8" fill="none" stroke="white" stroke-width="2.4" '
                     'stroke-linecap="round" stroke-linejoin="round"/></svg>', encoding="utf-8")
    return f.as_posix()


def colors(name: str) -> dict:
    return PALETTES.get(name, PALETTES["dark"])


def stylesheet(name: str) -> str:
    c = colors(name)
    check = _icons()
    return f"""
* {{ font-family: "Segoe UI", "Noto Sans", "Inter", "Roboto", sans-serif; font-size: 13px; color: {c['text']}; }}
QMainWindow, #root {{ background: {c['bg']}; }}
#sidebar {{ background: {c['side']}; border-right: 1px solid {c['border']}; }}
#brand {{ font-size: 17px; font-weight: 700; letter-spacing: 0.3px; }}
#brandSub {{ color: {c['muted']}; font-size: 11px; }}
QPushButton#nav {{ text-align: left; padding: 10px 14px; border: none; border-radius: 10px; color: {c['muted']};
                  background: transparent; font-size: 14px; }}
QPushButton#nav:hover {{ background: {c['surface2']}; color: {c['text']}; }}
QPushButton#nav:checked {{ background: {c['surface2']}; color: {c['text']}; font-weight: 600; }}
#pageTitle {{ font-size: 22px; font-weight: 700; }}
#pageSub {{ color: {c['muted']}; }}
QFrame#card {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 14px; }}
#cardTitle {{ font-size: 15px; font-weight: 600; }}
#muted {{ color: {c['muted']}; }}
#hint {{ color: {c['muted']}; font-size: 12px; }}
QLabel#pill {{ padding: 4px 11px; border-radius: 11px; font-size: 12px; font-weight: 600;
              background: {c['surface2']}; color: {c['muted']}; }}
QLabel#pill[tone="ok"] {{ background: rgba(61,220,151,0.16); color: {c['ok']}; }}
QLabel#pill[tone="warn"] {{ background: rgba(255,180,84,0.16); color: {c['warn']}; }}
QLabel#pill[tone="bad"] {{ background: rgba(255,107,129,0.16); color: {c['danger']}; }}
QLabel#chip {{ padding: 2px 9px; border-radius: 9px; background: {c['surface2']}; color: {c['muted']}; font-size: 11px; }}
QPushButton {{ padding: 8px 16px; border-radius: 10px; border: 1px solid {c['border']}; background: {c['surface2']}; }}
QPushButton:hover {{ border-color: {c['accent']}; }}
QPushButton:disabled {{ color: {c['muted']}; background: transparent; }}
QPushButton#primary {{ background: {c['accent']}; border: none; color: {c['accent_t']}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {c['accent_h']}; }}
QPushButton#primary:disabled {{ background: {c['surface2']}; color: {c['muted']}; }}
QPushButton#danger {{ color: {c['danger']}; }}
QPushButton#danger:hover {{ border-color: {c['danger']}; }}
QLineEdit, QComboBox, QPlainTextEdit {{
    background: {c['input']}; border: 1px solid {c['border']}; border-radius: 9px; padding: 7px 10px;
    selection-background-color: {c['sel']}; }}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {c['accent']}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ background: {c['surface']}; border: 1px solid {c['border']}; selection-background-color: {c['sel']}; }}
QCheckBox {{ spacing: 10px; }}
QCheckBox::indicator {{ width: 20px; height: 20px; border-radius: 6px; background: {c['input']}; border: 1.5px solid {c['border']}; }}
QCheckBox::indicator:hover {{ border-color: {c['accent']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; image: url({check}); }}
QRadioButton {{ spacing: 10px; }}
QRadioButton::indicator {{ width: 18px; height: 18px; border-radius: 10px; background: {c['input']}; border: 1.5px solid {c['border']}; }}
QRadioButton::indicator:hover {{ border-color: {c['accent']}; }}
QRadioButton::indicator:checked {{ border-color: {c['accent']};
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 {c['accent']}, stop:0.45 {c['accent']}, stop:0.5 {c['input']}, stop:1 {c['input']}); }}
QProgressBar {{ background: {c['surface2']}; border: none; border-radius: 6px; height: 12px; text-align: center; font-size: 10px; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 6px; }}
QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget {{ border: none; background: transparent; }}
QScrollArea > QWidget > QScrollBar {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QToolTip {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 5px; }}
"""
