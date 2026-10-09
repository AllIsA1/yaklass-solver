"""yaklassctl — управление сервером из командной строки (работает с той же БД)."""
from __future__ import annotations

import argparse
import os
import sys
import time

from . import security
from .db import Db
from .settings import load_dotenv


def fmt_ts(ts: int | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "—"


def table(rows: list[list], header: list[str]) -> None:
    rows = [[str(c) for c in r] for r in rows]
    w = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(header)]
    print("  ".join(h.ljust(w[i]) for i, h in enumerate(header)))
    for r in rows:
        print("  ".join(c.ljust(w[i]) for i, c in enumerate(r)))


def main(argv: list[str] | None = None) -> int:
    load_dotenv()                                      # тот же .env, что у сервера (DB_PATH)
    ap = argparse.ArgumentParser(prog="yaklassctl", description="Управление сервером yaklass-solver")
    ap.add_argument("--db", default=os.environ.get("DB_PATH", "data/server.db"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats")
    u = sub.add_parser("users").add_subparsers(dest="sub", required=True)
    u.add_parser("list")
    for name in ("add", "ban", "unban"):
        u.add_parser(name).add_argument("tg_id", type=int)
    q = u.add_parser("quota")
    q.add_argument("tg_id", type=int)
    q.add_argument("limit", help="число запросов в сутки или 'default'")
    a = sub.add_parser("agents").add_subparsers(dest="sub", required=True)
    al = a.add_parser("list")
    al.add_argument("tg_id", type=int, nargs="?")
    a.add_parser("revoke").add_argument("agent_id", type=int)
    a.add_parser("revoke-user").add_argument("tg_id", type=int)
    inv = sub.add_parser("invite").add_subparsers(dest="sub", required=True)
    ic = inv.add_parser("create")
    ic.add_argument("--uses", type=int, default=1)
    ic.add_argument("--note", default="")
    inv.add_parser("list")
    args = ap.parse_args(argv)
    db = Db(args.db)

    if args.cmd == "stats":
        for k, v in db.stats().items():
            print(f"{k}: {v}")
    elif args.cmd == "users":
        if args.sub == "list":
            table([[x.tg_id, x.username or "—", "BAN" if x.banned else "", x.quota or "default", db.used_today(x.tg_id),
                    fmt_ts(x.created_at)] for x in db.list_users()],
                  ["tg_id", "username", "", "quota", "today", "created"])
        elif args.sub == "add":
            db.upsert_user(args.tg_id)
            print("ok")
        elif args.sub in ("ban", "unban"):
            ok = db.set_banned(args.tg_id, args.sub == "ban")
            if ok and args.sub == "ban":
                db.revoke_user_agents(args.tg_id)
            print("ok" if ok else "пользователь не найден")
            return 0 if ok else 1
        elif args.sub == "quota":
            lim = None if args.limit == "default" else int(args.limit)
            ok = db.set_quota(args.tg_id, lim)
            print("ok" if ok else "пользователь не найден")
            return 0 if ok else 1
    elif args.cmd == "agents":
        if args.sub == "list":
            table([[x.id, x.tg_id, x.name, "revoked" if x.revoked else "active", fmt_ts(x.created_at), fmt_ts(x.last_seen)]
                   for x in db.list_agents(args.tg_id)], ["id", "tg_id", "name", "state", "created", "last_seen"])
        elif args.sub == "revoke":
            print("ok" if db.revoke_agent(args.agent_id) else "агент не найден")
        elif args.sub == "revoke-user":
            print(f"отозвано: {db.revoke_user_agents(args.tg_id)}")
    elif args.cmd == "invite":
        if args.sub == "create":
            code = security.new_invite()
            db.create_invite(code, args.uses, args.note)
            print(code)
        else:
            table([[r["code"], r["uses_left"], r["note"], fmt_ts(r["created_at"])] for r in db.list_invites()],
                  ["code", "uses_left", "note", "created"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
