"""Запуск: API (uvicorn) + Telegram-бот в одном процессе."""
from __future__ import annotations

import asyncio
import logging

import uvicorn

from .api import create_app
from .bot import build_bot
from .core import Core
from .db import Db
from .hub import Hub
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
    poll = None
    if s.bot_token:
        bot, dp = build_bot(core)
        poll = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
        log.info("Telegram-бот запущен")
    else:
        log.warning("TELEGRAM_BOT_TOKEN не задан — работает только API")
    try:
        await server.serve()
    finally:
        if poll:
            poll.cancel()
            await bot.session.close()


def run() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    run()
