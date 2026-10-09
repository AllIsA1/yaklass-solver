"""Канал управления: агенты на ПК пользователей держат исходящее WebSocket-соединение.

Протокол намеренно узкий (см. docs/PROTOCOL.md): сервер может отправить агенту только команды из
белого списка, без адресов и кода. Компрометация сервера не должна давать удалённого исполнения на ПК.
"""
from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

# команды сервер -> агент
COMMANDS = {"status", "list_works", "start_work", "pause", "resume", "stop"}
# события агент -> сервер
EVENTS = {"hello", "works", "progress", "finished", "error", "status", "ack"}
WORK_ID_RE = re.compile(r"^\d{1,12}$")
MAX_STR = 500


class CommandError(ValueError):
    pass


def validate_command(cmd: dict) -> dict:
    """Пропускает только известные команды с проверенными аргументами."""
    name = cmd.get("cmd")
    if name not in COMMANDS:
        raise CommandError(f"неизвестная команда: {name!r}")
    out: dict = {"type": "cmd", "cmd": name}
    if name == "start_work":
        wid = str(cmd.get("work_id", ""))
        if not WORK_ID_RE.match(wid):
            raise CommandError("work_id должен быть числом")
        mode = cmd.get("mode", "auto")
        if mode not in ("auto", "dry"):
            raise CommandError("mode: auto или dry")
        out.update(work_id=wid, mode=mode)
    return out


def _clip(v, depth: int = 0):
    """Обрезает строки и вложенность во входящих событиях (данные от агента недоверенные)."""
    if isinstance(v, str):
        return v[:MAX_STR]
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    if depth >= 3:
        return None
    if isinstance(v, list):
        return [_clip(x, depth + 1) for x in v[:50]]
    if isinstance(v, dict):
        return {str(k)[:40]: _clip(x, depth + 1) for k, x in list(v.items())[:30]}
    return None


def sanitize_event(msg: dict) -> dict | None:
    t = msg.get("type")
    if t not in EVENTS:
        return None
    return {"type": t, **{k: _clip(v) for k, v in msg.items() if k != "type"}}


@dataclass
class Conn:
    ws: object
    agent_id: int
    since: float = field(default_factory=time.time)


Notifier = Callable[[int, dict], Awaitable[None]]


class Hub:
    def __init__(self, notify: Notifier | None = None):
        self._conns: dict[int, dict[int, Conn]] = defaultdict(dict)    # tg_id -> {agent_id: Conn}
        self.notify = notify
        self.last_status: dict[int, dict] = {}

    # --- подключения ---
    def connect(self, tg_id: int, agent_id: int, ws) -> Conn:
        conn = Conn(ws, agent_id)
        self._conns[tg_id][agent_id] = conn       # повторное подключение того же агента заменяет старое
        return conn

    def disconnect(self, tg_id: int, agent_id: int, conn: Conn | None = None) -> None:
        cur = self._conns.get(tg_id, {}).get(agent_id)
        if cur is not None and (conn is None or cur is conn):
            del self._conns[tg_id][agent_id]
        if not self._conns.get(tg_id):
            self._conns.pop(tg_id, None)
            self.last_status.pop(tg_id, None)

    def online(self, tg_id: int) -> bool:
        return bool(self._conns.get(tg_id))

    def online_count(self) -> int:
        return sum(len(v) for v in self._conns.values())

    async def kick(self, tg_id: int) -> None:
        """Закрыть все соединения пользователя (после /unlink)."""
        for conn in list(self._conns.get(tg_id, {}).values()):
            try:
                await conn.ws.close(code=4403)
            except Exception:  # noqa: BLE001
                pass
        self._conns.pop(tg_id, None)
        self.last_status.pop(tg_id, None)

    # --- сервер -> агент ---
    async def send_command(self, tg_id: int, cmd: dict) -> int:
        """Отправляет команду всем онлайн-агентам пользователя. -> сколько агентов получили."""
        msg = validate_command(cmd)
        sent = 0
        for aid, conn in list(self._conns.get(tg_id, {}).items()):
            try:
                await conn.ws.send_json(msg)
                sent += 1
            except Exception:  # noqa: BLE001 — соединение умерло
                self.disconnect(tg_id, aid, conn)
        return sent

    # --- агент -> сервер ---
    async def handle_event(self, tg_id: int, raw: dict) -> None:
        ev = sanitize_event(raw) if isinstance(raw, dict) else None
        if ev is None:
            return
        if ev["type"] == "status":
            self.last_status[tg_id] = ev
        if self.notify is not None:
            await self.notify(tg_id, ev)
