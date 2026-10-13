"""Тёмная и светлая темы (QSS) и иконки.

Стиль — спокойный, «тёплый»: нейтральные серо-бежевые поверхности почти без рамок, один терракотовый акцент,
заголовки с засечками, широкие скругления."""
from __future__ import annotations

PALETTES = {
    "dark": dict(bg="#262624", side="#1f1e1d", surface="#30302e", surface2="#3a3936", hover="#353432",
                 border="#3f3e3a", border2="#4d4b46", text="#ecebe5", muted="#a19f97", faint="#7b7972",
                 accent="#d97757", accent_h="#e3896b", accent_t="#ffffff", accent_soft="rgba(217,119,87,0.16)",
                 ok="#8bbf8a", warn="#e2a75c", danger="#e8705f", input="#2b2b29", sel="#5a4237",
                 code="#1c1b1a"),
    "light": dict(bg="#faf9f5", side="#f0eee6", surface="#ffffff", surface2="#ecebe4", hover="#f3f1ea",
                  border="#e3e0d6", border2="#d2cec2", text="#2c2b28", muted="#6e6b63", faint="#9a978e",
                  accent="#c6613f", accent_h="#b4552f", accent_t="#ffffff", accent_soft="rgba(198,97,63,0.12)",
                  ok="#3f8a4a", warn="#b37714", danger="#c4432f", input="#ffffff", sel="#f3d9cd",
                  code="#f4f2eb"),
}

SANS = '"Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans", "Ubuntu", "Cantarell", "DejaVu Sans", sans-serif'
SERIF = '"Tiempos Headline", "Georgia", "Cambria", "IBM Plex Serif", "Noto Serif", "Liberation Serif", "DejaVu Serif", serif'
MONO = '"JetBrains Mono", "Cascadia Mono", "Consolas", "Noto Sans Mono", "DejaVu Sans Mono", monospace'

# Иконки 24×24, линии в 1.7 px (рисуем сами: простые геометрические контуры).
ICONS = {
    "home": '<path d="M4 10.4 12 4l8 6.4V19a1 1 0 0 1-1 1h-4.5v-5.5h-5V20H5a1 1 0 0 1-1-1z"/>',
    "works": '<path d="M10 6.5h10M10 12h10M10 17.5h10"/><path d="M4 6.6l1.4 1.4L7.8 5.4M4 12.1l1.4 1.4 2.4-2.6'
             'M4 17.6l1.4 1.4 2.4-2.6"/>',
    "settings": '<path d="M4 7h9M17 7h3M4 12h3M11 12h9M4 17h11M19 17h1"/><circle cx="15" cy="7" r="2"/>'
                '<circle cx="9" cy="12" r="2"/><circle cx="17" cy="17" r="2"/>',
    "log": '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M7.5 9.5l3 2.5-3 2.5M12.5 15h4"/>',
    "refresh": '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 4.5v4.2h-4.2"/>',
    "play": '<path d="M8 5.5v13l10.5-6.5z" fill="CLR"/>',
    "moon": '<path d="M19.5 14.6A7.6 7.6 0 0 1 9.4 4.5a7.6 7.6 0 1 0 10.1 10.1z"/>',
    "sun": '<circle cx="12" cy="12" r="3.8"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4'
           'M5.6 18.4 7 17M17 7l1.4-1.4"/>',
    "bolt": '<path d="M13 3 5.5 13.2h5.6L10.5 21 18.5 10.6h-5.7z"/>',
    "check": '<path d="M5 12.5l4.2 4.2L19 7"/>',
    "chev": '<path d="M7 10l5 5 5-5"/>',
    "send": '<path d="M12 19V5M6 11l6-6 6 6"/>',
    "pause": '<path d="M9 6v12M15 6v12"/>',
    "stop": '<rect x="6.5" y="6.5" width="11" height="11" rx="2"/>',
    "download": '<path d="M12 4v11M7 10.5l5 5 5-5M5 19.5h14"/>',
    "copy": '<rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5V6a1.5 1.5 0 0 0-1.5-1.5H6'
            'A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5"/>',
    "trash": '<path d="M5 7h14M10 7V5h4v2M7 7l.8 12h8.4L17 7"/>',
    "telegram": '<path d="M20.5 4.5 3.8 11.2l5.6 2 2 6 3.2-3.8 4.6 3.3z"/><path d="M9.4 13.2l11.1-8.7"/>',
    "school": '<path d="M3 9.5 12 5l9 4.5-9 4.5z"/><path d="M7 11.5V16c1.5 1.5 3 2 5 2s3.5-.5 5-2v-4.5M21 9.5V14"/>',
    "spark": '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.6 2.6M15.4 15.4 18 18M6 18l2.6-2.6M15.4 8.6 18 6"/>',
}


