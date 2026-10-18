"""История выполненных работ: локальный файл history.json в папке данных (последние MAX запусков)."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

MAX = 300


class History:
    def __init__(self, directory: Path):
        self.path = Path(directory) / "history.json"
        self._lock = threading.Lock()

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []

    def items(self) -> list[dict]:
        """Новые сверху."""
        with self._lock:
            data = self._load()
            order = sorted(range(len(data)), key=lambda i: (data[i].get("ts", 0), i), reverse=True)   # при равном времени — позднее добавленное выше
            return [data[i] for i in order]

    def add(self, entry: dict) -> dict:
        entry = {"ts": int(time.time()), **entry}
        with self._lock:
            data = self._load()
            data.append(entry)
            self._write(data[-MAX:])
        return entry

    def clear(self) -> None:
        with self._lock:
            self._write([])

    def _write(self, data: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)


def fmt_points(v) -> str:
    """Баллы: 11 или 11,5."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "?"
    return str(int(f)) if f == int(f) else f"{f:.1f}".replace(".", ",")


def fmt_duration(sec) -> str:
    try:
        m, s = divmod(max(0, int(sec)), 60)
    except (TypeError, ValueError):
        return ""
    return f"{m} мин {s} с" if m else f"{s} с"
