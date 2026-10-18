"""Логика Telegram-бота без привязки к библиотеке: команды и нажатия кнопок -> ответы.
Обвязка на Telethon — в bot.py; здесь всё проверяется тестами без сети."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from yaklass_bot.stealth import GRADE_BANDS, parse_minutes

from .core import DISCLAIMER, HELP, Core, esc

Buttons = list[list[tuple[str, str]]]          # строки кнопок: (подпись, callback_data)


@dataclass
class Reply:
    text: str
    buttons: Buttons | None = None


@dataclass
class Ctx:
    tg_id: int
    username: str = ""
    access_hash: int | None = None             # адрес пользователя для Telegram (MTProto), если известен


@dataclass
class CbResult:
    answer: str | None = None                  # всплывающая подсказка
    alert: bool = False                        # показать подсказку окном
    clear: bool = False                        # убрать кнопки у сообщения
    replies: list[Reply] = field(default_factory=list)


CALLBACKS = [
    (re.compile(r"^run:(\d{1,12})$"), "run_ask"),
    (re.compile(r"^(go|fast|dry):(\d{1,12})$"), "run_go"),
    (re.compile(r"^st:(\d{1,12})$"), "stealth_time"),
    (re.compile(r"^sm:(\d{1,12}):(\d{1,3})$"), "stealth_grade"),
    (re.compile(r"^sg:(\d{1,12}):(\d{1,3}):([345])$"), "stealth_go"),
    (re.compile(r"^cancel$"), "cancel"),
]
SIMPLE = {"pause", "resume", "stop"}


class BotLogic:
    def __init__(self, core: Core):
        self.core = core

    async def user_of(self, ctx: Ctx):
        user, why = self.core.register(ctx.tg_id, ctx.username)
        if user is None:
            return None, why
        if user.banned:
            return None, "Доступ закрыт."
        return user, ""

    # ------------------------------------------------------------ команды
    async def command(self, ctx: Ctx, name: str, args: str = "") -> list[Reply]:
        name = name.lower()
        if name == "start":
            invite = args.strip() or None
            user, why = self.core.register(ctx.tg_id, ctx.username, invite)
            if user is None:
                return [Reply(esc(why))]
            return [Reply(DISCLAIMER + "\n\n" + HELP + "\n\nНачните с /link.")]
        if name == "help":
            return [Reply(HELP)]
        handler = {"link": self._link, "unlink": self._unlink, "status": self._status, "works": self._works,
                   "stealth": self._stealth}.get(name)
        if handler is None and name not in SIMPLE:
            return []
        user, why = await self.user_of(ctx)
        if user is None:
            return [Reply(esc(why))]
        if name in SIMPLE:
            return self._say(await self.core.command(user.tg_id, {"cmd": name}))
        return await handler(user, args)

    @staticmethod
    def _say(text: str) -> list[Reply]:
        """Отвечает, только если есть что сказать (при успешной команде текст пустой)."""
        return [Reply(text)] if text else []

    async def _link(self, user, args):
        code, minutes = self.core.make_link_code(user)
        return [Reply(self.core.link_message(code, minutes))]

    async def _unlink(self, user, args):
        return [Reply(f"Отвязано приложений: {await self.core.unlink(user.tg_id)}.")]

    async def _status(self, user, args):
        if self.core.hub.online(user.tg_id):
            await self.core.hub.send_command(user.tg_id, {"cmd": "status"})
        return [Reply(self.core.status_text(user))]

    async def _works(self, user, args):
        return self._say(await self.core.command(user.tg_id, {"cmd": "list_works"}))

    async def _stealth(self, user, args):
        """/stealth <номер работы> <время: 7, 7.5 или 7:30> <оценка 3-5>"""
        parts = args.split()
        usage = "Формат: <code>/stealth &lt;номер работы&gt; &lt;время: 7, 7.5 или 7:30&gt; &lt;оценка 3–5&gt;</code>"
        if len(parts) != 3 or not parts[0].isdigit() or parts[2] not in ("3", "4", "5"):
            return [Reply(usage)]
        try:
            minutes = parse_minutes(parts[1])
        except ValueError as e:
            return [Reply(f"{esc(e)}\n{usage}")]
        text = await self.core.command(user.tg_id, {"cmd": "start_work", "work_id": parts[0], "mode": "auto",
                                                    "style": "stealth", "minutes": minutes, "grade": int(parts[2])})
        return [Reply(text or "Запускаю.")]

    # ------------------------------------------------------------ кнопки
    async def callback(self, ctx: Ctx, data: str) -> CbResult:
        for rx, name in CALLBACKS:
            m = rx.match(data)
            if m:
                return await getattr(self, "_cb_" + name)(ctx, *m.groups())
        return CbResult()

    async def _cb_run_ask(self, ctx, wid):
        return CbResult(replies=[Reply(
            f"Выполнить работу <code>{wid}</code>?\n"
            "Приложение само нажмёт «Начать», ответит на задания и завершит работу. Отменить это нельзя.\n"
            "• <b>Обычно</b> — с обычными паузами;\n"
            "• <b>Быстро</b> — минимальные паузы;\n"
            "• <b>Скрытно</b> — за указанное время и на указанную оценку (часть заданий будет оставлена, "
            "решена неверно или частично).",
            [[("👤 Обычно", f"go:{wid}"), ("⚡ Быстро", f"fast:{wid}")],
             [("🥷 Скрытно", f"st:{wid}"), ("🧪 Только заполнить", f"dry:{wid}")],
             [("Отмена", "cancel")]])])

    async def _launch(self, ctx: Ctx, cmd: dict) -> CbResult:
        user, why = await self.user_of(ctx)
        if user is None:
            return CbResult(answer=why, alert=True)
        text = await self.core.command(user.tg_id, cmd)
        # при ошибке (приложение офлайн и т.п.) — сообщение; при успехе — только всплывающая подсказка
        return CbResult(answer=None if text else "Запускаю…", clear=True, replies=[Reply(text)] if text else [])

    async def _cb_run_go(self, ctx, kind, wid):
        return await self._launch(ctx, {"cmd": "start_work", "work_id": wid, "mode": "dry" if kind == "dry" else "auto",
                                        "style": "fast" if kind == "fast" else "normal"})

    async def _cb_stealth_time(self, ctx, wid):
        mins = (5, 7, 10, 15, 20, 25, 30, 40)
        return CbResult(replies=[Reply(
            f"🥷 Работа <code>{wid}</code>: за сколько минут выполнить?\n"
            f"Другое время (например 7:30) — командой <code>/stealth {wid} 7:30 4</code>.",
            [[(f"{m} мин", f"sm:{wid}:{m}") for m in mins[:4]],
             [(f"{m} мин", f"sm:{wid}:{m}") for m in mins[4:]], [("Отмена", "cancel")]])])

    async def _cb_stealth_grade(self, ctx, wid, m):
        row = [(f"{g} ({int(lo)}–{int(hi)}%)", f"sg:{wid}:{m}:{g}") for g, (lo, hi) in GRADE_BANDS.items()]
        return CbResult(replies=[Reply(f"На какую оценку выполнить работу <code>{wid}</code> за {m} мин?",
                                       [row, [("Отмена", "cancel")]])])

    async def _cb_stealth_go(self, ctx, wid, m, g):
        return await self._launch(ctx, {"cmd": "start_work", "work_id": wid, "mode": "auto", "style": "stealth",
                                        "minutes": float(m), "grade": int(g)})

    async def _cb_cancel(self, ctx):
        return CbResult(answer="Отменено", clear=True)
