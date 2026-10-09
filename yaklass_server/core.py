"""Логика бота без привязки к Telegram (чтобы тестировать без сети)."""
from __future__ import annotations

import html
import time

from . import security
from .db import Db, User
from .hub import CommandError, Hub
from .settings import Settings

DISCLAIMER = (
    "⚠️ <b>Важно.</b> Автоматическое выполнение работ нарушает правила ЯКласса: ваш аккаунт могут "
    "ограничить, а учитель — заметить. Вы используете это на свой риск и ответственность.\n"
    "Куки и пароль <b>никогда</b> не отправляйте боту: приложение работает на вашем компьютере, "
    "сервер вашего аккаунта не видит."
)

HELP = (
    "<b>Команды</b>\n"
    "/link — код для привязки приложения (действует 10 минут)\n"
    "/unlink — отвязать все приложения\n"
    "/status — состояние приложения и лимит\n"
    "/works — новые работы\n"
    "/pause, /resume, /stop — управление выполнением"
)


def esc(v) -> str:
    return html.escape(str(v), quote=False)


class Core:
    def __init__(self, s: Settings, db: Db, hub: Hub):
        self.s, self.db, self.hub = s, db, hub

    def is_admin(self, tg_id: int) -> bool:
        return tg_id in self.s.admin_ids

    def register(self, tg_id: int, username: str, invite: str | None = None) -> tuple[User | None, str]:
        """-> (пользователь, причина отказа). Политика: open / invite / closed (админы проходят всегда)."""
        existing = self.db.get_user(tg_id)
        if existing:
            if username != existing.username:
                self.db.upsert_user(tg_id, username)
            return self.db.get_user(tg_id), ""
        if not self.is_admin(tg_id):
            if self.s.registration == "closed":
                return None, "Регистрация закрыта."
            if self.s.registration == "invite" and not (invite and self.db.use_invite(invite)):
                return None, "Нужно приглашение: откройте бота по ссылке-приглашению или отправьте /start КОД."
        return self.db.upsert_user(tg_id, username), ""

    def make_link_code(self, user: User) -> tuple[str, int]:
        code = security.new_link_code()
        self.db.create_link_code(user.tg_id, code, self.s.link_code_ttl_s)
        return code, self.s.link_code_ttl_s // 60

    def link_message(self, code: str, minutes: int) -> str:
        """Сначала адрес сервера, затем код, затем пояснения (срок действия и конфиденциальность)."""
        return (f"<b>Адрес сервера:</b>\n<code>{esc(self.s.public_url)}</code>\n\n"
                f"<b>Код привязки:</b>\n<code>{esc(code)}</code>\n\n"
                f"Введите адрес и код в приложении на компьютере. Код действует {minutes} мин и одноразовый.\n"
                "🔒 Никому не передавайте код: с ним можно привязать приложение к вашему аккаунту.")

    async def unlink(self, tg_id: int) -> int:
        n = self.db.revoke_user_agents(tg_id)
        await self.hub.kick(tg_id)
        return n

    def status_text(self, user: User) -> str:
        limit = user.quota if user.quota is not None else self.s.daily_quota
        used = self.db.used_today(user.tg_id)
        online = self.hub.online(user.tg_id)
        lines = [f"Приложение: {'🟢 онлайн' if online else '🔴 не запущено'}",
                 f"Запросов сегодня: {used}/{limit}"]
        st = self.hub.last_status.get(user.tg_id)
        if st:
            lines.append(f"Состояние: {esc(st.get('state', '?'))}"
                         + (f", работа {esc(st['work_id'])}" if st.get("work_id") else ""))
        if not online:
            lines.append("Запустите приложение на компьютере — оно подключится само.")
        return "\n".join(lines)

    async def command(self, tg_id: int, cmd: dict) -> str:
        """Команда агенту. -> текст для пользователя ТОЛЬКО при проблеме; при успехе пусто (подтверждения не нужны)."""
        if not self.hub.online(tg_id):
            return "Приложение не запущено. Откройте его на компьютере и повторите."
        try:
            n = await self.hub.send_command(tg_id, cmd)
        except CommandError as e:
            return f"Некорректная команда: {esc(e)}"
        return "" if n else "Приложение не отвечает. Проверьте, что оно запущено."


def render_event(ev: dict) -> tuple[str, list[tuple[str, str]]]:
    """Событие агента -> (текст HTML, кнопки [(подпись, callback_data)])."""
    t = ev.get("type")
    if t in ("hello", "status"):
        return "", []          # подключение и состояние — только по запросу /status, отдельных сообщений нет
    if t == "works":
        works = ev.get("works") or []
        head = "🆕 <b>Новые работы</b>" if ev.get("new") else "<b>Работы</b>"
        if not works:
            return "Новых работ нет.", []
        lines, buttons = [head], []
        for w in works[:10]:
            wid = str(w.get("id", ""))
            lines.append(f"• {esc(w.get('subject', ''))} — {esc(w.get('title', ''))}"
                         + (f" (до {esc(w['deadline'])})" if w.get("deadline") else ""))
            if wid.isdigit():
                buttons.append((f"▶ {str(w.get('title', wid))[:40]}", f"run:{wid}"))
        return "\n".join(lines), buttons
    if t == "progress":
        done, total = ev.get("done"), ev.get("total")
        bar = f"{done}/{total}" if done is not None and total else ""
        return f"⏳ Выполнение {bar} {esc(ev.get('note', ''))}".strip(), []
    if t == "finished":
        s = ev.get("summary") or {}
        ok = ev.get("completed")
        parts = ", ".join(f"{esc(k)}: {esc(v)}" for k, v in s.items())
        return (("✅ Работа завершена" if ok else "⚠️ Работа НЕ завершена (нужно доделать вручную)")
                + (f"\n{parts}" if parts else "")), []
    if t == "error":
        return f"❌ {esc(ev.get('message', 'Ошибка в приложении'))}", []
    if t == "ack":
        return ("" if ev.get("ok", True) else f"❌ {esc(ev.get('msg', 'Команда не выполнена'))}"), []
    return "", []


class ProgressThrottle:
    """Не чаще одного сообщения о прогрессе раз в `every_s` секунд на пользователя (финальное — всегда)."""

    def __init__(self, every_s: float = 15.0, clock=time.monotonic):
        self.every, self.clock = every_s, clock
        self._last: dict[int, float] = {}

    def allow(self, tg_id: int, ev: dict) -> bool:
        if ev.get("type") != "progress":
            return True
        done, total = ev.get("done"), ev.get("total")
        if done is not None and total and done >= total:
            return True
        now = self.clock()
        if now - self._last.get(tg_id, -1e9) < self.every:
            return False
        self._last[tg_id] = now
        return True
