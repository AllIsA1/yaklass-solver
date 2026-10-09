"""SQLite-хранилище. Одно соединение под блокировкой — для нагрузки в сотни пользователей достаточно."""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  tg_id INTEGER PRIMARY KEY, username TEXT, created_at INTEGER NOT NULL,
  banned INTEGER NOT NULL DEFAULT 0, quota INTEGER, note TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS link_codes(
  code TEXT PRIMARY KEY, tg_id INTEGER NOT NULL, expires_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS agents(
  id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER NOT NULL, token_hash TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, last_seen INTEGER, revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS usage(
  tg_id INTEGER NOT NULL, day TEXT NOT NULL, n INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(tg_id, day));
CREATE TABLE IF NOT EXISTS invites(
  code TEXT PRIMARY KEY, uses_left INTEGER NOT NULL, created_at INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '');
"""


def today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


@dataclass
class User:
    tg_id: int
    username: str
    created_at: int
    banned: bool
    quota: int | None
    note: str


@dataclass
class Agent:
    id: int
    tg_id: int
    name: str
    created_at: int
    last_seen: int | None
    revoked: bool


def _user(r) -> User:
    return User(r["tg_id"], r["username"] or "", r["created_at"], bool(r["banned"]), r["quota"], r["note"])


def _agent(r) -> Agent:
    return Agent(r["id"], r["tg_id"], r["name"], r["created_at"], r["last_seen"], bool(r["revoked"]))


class Db:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            if path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)

    def _q(self, sql: str, args=()):
        with self._lock, self._conn:
            return self._conn.execute(sql, args).fetchall()

    def _x(self, sql: str, args=()) -> int:
        with self._lock, self._conn:
            return self._conn.execute(sql, args).rowcount

    # ---- пользователи ----
    def upsert_user(self, tg_id: int, username: str = "") -> User:
        self._x("INSERT INTO users(tg_id, username, created_at) VALUES(?,?,?) "
                "ON CONFLICT(tg_id) DO UPDATE SET username=excluded.username", (tg_id, username or "", int(time.time())))
        return self.get_user(tg_id)  # type: ignore[return-value]

    def get_user(self, tg_id: int) -> User | None:
        r = self._q("SELECT * FROM users WHERE tg_id=?", (tg_id,))
        return _user(r[0]) if r else None

    def list_users(self) -> list[User]:
        return [_user(r) for r in self._q("SELECT * FROM users ORDER BY created_at")]

    def set_banned(self, tg_id: int, banned: bool) -> bool:
        return self._x("UPDATE users SET banned=? WHERE tg_id=?", (int(banned), tg_id)) > 0

    def set_quota(self, tg_id: int, quota: int | None) -> bool:
        return self._x("UPDATE users SET quota=? WHERE tg_id=?", (quota, tg_id)) > 0

    # ---- коды привязки ----
    def create_link_code(self, tg_id: int, code: str, ttl_s: int, now: int | None = None) -> None:
        now = int(now if now is not None else time.time())
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM link_codes WHERE expires_at < ? OR tg_id = ?", (now, tg_id))
            self._conn.execute("INSERT INTO link_codes(code, tg_id, expires_at) VALUES(?,?,?)", (code, tg_id, now + ttl_s))

    def consume_link_code(self, code: str, now: int | None = None) -> int | None:
        """Одноразово: код удаляется при любой попытке использования."""
        now = int(now if now is not None else time.time())
        with self._lock, self._conn:
            row = self._conn.execute("SELECT tg_id, expires_at FROM link_codes WHERE code=?", (code,)).fetchone()
            self._conn.execute("DELETE FROM link_codes WHERE code=?", (code,))
        return row["tg_id"] if row and row["expires_at"] >= now else None

    # ---- агенты ----
    def create_agent(self, tg_id: int, token_hash: str, name: str, max_agents: int) -> Agent:
        with self._lock, self._conn:
            self._conn.execute("INSERT INTO agents(tg_id, token_hash, name, created_at) VALUES(?,?,?,?)",
                               (tg_id, token_hash, name[:60], int(time.time())))
            old = self._conn.execute("SELECT id FROM agents WHERE tg_id=? AND revoked=0 ORDER BY id DESC LIMIT -1 OFFSET ?",
                                     (tg_id, max_agents)).fetchall()
            for r in old:                                    # лишние самые старые отзываем
                self._conn.execute("UPDATE agents SET revoked=1 WHERE id=?", (r["id"],))
        return self.agent_by_hash(token_hash)  # type: ignore[return-value]

    def agent_by_hash(self, token_hash: str) -> Agent | None:
        r = self._q("SELECT * FROM agents WHERE token_hash=?", (token_hash,))
        return _agent(r[0]) if r else None

    def touch_agent(self, agent_id: int) -> None:
        self._x("UPDATE agents SET last_seen=? WHERE id=?", (int(time.time()), agent_id))

    def list_agents(self, tg_id: int | None = None) -> list[Agent]:
        if tg_id is None:
            return [_agent(r) for r in self._q("SELECT * FROM agents ORDER BY id")]
        return [_agent(r) for r in self._q("SELECT * FROM agents WHERE tg_id=? ORDER BY id", (tg_id,))]

    def revoke_agent(self, agent_id: int) -> bool:
        return self._x("UPDATE agents SET revoked=1 WHERE id=?", (agent_id,)) > 0

    def revoke_user_agents(self, tg_id: int) -> int:
        return self._x("UPDATE agents SET revoked=1 WHERE tg_id=? AND revoked=0", (tg_id,))

    # ---- квоты ----
    def try_consume(self, tg_id: int, limit: int, day: str | None = None) -> tuple[bool, int]:
        """Атомарно: +1 к счётчику, если лимит не исчерпан. -> (успех, использовано)."""
        day = day or today()
        with self._lock, self._conn:
            r = self._conn.execute("SELECT n FROM usage WHERE tg_id=? AND day=?", (tg_id, day)).fetchone()
            n = r["n"] if r else 0
            if n >= limit:
                return False, n
            self._conn.execute("INSERT INTO usage(tg_id, day, n) VALUES(?,?,1) "
                               "ON CONFLICT(tg_id, day) DO UPDATE SET n=n+1", (tg_id, day))
            return True, n + 1

    def refund(self, tg_id: int, day: str | None = None) -> None:
        self._x("UPDATE usage SET n = MAX(0, n-1) WHERE tg_id=? AND day=?", (tg_id, day or today()))

    def used_today(self, tg_id: int, day: str | None = None) -> int:
        r = self._q("SELECT n FROM usage WHERE tg_id=? AND day=?", (tg_id, day or today()))
        return r[0]["n"] if r else 0

    # ---- приглашения ----
    def create_invite(self, code: str, uses: int = 1, note: str = "") -> None:
        self._x("INSERT INTO invites(code, uses_left, created_at, note) VALUES(?,?,?,?)", (code, uses, int(time.time()), note))

    def use_invite(self, code: str) -> bool:
        return self._x("UPDATE invites SET uses_left=uses_left-1 WHERE code=? AND uses_left>0", (code,)) > 0

    def list_invites(self) -> list[sqlite3.Row]:
        return self._q("SELECT * FROM invites ORDER BY created_at")

    def stats(self) -> dict:
        d = today()
        one = lambda sql, a=(): self._q(sql, a)[0][0]  # noqa: E731
        return {"users": one("SELECT COUNT(*) FROM users"), "banned": one("SELECT COUNT(*) FROM users WHERE banned=1"),
                "agents_active": one("SELECT COUNT(*) FROM agents WHERE revoked=0"),
                "requests_today": one("SELECT COALESCE(SUM(n),0) FROM usage WHERE day=?", (d,)),
                "active_users_today": one("SELECT COUNT(*) FROM usage WHERE day=? AND n>0", (d,))}
