import asyncio
import re
import socket
import threading
import time

import pytest
import uvicorn

from yaklass_app.agent import Agent
from yaklass_app.backend import Backend, NotLoggedIn
from yaklass_app.settings import AppSettings, Store
from yaklass_bot.autorun import Outcome, RunResult
from yaklass_bot.models import Field, Option, Task, Work
from yaklass_bot.solver.base import Provider
from yaklass_server import security
from yaklass_server.api import create_app
from yaklass_server.db import Db
from yaklass_server.hub import Hub
from yaklass_server.settings import Settings
from yaklass_server.solver_service import SolverService


class FakeModel(Provider):
    supports_images = False

    def __init__(self):
        self.prompts = []

    def ask(self, prompt, images=()):
        self.prompts.append(prompt)
        names = re.findall(r"^- \[\[(.+?)\]\]", prompt, re.M)
        return '{"answers": {%s}, "confidence": 0.7}' % ", ".join(f'"{n}": 1' for n in names)


def wait_for(cond, timeout=8.0, step=0.02):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return False


class RealServer:
    """Настоящий сервер (uvicorn в потоке) с фейковой моделью; события агентов собираются в self.events."""

    def __init__(self, quota=100):
        s = Settings(db_path=":memory:", daily_quota=quota, rate_per_min=1000, search_mode="off", llm_api_key="k")
        self.s, self.db, self.hub, self.model = s, Db(":memory:"), Hub(), FakeModel()
        self.events: list[tuple[int, dict]] = []

        async def collect(tg_id, ev):
            self.events.append((tg_id, ev))

        self.hub.notify = collect
        app = create_app(s, self.db, self.hub, SolverService(s, provider=self.model))
        with socket.socket() as sk:
            sk.bind(("127.0.0.1", 0))
            self.port = sk.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=lambda: self.loop.run_until_complete(self.server.serve()), daemon=True)
        self.thread.start()
        assert wait_for(lambda: self.server.started, 10)

    def new_code(self, tg_id=1, username="alice") -> str:
        self.db.upsert_user(tg_id, username)
        code = security.new_link_code()
        self.db.create_link_code(tg_id, code, 600)
        return code

    def command(self, tg_id, cmd):
        return asyncio.run_coroutine_threadsafe(self.hub.send_command(tg_id, cmd), self.loop).result(5)

    def events_of(self, kind):
        return [e for _, e in self.events if e.get("type") == kind]

    def stop(self):
        self.server.should_exit = True
        self.thread.join(5)


class FakeBackend(Backend):
    """Браузера нет: «выполняет работу», вызывая настоящий решатель на синтетических заданиях."""

    def __init__(self):
        self.logged_in = True
        self.runs: list[tuple] = []
        self.tasks = 3
        self.list_error: Exception | None = None
        self.login_error: Exception | None = None
        self.login_calls = 0

    def list_works(self):
        if self.list_error:
            raise self.list_error
        if not self.logged_in:
            raise NotLoggedIn("нет входа")
        return [Work("111", "Алгебра", "Тема 1", 1_900_000_000_000, "/TestWorkRun/Preview/111"),
                Work("222", "Геометрия", "Тема 2", None, "/TestWorkRun/Preview/222")]

    def run_work(self, work_id, mode, solve_fn, pace, control, log, on_progress):
        self.runs.append((work_id, mode, pace))
        res = RunResult()
        from yaklass_bot.control import Stopped
        from yaklass_bot.solver import FatalSolveError
        try:
            for i in range(1, self.tasks + 1):
                control.checkpoint()
                t = Task(position=i, title="t", points=1, text=f"Вопрос {i}: [[f{i}|dd]]", images=[],
                         fields=[Field(f"f{i}|dd", "dropdown", options=[Option("a", "А"), Option("b", "Б")])])
                ans = solve_fn(t, log)
                res.outcomes.append(Outcome(i, "t", "answered" if mode == "auto" else "filled", confidence=ans.confidence))
                on_progress(i, self.tasks, f"задание {i}")
                control.sleep(0.15)
            res.completed = mode == "auto"
        except Stopped:
            res.stopped, res.note = True, "остановлено пользователем"
        except FatalSolveError as e:
            res.fatal, res.note = str(e), str(e)
        return res

    def login(self):
        self.login_calls += 1
        if self.login_error:
            raise self.login_error
        self.logged_in = True


@pytest.fixture
def server():
    srv = RealServer()
    yield srv
    srv.stop()


@pytest.fixture
def setup(tmp_path, server):
    store = Store(tmp_path / "cfg", use_keyring=False)
    s = AppSettings(server_url=server.url, device_name="test-pc", poll_minutes=1)
    store.save(s)
    backend = FakeBackend()
    events = []
    agent = Agent(store, backend, emit=lambda n, p: events.append((n, p)), directory=tmp_path / "data")
    yield type("S", (), dict(server=server, store=store, backend=backend, agent=agent, events=events, tmp=tmp_path))
    agent.shutdown()


def link_and_start(env, tg_id=1):
    code = env.server.new_code(tg_id)
    env.agent.link(code)
    env.agent.start()
    assert wait_for(lambda: env.agent.connected), "агент не подключился к серверу"
