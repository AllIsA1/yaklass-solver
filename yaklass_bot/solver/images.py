"""Загрузка картинок задания для мультимодальных моделей."""
from __future__ import annotations

from dataclasses import dataclass

import requests

MAX_BYTES = 8 * 1024 * 1024
RASTER = ("image/png", "image/jpeg", "image/gif", "image/webp")


@dataclass
class Image:
    data: bytes
    mime: str


def download(url: str, allow_redirects: bool = True) -> Image | None:
    """None — если картинку не удалось получить или модель её не примет (напр. SVG без cairosvg)."""
    try:
        r = requests.get(url, timeout=30, allow_redirects=allow_redirects)
        r.raise_for_status()
    except requests.RequestException:
        return None
    data, mime = r.content, r.headers.get("content-type", "").split(";")[0].strip().lower()
    if mime == "image/svg+xml" or url.lower().split("?")[0].endswith(".svg"):
        try:
            import cairosvg  # необязательная зависимость
            return Image(cairosvg.svg2png(bytestring=data, output_width=800), "image/png")
        except Exception:
            return None
    if mime not in RASTER or len(data) > MAX_BYTES:
        return None
    return Image(data, mime)
