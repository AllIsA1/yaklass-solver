"""HTTP/WebSocket API для агентов."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from yaklass_bot.solver.base import SolveError

from . import security
from .db import Db, User
from .hub import Hub
from .schemas import LinkRequest, LinkResponse, MeResponse, SolveRequest, SolveResponse
from .settings import Settings
from .solver_service import SolverService

log = logging.getLogger("yaklass.api")
MAX_WS_MSG = 64_000


@dataclass
class Principal:
    tg_id: int
    agent_id: int
    user: User


def create_app(settings: Settings, db: Db, hub: Hub, solver: SolverService) -> FastAPI:
    app = FastAPI(title="yaklass-solver", docs_url=None, redoc_url=None, openapi_url=None)
    sem = asyncio.Semaphore(settings.max_concurrent_solves)
    solve_limiter = security.RateLimiter(settings.rate_per_min)
    link_limiter = security.RateLimiter(10)             # подбор кодов привязки: 10 попыток в минуту с адреса

    @app.middleware("http")
    async def limit_body(request: Request, call_next):
        size = request.headers.get("content-length")
        if size and size.isdigit() and int(size) > settings.max_body_bytes:
            return JSONResponse({"detail": "слишком большой запрос"}, status_code=413)
        return await call_next(request)

    def authenticate(token: str | None) -> Principal:
        if not token or not token.startswith(security.TOKEN_PREFIX):
            raise HTTPException(401, "нет токена")
        agent = db.agent_by_hash(security.hash_token(token))
        if agent is None or agent.revoked:
            raise HTTPException(401, "токен недействителен")
        user = db.get_user(agent.tg_id)
        if user is None or user.banned:
            raise HTTPException(403, "доступ закрыт")
        db.touch_agent(agent.id)
        return Principal(agent.tg_id, agent.id, user)

    async def principal(authorization: str | None = Header(default=None)) -> Principal:
        scheme, _, token = (authorization or "").partition(" ")
        return authenticate(token if scheme.lower() == "bearer" else None)

    def quota_of(user: User) -> int:
        return user.quota if user.quota is not None else settings.daily_quota

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "agents_online": hub.online_count()}

    @app.post("/v1/link", response_model=LinkResponse)
    async def link(req: LinkRequest, request: Request):
        ip = request.client.host if request.client else "?"
        wait = link_limiter.hit(ip)
        if wait:
            raise HTTPException(429, "слишком много попыток", headers={"Retry-After": str(int(wait) + 1)})
        code = security.normalize_code(req.code)
        tg_id = db.consume_link_code(code) if code else None
        user = db.get_user(tg_id) if tg_id else None
        if user is None or user.banned:
            raise HTTPException(400, "код неверный или истёк — получите новый командой /link в боте")
        token = security.new_token()
        db.create_agent(user.tg_id, security.hash_token(token), req.device_name or "agent", settings.max_agents_per_user)
        log.info("agent linked tg_id=%s device=%r", user.tg_id, req.device_name[:30])
        return LinkResponse(token=token, username=user.username)

    @app.get("/v1/me", response_model=MeResponse)
    async def me(p: Principal = Depends(principal)):
        return MeResponse(tg_id=p.tg_id, username=p.user.username, quota_per_day=quota_of(p.user),
                          used_today=db.used_today(p.tg_id), min_task_interval_s=settings.min_task_interval_s,
                          agent_online=hub.online(p.tg_id))

    @app.post("/v1/solve", response_model=SolveResponse)
    async def solve(req: SolveRequest, p: Principal = Depends(principal)):
        wait = solve_limiter.hit(p.tg_id)
        if wait:
            raise HTTPException(429, "слишком часто", headers={"Retry-After": str(int(wait) + 1)})
        limit = quota_of(p.user)
        ok, used = db.try_consume(p.tg_id, limit)
        if not ok:
            raise HTTPException(429, f"дневной лимит {limit} запросов исчерпан")
        task = req.task.to_task()
        if settings.log_tasks:
            log.info("solve tg_id=%s task=%r", p.tg_id, task.text[:200])
        try:
            async with sem:
                ans = await asyncio.to_thread(solver.solve, task)
        except SolveError as e:
            db.refund(p.tg_id)                         # не по вине пользователя — запрос не списываем
            raise HTTPException(502, f"модель не дала ответа: {e}") from e
        except Exception:  # noqa: BLE001
            db.refund(p.tg_id)
            log.exception("solve failed")
            raise HTTPException(500, "внутренняя ошибка решателя") from None
        return SolveResponse(answers=ans.values, confidence=ans.confidence, evidence=ans.evidence,
                             remaining_today=max(0, limit - used))

    @app.get("/v1/agent")
    async def agent_http_hint():
        """Сюда попадает запрос БЕЗ апгрейда до WebSocket — почти всегда это nginx без заголовков Upgrade."""
        return JSONResponse({"detail": "Этот адрес принимает только WebSocket. Если вы видите это в приложении — "
                                       "в nginx не переданы заголовки Upgrade/Connection (docs/SERVER.md)."},
                            status_code=426)

    @app.websocket("/v1/agent")
    async def agent_ws(ws: WebSocket):
        await ws.accept()
        try:                                           # токен — первым сообщением, не в URL (не попадёт в логи)
            first = await asyncio.wait_for(ws.receive_json(), 10)
            p = authenticate(first.get("token") if isinstance(first, dict) and first.get("type") == "auth" else None)
        except (HTTPException, asyncio.TimeoutError, ValueError, WebSocketDisconnect):
            await ws.close(code=4401)
            return
        conn = hub.connect(p.tg_id, p.agent_id, ws)
        await ws.send_json({"type": "welcome", "min_task_interval_s": settings.min_task_interval_s})
        try:
            while True:
                text = await ws.receive_text()
                if len(text) > MAX_WS_MSG:
                    await ws.close(code=1009)
                    break
                try:
                    msg = json.loads(text)
                except ValueError:
                    continue
                await hub.handle_event(p.tg_id, msg)
        except WebSocketDisconnect:
            pass
        finally:
            hub.disconnect(p.tg_id, p.agent_id, conn)

    return app
