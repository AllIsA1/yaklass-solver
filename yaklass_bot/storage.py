"""Хранилище на CSV. Запись атомарная: временный файл + rename."""
from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path


class CsvTable:
    def __init__(self, path: Path, columns: list[str], key: str):
        self.path, self.columns, self.key = path, columns, key

    def read(self) -> list[dict[str, str]]:
        if not self.path.exists():
            return []
        with self.path.open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def _write(self, rows: list[dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=self.columns, extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def get(self, key: str) -> dict[str, str] | None:
        return next((r for r in self.read() if r[self.key] == key), None)

    def upsert(self, row: dict) -> None:
        row = {c: str(row.get(c, "")) for c in self.columns}
        rows = self.read()
        for i, r in enumerate(rows):
            if r[self.key] == row[self.key]:
                rows[i] = {**r, **{k: v for k, v in row.items() if v != ""}}
                break
        else:
            rows.append(row)
        self._write(rows)


def works_table(data_dir: Path) -> CsvTable:
    return CsvTable(data_dir / "works.csv",
                    ["work_id", "subject", "title", "deadline_utc_ms", "status", "first_seen"],
                    key="work_id")


def tasks_table(data_dir: Path) -> CsvTable:
    return CsvTable(data_dir / "tasks.csv",
                    ["task_key", "work_id", "position", "status", "answer_json", "confidence", "spent_s"],
                    key="task_key")
