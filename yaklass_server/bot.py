"""Telegram-бот (aiogram 3, long polling): привязка приложения и управление им."""
from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .core import DISCLAIMER, HELP, Core, ProgressThrottle, esc, render_event

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
            "<b>Автономно</b>: приложение само нажмёт «Начать», ответит на все задания и завершит работу. "
            "Отменить это нельзя.",
            reply_markup=kb([[("✅ Автономно", f"go:{wid}"), ("🧪 Только заполнить", f"dry:{wid}")],
                             [("Отмена", "cancel")]]))
        await cb.answer()

    @r.callback_query(F.data.regexp(r"^(go|dry):\d{1,12}$"))
    async def run_go(cb: CallbackQuery):
        user, why = await user_of(cb)
        if user is None:
            return await cb.answer(why, show_alert=True)
        kind, wid = cb.data.split(":")
        text = await core.command(user.tg_id, {"cmd": "start_work", "work_id": wid, "mode": "auto" if kind == "go" else "dry"})
        await cb.message.edit_reply_markup(reply_markup=None)
        if text:
            await cb.message.answer(text)                  # только ошибка (приложение офлайн и т.п.)
        await cb.answer("Запускаю…" if not text else None)   # всплывающая подсказка, не сообщение в чате

    @r.callback_query(F.data == "cancel")
    async def cancel(cb: CallbackQuery):
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.answer("Отменено")

    return r


def build_bot(core: Core) -> tuple[Bot, Dispatcher]:
    bot = Bot(core.s.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(build_router(core))
    throttle = ProgressThrottle()

    async def notify(tg_id: int, ev: dict) -> None:
        if not throttle.allow(tg_id, ev):
            return
        text, buttons = render_event(ev)
        if not text:
            return
        try:
            await bot.send_message(tg_id, text, reply_markup=kb([[b] for b in buttons]) if buttons else None)
        except Exception:  # noqa: BLE001 — пользователь мог заблокировать бота
            log.warning("не удалось отправить сообщение tg_id=%s", tg_id)

    core.hub.notify = notify
    return bot, dp
