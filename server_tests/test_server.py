import time

import pytest

from yaklass_server import security
from yaklass_server.db import Db
from yaklass_server.hub import CommandError, sanitize_event, validate_command
from yaklass_server.settings import Settings, load_dotenv
from yaklass_server.solver_service import image_allowed

from .conftest import TASK, auth, make_agent


# ---------- привязка ----------

def test_link_code_flow_and_single_use(env):
    user, token = make_agent(env)
    assert token.startswith("yks_")
    h = security.hash_token(token)
    assert env.db.agent_by_hash(h) and token not in str(env.db._q("SELECT * FROM agents")[0]["token_hash"])  # в БД только хеш
    code = security.new_link_code()
    env.db.create_link_code(user.tg_id, code, 600)
    assert env.client.post("/v1/link", json={"code": code}).status_code == 200
    assert env.client.post("/v1/link", json={"code": code}).status_code == 400       # одноразовый


def test_link_code_expires_and_is_normalized(env):
    env.db.upsert_user(1)
    env.db.create_link_code(1, "ABCD-EFGH", 600, now=int(time.time()) - 700)         # уже истёк
    assert env.client.post("/v1/link", json={"code": "abcd efgh"}).status_code == 400
    env.db.create_link_code(1, "ABCD-EFGH", 600)
    assert env.client.post("/v1/link", json={"code": "abcd efgh"}).status_code == 200  # регистр/пробел не важны


def test_link_bruteforce_is_rate_limited(env):
    codes = [env.client.post("/v1/link", json={"code": f"AAAA-{i:04d}".replace("0", "B")}).status_code for i in range(14)]
    assert 429 in codes and codes[0] == 400


def test_banned_user_cannot_link_or_use_token(env):
    user, token = make_agent(env)
    env.db.set_banned(user.tg_id, True)
    assert env.client.get("/v1/me", headers=auth(token)).status_code == 403
    code = security.new_link_code()
    env.db.create_link_code(user.tg_id, code, 600)
    assert env.client.post("/v1/link", json={"code": code}).status_code == 400


def test_agent_limit_revokes_oldest(env):
    env.s.max_agents_per_user = 2
    tokens = [make_agent(env)[1] for _ in range(3)]
    codes = [env.client.get("/v1/me", headers=auth(t)).status_code for t in tokens]
    assert codes == [401, 200, 200]


# ---------- авторизация ----------

@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer yks_nope"}, {"Authorization": "Basic x"},
                                     {"Authorization": "Bearer "}])
def test_requests_without_valid_token_rejected(env, headers):
    assert env.client.get("/v1/me", headers=headers).status_code == 401
    assert env.client.post("/v1/solve", json=TASK, headers=headers).status_code == 401


def test_unlink_revokes_token(env):
    user, token = make_agent(env)
    assert env.db.revoke_user_agents(user.tg_id) == 1
    assert env.client.get("/v1/me", headers=auth(token)).status_code == 401


# ---------- решение, квоты ----------

