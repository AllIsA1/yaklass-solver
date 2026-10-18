"""User-Agent браузера, из которого импортируются куки: запросы идут «от имени» того же браузера.

Движок у нас всегда Chromium (так работает Playwright), поэтому строка собирается из РЕАЛЬНОЙ версии движка с
фирменной меткой выбранного браузера: Edge — «Edg/», Opera — «OPR/»; Chrome, Brave, Vivaldi и Chromium у себя
дополнительных меток не добавляют (их строка совпадает с Chrome — так и у настоящих браузеров).
Firefox и LibreWolf (движок Gecko) не имитируются: строка Firefox поверх Chromium расходится с его поведением
и заметна сайту сильнее, чем обычная; для них используется строка самого Chromium."""
from __future__ import annotations

import re
import sys

CHROMIUM_FAMILY = {"chrome", "chromium", "brave", "vivaldi", "edge", "msedge", "opera"}
DEFAULT_MAJOR = 130
OPERA_OFFSET = 15            # у Opera своя нумерация: «OPR/117» соответствует Chromium 132 (разница стабильна)


def platform_token(plat: str | None = None) -> str:
    plat = plat or sys.platform
    if plat.startswith("win"):
        return "Windows NT 10.0; Win64; x64"
    if plat == "darwin":
        return "Macintosh; Intel Mac OS X 10_15_7"
    return "X11; Linux x86_64"


def _major(version: str) -> int:
    m = re.match(r"\s*(\d+)", version or "")
    return int(m.group(1)) if m else DEFAULT_MAJOR


def supports(browser: str) -> bool:
    return (browser or "").lower() in CHROMIUM_FAMILY


def build_user_agent(browser: str, engine_version: str = "", plat: str | None = None) -> str:
    """'' — если для этого браузера строку собрать нельзя (тогда остаётся строка самого движка)."""
    b = (browser or "").lower()
    if b not in CHROMIUM_FAMILY:
        return ""
    major = _major(engine_version)
    ua = (f"Mozilla/5.0 ({platform_token(plat)}) AppleWebKit/537.36 (KHTML, like Gecko) "
          f"Chrome/{major}.0.0.0 Safari/537.36")
    if b in ("edge", "msedge"):
        ua += f" Edg/{major}.0.0.0"
    elif b == "opera":
        ua += f" OPR/{max(major - OPERA_OFFSET, 1)}.0.0.0"
    return ua
