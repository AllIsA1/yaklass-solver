"""Telegram-бот (aiogram 3, long polling): привязка приложения и управление им."""
from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from yaklass_bot.stealth import GRADE_BANDS, parse_minutes

from .core import DISCLAIMER, HELP, Core, RunTimer, esc, render_event

log = logging.getLogger("yaklass.bot")


def kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in r] for r in rows])


async def say(m: Message, text: str) -> None:
    """Отвечает, только если есть что сказать (при успешной команде текст пустой)."""
    if text:
        await m.answer(text)


def build_router(core: Core) -> Router:
    r = Router()

    async def user_of(m: Message | CallbackQuery):
        u = m.from_user
        user, why = core.register(u.id, u.username or "")
        if user is None:
            return None, why
        if user.banned:
            return None, "Доступ закрыт."
        return user, ""

    @r.message(CommandStart())
    async def start(m: Message, command: CommandObject):
        invite = (command.args or "").strip() or None
        user, why = core.register(m.from_user.id, m.from_user.username or "", invite)
        if user is None:
            return await m.answer(esc(why))
        await m.answer(DISCLAIMER + "\n\n" + HELP + "\n\nНачните с /link.")

    @r.message(Command("help"))
    async def help_(m: Message):
        await m.answer(HELP)

    @r.message(Command("link"))
    async def link(m: Message):
        user, why = await user_of(m)
        if user is None:
            return await m.answer(esc(why))
        code, minutes = core.make_link_code(user)
        await m.answer(core.link_message(code, minutes))

    @r.message(Command("unlink"))
    async def unlink(m: Message):
        user, why = await user_of(m)
        if user is None:
            return await m.answer(esc(why))
        n = await core.unlink(user.tg_id)
        await m.answer(f"Отвязано приложений: {n}.")

    @r.message(Command("status"))
    async def status(m: Message):
        user, why = await user_of(m)
        if user is None:
            return await m.answer(esc(why))
        if core.hub.online(user.tg_id):
            await core.hub.send_command(user.tg_id, {"cmd": "status"})
        await m.answer(core.status_text(user))

    @r.message(Command("works"))
    async def works(m: Message):
        user, why = await user_of(m)
        if user is None:
            return await m.answer(esc(why))
        await say(m, await core.command(user.tg_id, {"cmd": "list_works"}))

    for name in ("pause", "resume", "stop"):
        def make(n):
            async def h(m: Message):
                user, why = await user_of(m)
                if user is None:
                    return await m.answer(esc(why))
                await say(m, await core.command(user.tg_id, {"cmd": n}))
            return h
        r.message.register(make(name), Command(name))

    @r.callback_query(F.data.regexp(r"^run:\d{1,12}$"))
    async def run_ask(cb: CallbackQuery):
        wid = cb.data.split(":")[1]
        await cb.message.answer(
            f"Выполнить работу <code>{wid}</code>?\n"
            "Приложение само нажмёт «Начать», ответит на задания и завершит работу. Отменить это нельзя.\n"
            "• <b>Обычно</b> — с обычными паузами;\n"
            "• <b>Быстро</b> — минимальные паузы;\n"
            "• <b>Скрытно</b> — за указанное время и на указанную оценку (часть заданий будет оставлена, "
            "решена неверно или частично).",
            reply_markup=kb([[("👤 Обычно", f"go:{wid}"), ("⚡ Быстро", f"fast:{wid}")],
                             [("🥷 Скрытно", f"st:{wid}"), ("🧪 Только заполнить", f"dry:{wid}")],
                             [("Отмена", "cancel")]]))
        await cb.answer()

    async def launch(cb: CallbackQuery, cmd: dict):
        user, why = await user_of(cb)
        if user is None:
            return await cb.answer(why, show_alert=True)
        text = await core.command(user.tg_id, cmd)
        await cb.message.edit_reply_markup(reply_markup=None)
        if text:
            await cb.message.answer(text)                  # только ошибка (приложение офлайн и т.п.)
        await cb.answer("Запускаю…" if not text else None)   # всплывающая подсказка, не сообщение в чате

    @r.callback_query(F.data.regexp(r"^(go|fast|dry):\d{1,12}$"))
    async def run_go(cb: CallbackQuery):
        kind, wid = cb.data.split(":")
        cmd = {"cmd": "start_work", "work_id": wid, "mode": "dry" if kind == "dry" else "auto",
               "style": "fast" if kind == "fast" else "normal"}
        await launch(cb, cmd)

    @r.callback_query(F.data.regexp(r"^st:\d{1,12}$"))
    async def stealth_time(cb: CallbackQuery):
        wid = cb.data.split(":")[1]
        mins = (5, 7, 10, 15, 20, 25, 30, 40)
        await cb.message.answer(
            f"🥷 Работа <code>{wid}</code>: за сколько минут выполнить?\n"
            f"Другое время (например 7:30) — командой <code>/stealth {wid} 7:30 4</code>.",
            reply_markup=kb([[(f"{m} мин", f"sm:{wid}:{m}") for m in mins[:4]],
                             [(f"{m} мин", f"sm:{wid}:{m}") for m in mins[4:]], [("Отмена", "cancel")]]))
        await cb.answer()

    @r.callback_query(F.data.regexp(r"^sm:\d{1,12}:\d{1,3}$"))
    async def stealth_grade(cb: CallbackQuery):
        _, wid, m = cb.data.split(":")
        row_ = [(f"{g} ({int(lo)}–{int(hi)}%)", f"sg:{wid}:{m}:{g}") for g, (lo, hi) in GRADE_BANDS.items()]
        await cb.message.answer(f"На какую оценку выполнить работу <code>{wid}</code> за {m} мин?",
                                reply_markup=kb([row_, [("Отмена", "cancel")]]))
        await cb.answer()

    @r.callback_query(F.data.regexp(r"^sg:\d{1,12}:\d{1,3}:[345]$"))
    async def stealth_go(cb: CallbackQuery):
        _, wid, m, g = cb.data.split(":")
        await launch(cb, {"cmd": "start_work", "work_id": wid, "mode": "auto", "style": "stealth",
                          "minutes": float(m), "grade": int(g)})

    @r.message(Command("stealth"))
    async def stealth_cmd(m: Message, command: CommandObject):
        """/stealth <номер работы> <время: 7, 7.5 или 7:30> <оценка 3-5>"""
        user, why = await user_of(m)
        if user is None:
            return await m.answer(esc(why))
        parts = (command.args or "").split()
        usage = "Формат: <code>/stealth &lt;номер работы&gt; &lt;время: 7, 7.5 или 7:30&gt; &lt;оценка 3–5&gt;</code>"
        if len(parts) != 3 or not parts[0].isdigit() or parts[2] not in ("3", "4", "5"):
            return await m.answer(usage)
        try:
            minutes = parse_minutes(parts[1])
        except ValueError as e:
            return await m.answer(f"{esc(e)}\n{usage}")
        text = await core.command(user.tg_id, {"cmd": "start_work", "work_id": parts[0], "mode": "auto",
                                               "style": "stealth", "minutes": minutes, "grade": int(parts[2])})
        await m.answer(text or "Запускаю.")

    @r.callback_query(F.data == "cancel")
    async def cancel(cb: CallbackQuery):
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.answer("Отменено")

    return r


def build_bot(core: Core) -> tuple[Bot, Dispatcher]:
    bot = Bot(core.s.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(build_router(core))
    timer = RunTimer()

    async def notify(tg_id: int, ev: dict) -> None:
        ev = timer.track(tg_id, ev)
        text, buttons = render_event(ev)
        if not text:
            return
        try:
            await bot.send_message(tg_id, text, reply_markup=kb([[b] for b in buttons]) if buttons else None)
        except Exception:  # noqa: BLE001 — пользователь мог заблокировать бота
            log.warning("не удалось отправить сообщение tg_id=%s", tg_id)

    core.hub.notify = notify
    return bot, dp
