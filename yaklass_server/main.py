"""Запуск: API (uvicorn) + Telegram-бот в одном процессе."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import uvicorn

from .api import create_app
from .bot import TelegramBot
from .core import Core
from .db import Db
from .hub import Hub
from .mtproxy import parse_mtproxy
from .settings import Settings
from .solver_service import SolverService

log = logging.getLogger("yaklass")


async def amain() -> None:
    s = Settings.from_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db, hub = Db(s.db_path), Hub()
    core = Core(s, db, hub)
    app = create_app(s, db, hub, SolverService(s))
    server = uvicorn.Server(uvicorn.Config(app, host=s.host, port=s.port, log_level="info",
                                           proxy_headers=True, forwarded_allow_ips=s.trusted_proxy,
                                           ws_max_size=70_000))
    bot, poll = None, None
    if not s.bot_token:
        log.warning("TELEGRAM_BOT_TOKEN не задан — работает только API")
    elif not (s.telegram_api_id and s.telegram_api_hash):
        log.error("Для бота (Telethon) задайте TELEGRAM_API_ID и TELEGRAM_API_HASH (my.telegram.org → API development "
                  "tools) — пока работает только API")
    else:
        session = s.bot_session or str(Path(s.db_path).parent / "bot")
        bot = TelegramBot(core, s.telegram_api_id, s.telegram_api_hash, session, parse_mtproxy(s.mtproxy))
        poll = asyncio.create_task(_run_bot(bot))
    try:
        await server.serve()
    finally:
        if poll:
            poll.cancel()
        if bot:
            await bot.stop()


async def _run_bot(bot: TelegramBot) -> None:
    """Бот не должен ронять API: ошибка подключения к Telegram (нет сети, неверный токен) — в журнал."""
    try:
        await bot.run()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        log.exception("Telegram-бот остановлен из-за ошибки (API продолжает работать)")


def run() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    run()
