from __future__ import annotations

import random
import time
from datetime import datetime, timezone

from .config import Config
from .notify import Notifier
from .parser import parse_works_list
from .session import fetch, make_session
from .storage import works_table


def check_new_works(cfg: Config, notifier: Notifier, session=None) -> int:
    """Один проход: тянет «Новые работы», сообщает о ранее не виденных. Возвращает их число."""
    s = session or make_session(cfg)
    works = parse_works_list(fetch(s, cfg, "/TestWork"))
    table = works_table(cfg.data_dir)
    known = {r["work_id"] for r in table.read()}
    new = 0
    for w in works:
        if w.work_id in known:
            continue
        new += 1
        deadline = (datetime.fromtimestamp(w.deadline_utc_ms / 1000, timezone.utc)
                    .astimezone().strftime("%d.%m %H:%M") if w.deadline_utc_ms else "—")
        table.upsert({"work_id": w.work_id, "subject": w.subject, "title": w.title,
                      "deadline_utc_ms": w.deadline_utc_ms or "", "status": "new",
                      "first_seen": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        notifier.send(f"Новая работа: {w.subject} — «{w.title}», до {deadline} (id {w.work_id})")
    return new


def watch(cfg: Config, notifier: Notifier) -> None:
    s = make_session(cfg)
    while True:
        check_new_works(cfg, notifier, s)
        delay = cfg.interval_min + random.uniform(-cfg.jitter_min, cfg.jitter_min)
        time.sleep(max(1.0, delay) * 60)