def icon_svg(name: str, color: str, size: int = 24, stroke: float = 1.7) -> bytes:
    body = ICONS[name].replace("CLR", color)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
            ).encode()


def _icon_file(name: str, color: str) -> str:
    """QSS умеет картинки только из файла (url(...)): пишем SVG во временную папку один раз."""
    import tempfile
    from pathlib import Path
    d = Path(tempfile.gettempdir()) / "yaklass-solver-icons"
    d.mkdir(exist_ok=True)
    f = d / f"{name}-{color.lstrip('#')}.svg"
    if not f.exists():
        f.write_bytes(icon_svg(name, color, 20, 2.2 if name == "check" else 1.8))
    return f.as_posix()


def _icons() -> str:          # совместимость: галочка для чекбоксов
    return _icon_file("check", "#ffffff")


def colors(name: str) -> dict:
    return PALETTES.get(name, PALETTES["dark"])


def stylesheet(name: str) -> str:
    c = colors(name)
    check = _icon_file("check", c["accent_t"])
    chev = _icon_file("chev", c["muted"])
    return f"""
* {{ font-family: {SANS}; font-size: 13px; color: {c['text']}; }}
QMainWindow, #root, #main, QDialog {{ background: {c['bg']}; }}
QWidget#page, QWidget#column {{ background: transparent; }}

/* ---------- боковая панель ---------- */
#sidebar {{ background: {c['side']}; border-right: 1px solid {c['border']}; }}
#brand {{ font-family: {SERIF}; font-size: 19px; font-weight: 600; }}
#brandMark {{ background: {c['accent']}; border-radius: 8px; color: {c['accent_t']}; font-family: {SERIF};
             font-size: 15px; font-weight: 700; }}
#sideCaption {{ color: {c['faint']}; font-size: 11px; font-weight: 600; padding-left: 10px; }}
QPushButton#nav {{ text-align: left; padding: 8px 10px; border: none; border-radius: 8px; color: {c['muted']};
                  background: transparent; font-size: 13px; }}
QPushButton#nav:hover {{ background: {c['hover']}; color: {c['text']}; }}
QPushButton#nav:checked {{ background: {c['surface2']}; color: {c['text']}; font-weight: 600; }}
#account {{ background: transparent; border-top: 1px solid {c['border']}; }}
#accountName {{ font-weight: 600; }}
#version {{ color: {c['faint']}; font-size: 11px; }}

/* ---------- верхняя полоса и заголовки ---------- */
#topbar {{ background: {c['bg']}; border-bottom: 1px solid {c['border']}; }}
#topTitle {{ font-weight: 600; font-size: 13px; }}
#topSub {{ color: {c['faint']}; font-size: 12px; }}
#pageTitle {{ font-family: {SERIF}; font-size: 28px; font-weight: 500; }}
#pageSub {{ color: {c['muted']}; font-size: 13px; }}
#section {{ color: {c['muted']}; font-size: 12px; font-weight: 600; letter-spacing: 0.4px; padding: 0 2px; }}

/* ---------- карточки ---------- */
QFrame#card {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 14px; }}
QFrame#banner {{ background: {c['accent_soft']}; border: 1px solid {c['accent']}; border-radius: 14px; }}
QFrame#workRow {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px; }}
QFrame#workRow:hover {{ border-color: {c['border2']}; }}
QFrame#divider {{ background: {c['border']}; max-height: 1px; min-height: 1px; border: none; }}
#cardTitle {{ font-size: 14px; font-weight: 600; }}
#rowTitle {{ font-size: 13px; }}
#workTitle {{ font-size: 14px; font-weight: 600; }}
#muted {{ color: {c['muted']}; }}
#hint {{ color: {c['muted']}; font-size: 12px; }}
#empty {{ color: {c['muted']}; font-size: 13px; padding: 28px 0; }}

/* ---------- значки состояния ---------- */
QLabel#pill {{ padding: 3px 10px 3px 22px; border-radius: 11px; font-size: 12px; font-weight: 600;
              background: {c['surface2']}; color: {c['muted']}; }}
QLabel#pill[tone="ok"] {{ color: {c['ok']}; }}
QLabel#pill[tone="warn"] {{ color: {c['warn']}; }}
QLabel#pill[tone="bad"] {{ color: {c['danger']}; }}
QLabel#chip {{ padding: 2px 8px; border-radius: 6px; background: {c['surface2']}; color: {c['muted']};
              font-size: 11px; font-weight: 600; }}
QLabel#chipAccent {{ padding: 2px 8px; border-radius: 6px; background: {c['accent_soft']}; color: {c['accent']};
                    font-size: 11px; font-weight: 600; }}

/* ---------- кнопки ---------- */
QPushButton {{ padding: 7px 14px; border-radius: 8px; border: 1px solid {c['border2']}; background: transparent;
              font-weight: 500; }}
QPushButton:hover {{ background: {c['hover']}; }}
QPushButton:pressed {{ background: {c['surface2']}; }}
QPushButton:disabled {{ color: {c['faint']}; border-color: {c['border']}; }}
QPushButton#primary {{ background: {c['accent']}; border: 1px solid {c['accent']}; color: {c['accent_t']}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {c['accent_h']}; border-color: {c['accent_h']}; }}
QPushButton#primary:disabled {{ background: {c['surface2']}; border-color: {c['surface2']}; color: {c['faint']}; }}
QPushButton#danger {{ color: {c['danger']}; }}
QPushButton#danger:hover {{ background: {c['hover']}; border-color: {c['danger']}; }}
QPushButton#ghost {{ border: none; color: {c['muted']}; padding: 6px 10px; }}
QPushButton#ghost:hover {{ background: {c['hover']}; color: {c['text']}; }}
QPushButton#iconBtn {{ border: none; padding: 6px; border-radius: 8px; background: transparent; }}
QPushButton#iconBtn:hover {{ background: {c['hover']}; }}

/* ---------- поля ввода ---------- */
QLineEdit, QComboBox, QPlainTextEdit {{
    background: {c['input']}; border: 1px solid {c['border2']}; border-radius: 10px; padding: 7px 11px;
    selection-background-color: {c['sel']}; selection-color: {c['text']}; }}
QLineEdit:hover, QComboBox:hover {{ border-color: {c['faint']}; }}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {c['accent']}; }}
QLineEdit#codeInput {{ font-family: {MONO}; font-size: 15px; letter-spacing: 2px; padding: 8px 12px; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url({chev}); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{ background: {c['surface']}; border: 1px solid {c['border2']}; border-radius: 8px;
    padding: 4px; outline: none; selection-background-color: {c['surface2']}; selection-color: {c['text']}; }}
QPlainTextEdit#console {{ background: {c['code']}; border: 1px solid {c['border']}; border-radius: 12px;
    padding: 10px 12px; font-family: {MONO}; font-size: 12px; color: {c['text']}; }}

QCheckBox {{ spacing: 10px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; background: {c['input']}; border: 1.5px solid {c['border2']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; image: url({check}); }}
QRadioButton {{ spacing: 10px; }}

/* ---------- прогресс, прокрутка, подсказки, диалоги ---------- */
QProgressBar {{ background: {c['surface2']}; border: none; border-radius: 3px; max-height: 6px; min-height: 6px; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 3px; }}
QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['border2']}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c['faint']}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}
QToolTip {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border2']}; border-radius: 6px; padding: 5px 8px; }}
QMessageBox {{ background: {c['bg']}; }}
QMessageBox QLabel {{ color: {c['text']}; }}
QMenu {{ background: {c['surface']}; border: 1px solid {c['border2']}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: {c['surface2']}; }}
"""
