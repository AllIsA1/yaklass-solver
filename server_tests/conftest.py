import re

import pytest
from fastapi.testclient import TestClient

from yaklass_bot.solver.base import Provider
from yaklass_server import security
from yaklass_server.api import create_app
from yaklass_server.core import Core
from yaklass_server.db import Db
from yaklass_server.hub import Hub
from yaklass_server.settings import Settings
from yaklass_server.solver_service import SolverService


class FakeModel(Provider):
    """«Модель»: на каждое поле отвечает «1» (первый вариант / текст «1»)."""
    supports_images = False

    def __init__(self):
        self.prompts = []
        self.fail = False

    def ask(self, prompt, images=()):
        self.prompts.append(prompt)
        if self.fail:
            return "не знаю"
        names = re.findall(r"^- \[\[(.+?)\]\]", prompt, re.M)
        return '{"answers": {%s}, "confidence": 0.8}' % ", ".join(f'"{n}": 1' for n in names)


TASK = {"task": {"position": 3, "title": "t", "text": "Столица России — [[e1|dd]]",
                 "fields": [{"name": "e1|dd", "kind": "dropdown",
                             "options": [{"value": "a", "text": "Москва"}, {"value": "b", "text": "Киев"}]}]}}


@pytest.fixture
def env():
    s = Settings(db_path=":memory:", daily_quota=3, rate_per_min=100, search_mode="off", llm_api_key="k")
    db, hub, model = Db(":memory:"), Hub(), FakeModel()
    app = create_app(s, db, hub, SolverService(s, provider=model))
    return type("Env", (), dict(s=s, db=db, hub=hub, model=model, core=Core(s, db, hub), app=app,
                                client=TestClient(app)))


def make_agent(env, tg_id=100, username="alice"):
    """Пользователь + привязанный агент -> токен."""
    user = env.db.upsert_user(tg_id, username)
    code = security.new_link_code()
    env.db.create_link_code(tg_id, code, 600)
    r = env.client.post("/v1/link", json={"code": code, "device_name": "pc"})
    assert r.status_code == 200, r.text
    return user, r.json()["token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}