def test_solve_maps_option_index_to_value_and_counts_quota(env):
    _, token = make_agent(env)
    r = env.client.post("/v1/solve", json=TASK, headers=auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["answers"] == {"e1|dd": "a"} and body["confidence"] == 0.8 and body["remaining_today"] == 2
    assert "Москва" in env.model.prompts[0]


def test_daily_quota_enforced_and_per_user_override(env):
    user, token = make_agent(env)
    codes = [env.client.post("/v1/solve", json=TASK, headers=auth(token)).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    env.db.set_quota(user.tg_id, 5)
    assert env.client.post("/v1/solve", json=TASK, headers=auth(token)).status_code == 200


def test_quota_is_per_user(env):
    _, t1 = make_agent(env, 1, "a")
    _, t2 = make_agent(env, 2, "b")
    for _ in range(3):
        env.client.post("/v1/solve", json=TASK, headers=auth(t1))
    assert env.client.post("/v1/solve", json=TASK, headers=auth(t1)).status_code == 429
    assert env.client.post("/v1/solve", json=TASK, headers=auth(t2)).status_code == 200


def test_model_failure_is_not_charged(env):
    _, token = make_agent(env)
    env.model.fail = True
    assert env.client.post("/v1/solve", json=TASK, headers=auth(token)).status_code == 502
    env.model.fail = False
    me = env.client.get("/v1/me", headers=auth(token)).json()
    assert me["used_today"] == 0


def test_rate_limit_per_minute(env):
    env.s.rate_per_min = 2
    from yaklass_server.api import create_app
    from yaklass_server.solver_service import SolverService
    from fastapi.testclient import TestClient
    app = create_app(env.s, env.db, env.hub, SolverService(env.s, provider=env.model))
    c = TestClient(app)
    from yaklass_server import security as sec
    env.db.upsert_user(7)
    code = sec.new_link_code(); env.db.create_link_code(7, code, 60)
    tok = c.post("/v1/link", json={"code": code}).json()["token"]
    st = [c.post("/v1/solve", json=TASK, headers=auth(tok)).status_code for _ in range(3)]
    assert st == [200, 200, 429]


def test_invalid_task_payloads_rejected(env):
    _, token = make_agent(env)
    bad = [
        {"task": {"text": "x", "fields": []}},                                             # нет полей
        {"task": {"text": "x" * 20_001, "fields": TASK["task"]["fields"]}},                 # слишком длинный текст
        {"task": {"text": "x", "fields": [{"name": "a", "kind": "evil"}]}},                  # неизвестный тип поля
        {"task": {"text": "x", "fields": [{"name": "a", "kind": "text", "options": [{"value": "v"}] * 61}]}},
        {},
    ]
    for b in bad:
        assert env.client.post("/v1/solve", json=b, headers=auth(token)).status_code == 422, b
    assert env.client.post("/v1/solve", content="x" * 400_000, headers={**auth(token), "content-type": "application/json"}
                           ).status_code == 413


# ---------- безопасность картинок (SSRF) ----------

@pytest.mark.parametrize("url,ok", [
    ("https://8b08ab88.selcdn.net/x/a.png", False),            # домен подходит, но в тесте DNS недоступен/не публичный — см. ниже
    ("http://selcdn.net/a.png", False),                         # не https
    ("https://evil.com/a.png", False),                          # чужой домен
    ("https://selcdn.net.evil.com/a.png", False),               # подделка суффикса
    ("https://127.0.0.1/a.png", False),
    ("https://localhost/a.png", False),
    ("file:///etc/passwd", False),
    ("", False),
])
def test_image_allowlist_blocks_ssrf(url, ok):
    assert image_allowed(url, ("selcdn.net", "yaklass.ru")) is ok


def test_image_allowlist_accepts_allowed_public_host(monkeypatch):
    import yaklass_server.solver_service as ss
    monkeypatch.setattr(ss, "_is_public", lambda h: True)
    assert ss.image_allowed("https://abc.selcdn.net/a.png", ("selcdn.net",))
    assert ss.image_allowed("https://selcdn.net/a.png", ("selcdn.net",))


# ---------- канал управления ----------

def test_command_whitelist():
    assert validate_command({"cmd": "start_work", "work_id": "123", "mode": "dry"}) == \
        {"type": "cmd", "cmd": "start_work", "work_id": "123", "mode": "dry"}
    for bad in [{"cmd": "exec", "x": "rm -rf /"}, {"cmd": "start_work", "work_id": "https://evil"},
                {"cmd": "start_work", "work_id": "1; rm"}, {"cmd": "start_work", "work_id": "1", "mode": "x"}, {}]:
        with pytest.raises(CommandError):
            validate_command(bad)
    assert validate_command({"cmd": "stop", "url": "https://evil", "code": "x"}) == {"type": "cmd", "cmd": "stop"}  # лишнее отброшено


def test_event_sanitizing():
    assert sanitize_event({"type": "rm"}) is None
    ev = sanitize_event({"type": "works", "works": [{"id": "1", "title": "x" * 5000}] * 80, "junk": {"a": {"b": {"c": {"d": 1}}}}})
    assert len(ev["works"]) == 50 and len(ev["works"][0]["title"]) == 500 and ev["junk"]["a"]["b"]["c"] is None


def test_websocket_agent_roundtrip(env):
    user, token = make_agent(env)
    got = []

    async def notify(tg_id, ev):
        got.append((tg_id, ev))

    env.hub.notify = notify
    with env.client as client, client.websocket_connect("/v1/agent") as ws:
        ws.send_json({"type": "auth", "token": token})
        assert ws.receive_json()["type"] == "welcome"
        assert env.hub.online(user.tg_id)
        ws.send_json({"type": "hello", "device": "pc"})
        ws.send_json({"type": "status", "state": "idle"})
        ws.send_json({"type": "bogus"})                                   # неизвестное событие игнорируется
        client.portal.call(env.hub.send_command, user.tg_id, {"cmd": "list_works"})   # «бот» шлёт команду агенту
        assert ws.receive_json() == {"type": "cmd", "cmd": "list_works"}
    assert [e["type"] for _, e in got] == ["hello", "status"] and not env.hub.online(user.tg_id)


def test_websocket_rejects_bad_token(env):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with env.client.websocket_connect("/v1/agent") as ws:
            ws.send_json({"type": "auth", "token": "yks_wrong"})
            ws.receive_json()


def test_websocket_requires_auth_first(env):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with env.client.websocket_connect("/v1/agent") as ws:
            ws.send_json({"type": "hello"})
            ws.receive_json()


# ---------- настройки и БД ----------

def test_dotenv_loading_does_not_override_real_env(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text('# комментарий\nPORT=9191\nDOMAIN="a.example"\nLLM_MODEL=m # хвост\nADMIN_IDS=1, 2\nREGISTRATION=invite\n')
    monkeypatch.setenv("PORT", "7000")
    for k in ("DOMAIN", "LLM_MODEL", "ADMIN_IDS", "REGISTRATION"):
        monkeypatch.delenv(k, raising=False)
    s = Settings.from_env(str(f))
    assert s.port == 7000 and s.domain == "a.example" and s.llm_model == "m" and s.admin_ids == {1, 2}
    assert s.registration == "invite" and s.host == "127.0.0.1"


def test_invalid_settings_rejected(monkeypatch):
    monkeypatch.setenv("REGISTRATION", "anyone")
    with pytest.raises(ValueError):
        Settings.from_env("/nonexistent")


def test_db_is_shared_between_connections(tmp_path):
    """Сервер и yaklassctl — разные процессы с одной БД: изменения видны сразу."""
    a, b = Db(str(tmp_path / "s.db")), Db(str(tmp_path / "s.db"))
    a.upsert_user(5, "x")
    b.set_banned(5, True)
    assert a.get_user(5).banned
    assert a.try_consume(5, 2) == (True, 1) and b.try_consume(5, 2) == (True, 2) and a.try_consume(5, 2) == (False, 2)


# ---------- регрессия: systemd (EnvironmentFile) не вырезает комментарии в конце строки ----------

def test_inline_comments_in_environment_like_systemd(monkeypatch):
    """Реальная ошибка с сервера: DAILY_QUOTA='30          # запросов на решение…' -> ValueError."""
    for k, v in {"DAILY_QUOTA": "30          # запросов на решение в сутки на пользователя",
                 "RATE_PER_MIN": "20  # в минуту", "LLM_VISION": "false   # без картинок",
                 "ADMIN_IDS": "1, 2   # администраторы", "REGISTRATION": "invite # по приглашению",
                 "PORT": '"9000"', "LLM_API_KEY": "sk-abc#def"}.items():          # '#' внутри значения без пробела — не комментарий
        monkeypatch.setenv(k, v)
    s = Settings.from_env("/nonexistent")
    assert (s.daily_quota, s.rate_per_min, s.llm_vision, s.admin_ids, s.registration, s.port) == \
        (30, 20, False, {1, 2}, "invite", 9000)
    assert s.llm_api_key == "sk-abc#def"


def test_bad_number_message_names_the_variable(monkeypatch):
    monkeypatch.setenv("PORT", "abc")
    with pytest.raises(ValueError, match="PORT"):
        Settings.from_env("/nonexistent")


def test_shipped_env_example_loads_the_way_systemd_reads_it(monkeypatch):
    """Файл .env.example (как и .env, созданный из него) должен читаться и без нашего разбора — как у systemd."""
    from pathlib import Path
    text = (Path(__file__).parent.parent / ".env.example").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:      # systemd: комментарии только в начале строки
            k, _, v = line.partition("=")
            monkeypatch.setenv(k.strip(), v)
    s = Settings.from_env("/nonexistent")
    assert s.daily_quota == 300 and s.port == 8080 and s.registration == "open" and s.search_mode == "always"
    assert not any(" #" in l for l in text.splitlines() if l and not l.lstrip().startswith("#"))   # нет комментариев в конце строк


def test_plain_http_get_on_ws_endpoint_gives_actionable_hint(env):
    """nginx без Upgrade-заголовков превращает WebSocket в обычный GET: раньше — безликий 404."""
    r = env.client.get("/v1/agent")
    assert r.status_code == 426 and "Upgrade" in r.json()["detail"] and "nginx" in r.json()["detail"]
