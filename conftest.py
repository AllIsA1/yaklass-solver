"""Тесты, использующие сохранённые страницы ЯКласса (samples/), пропускаются, если папки нет
(в репозиторий она не попадает: там данные учётной записи)."""
from pathlib import Path

ROOT = Path(__file__).parent
collect_ignore: list[str] = []
if not (ROOT / "samples").exists():
    for f in (ROOT / "tests").glob("test_*.py"):
        if "samples" in f.read_text(encoding="utf-8"):
            collect_ignore.append(str(f.relative_to(ROOT)))
