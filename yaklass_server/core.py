"""Логика бота без привязки к Telegram (чтобы тестировать без сети)."""
from __future__ import annotations

import html
import time

from yaklass_bot.autorun import SENT, STATUS_NAMES

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
    "/stealth &lt;работа&gt; &lt;время&gt; &lt;оценка&gt; — выполнить скрытно: за заданное время на оценку 3–5\n"
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
                 f"Запусков работ сегодня: {used}/{limit}"]
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


_SENT_NAMES = {STATUS_NAMES[k] for k in SENT}
_THEORY_NAME = STATUS_NAMES["theory"]


def fmt_duration(sec: int) -> str:
    m, s = divmod(max(0, int(sec)), 60)
    return f"{m} мин {s} с" if m else f"{s} с"


def _count(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def render_finished(ev: dict) -> str:
    """«Работа N сделана за X на Z%»: Z — доля заданий с отправленным ответом среди всех, кроме теории."""
    summary = ev.get("summary") if isinstance(ev.get("summary"), dict) else {}
    done = sum(_count(v) for k, v in summary.items() if k in _SENT_NAMES)
    total = sum(_count(v) for k, v in summary.items() if k != _THEORY_NAME)
    if isinstance(ev.get("tasks_total"), int):            # с заданиями, до которых проход не дошёл
        total = max(total, ev["tasks_total"] - _count(summary.get(_THEORY_NAME)))
    wid = str(ev.get("work_id") or "")
    work = f"Работа {esc(wid)}" if wid else "Работа"
    took = f" за {fmt_duration(ev['elapsed_s'])}" if isinstance(ev.get("elapsed_s"), int) else ""
    pct = round(100 * done / total) if total else 0
    est = ev.get("est_percent")
    if isinstance(est, (int, float)) and not isinstance(est, bool):   # скрытный режим: процент — по баллам
        pct = round(est)
        if ev.get("completed"):
            return f"✅ {work} сделана{took} на ~{pct}%"
        note = str(ev.get("note") or "")
        return (f"⚠️ {work} НЕ завершена{took}: ожидаемый результат ~{pct}% — доделайте вручную"
                + (f"\n{esc(note)}" if note else ""))
    if ev.get("completed"):
        return f"✅ {work} сделана{took} на {pct}% ({done}/{total})" if total else f"✅ {work} сделана{took}"
    note = str(ev.get("note") or "")
    return (f"⚠️ {work} НЕ завершена{took}: отвечено {done}/{total} ({pct}%) — доделайте вручную"
            + (f"\n{esc(note)}" if note else ""))


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
        return "", []          # по заданиям не пишем — только итог работы
    if t == "finished":
        return render_finished(ev), []
    if t == "error":
        return f"❌ {esc(ev.get('message', 'Ошибка в приложении'))}", []
    if t == "ack":
        return ("" if ev.get("ok", True) else f"❌ {esc(ev.get('msg', 'Команда не выполнена'))}"), []
    return "", []


class RunTimer:
    """Для итогового сообщения: время работы (от статуса «running» до «finished») и число заданий в ней
    (из событий прогресса — задания, до которых проход не дошёл, в итоги приложения не попадают)."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._runs: dict[int, dict] = {}       # tg_id -> {"work_id", "start", "tasks"}

    def track(self, tg_id: int, ev: dict) -> dict:
        """Запоминает начало и число заданий; к «finished» той же работы добавляет `elapsed_s` и `tasks_total`."""
        t, wid = ev.get("type"), str(ev.get("work_id") or "")
        if t == "status" and ev.get("state") == "running" and wid:
            self._runs[tg_id] = {"work_id": wid, "start": self.clock(), "tasks": None}
        elif t == "progress" and isinstance(ev.get("total"), int):
            run = self._runs.get(tg_id)
            if run and run["work_id"] == wid:
                run["tasks"] = ev["total"]
        elif t == "finished":
            run = self._runs.pop(tg_id, None)
            if run and run["work_id"] == wid:
                extra = {"elapsed_s": int(self.clock() - run["start"])}
                if run["tasks"]:
                    extra["tasks_total"] = run["tasks"]
                return {**ev, **extra}
        return ev
        done, total = ev.get("done"), ev.get("total")
        if done is not None and total and done >= total:
            return True
        now = self.clock()
        if now - self._last.get(tg_id, -1e9) < self.every:
            return False
        self._last[tg_id] = now
        return True
