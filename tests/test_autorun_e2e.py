"""Сквозной тест автономного режима в настоящем Chromium на фейковом «ЯКлассе» (страницы из samples/,
скрипты сайта грузятся с публичного CDN). Реальный сайт не затрагивается; ничего не отправляется наружу.
Проверяет: проход с 1-го по последнее, переписывание уже данного ответа («Сохранить»), завершение работы
только когда всё отправлено."""
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

pw_mod = pytest.importorskip("playwright.sync_api")

from yaklass_bot import autorun, filler  # noqa: E402
from yaklass_bot.control import RunControl  # noqa: E402
from yaklass_bot.pace import Pace  # noqa: E402
from yaklass_bot.solver import FatalSolveError  # noqa: E402
from yaklass_bot.solver.base import solve_task  # noqa: E402
from yaklass_bot.config import Config  # noqa: E402
from yaklass_bot.solver.base import Provider  # noqa: E402
from yaklass_bot.storage import tasks_table  # noqa: E402

S = Path(__file__).parent.parent / "samples"
BASE = "http://yaklass.test"


def sample(name: str) -> str:
    exact = S / (name + ".html")
    f = exact if exact.exists() else next(S.glob(name + "*.html"))
    return re.sub(r'(src|href|srcset|content)="//', r'\1="https://', f.read_text(encoding="utf-8"))


class Echo(Provider):
    """Фейковая «модель»: везде отвечает «1» (первый вариант / текст «1»)."""
    def __init__(self, broken_for=()):
        self.broken_for = broken_for

    def ask(self, prompt, images=()):
        if any(b in prompt for b in self.broken_for):
            return "не знаю"
        names = re.findall(r"^- \[\[(.+?)\]\]", prompt, re.M)
        return '{"answers": {%s}, "confidence": 0.9}' % ", ".join(f'"{n}": 1' for n in names)


def cdn_up() -> bool:
    import requests
    try:
        return requests.head("https://cdnjs.cloudflare.com", timeout=4).status_code < 500
    except requests.RequestException:
        return False


class Fake:
    """Фейковый сервер: 9 заданий, 2-е уже выполнено (кнопка «Сохранить»)."""
    def __init__(self):
        self.submits, self.completed = [], 0

    def exercise_html(self, pos: int) -> str:
        if pos == 2:
            return sample("2. Superlatives (2)")                      # отвечено ранее: #correctAnswerBtn
        if pos == 3:
            return sample("3. Параллельность")                        # поля внутри формулы
        h = sample("1. Взаимное")                                     # выпадающие списки
        return h.replace("exercisePosition=1&amp;twId", f"exercisePosition={pos}&amp;twId")

    def handle(self, route):
        req = route.request
        u = urlparse(req.url)
        q = parse_qs(u.query)
        html = None
        if u.path.startswith("/TestWorkRun/Preview/"):
            html = sample("Определение")
        elif u.path.startswith("/TestWorkRun/Start/") and req.method == "POST":
            html = self.exercise_html(1)
        elif u.path == "/TestWorkRun/Exercise" and req.method == "GET":
            html = self.exercise_html(int(q["exercisePosition"][0]))
        elif u.path == "/TestWorkRun/Exercise" and req.method == "POST":
            pos = int(q["exercisePosition"][0])
            self.submits.append((pos, req.post_data or ""))
            html = self.exercise_html(pos)
        elif u.path == "/TestWorkRun/Overview":
            html = sample("Adjectives (на 06.10) (2)")
        elif u.path == "/TestWorkRun/CompleteTest" and req.method == "POST":
            self.completed += 1
            html = "<html><body>Работа завершена</body></html>"
        if html is None:
            return route.fulfill(status=404, body="")
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)


@pytest.fixture
def env(tmp_path, monkeypatch):
    if not cdn_up():
        pytest.skip("нет доступа к CDN: скрипты сайта не загрузятся")
    monkeypatch.setattr(filler, "_pause", lambda *a, **k: None)
    with pw_mod.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"Chromium недоступен: {e}")
        ctx = browser.new_context()
        fake = Fake()
        ctx.route(f"{BASE}/**", fake.handle)
        page = ctx.new_page()
        cfg = Config()
        cfg.base_url, cfg.data_dir = BASE, tmp_path
        yield page, cfg, fake, tasks_table(tmp_path)
        browser.close()


def start(page):
    page.goto(BASE + "/TestWorkRun/Preview/100001")
    page.locator("#start-action-btn").click()
    page.wait_for_selector("#taskhtml")


FAST = Pace(between_tasks=(0, 0), before_submit=(0, 0), field_pause=(0, 0), typing_ms=(1, 2))


def run(env, provider=None, **kw):
    page, cfg, fake, table = env
    provider = provider or Echo()
    solve_fn = lambda task, log: solve_task(provider, task)  # noqa: E731
    kw.setdefault("pace", FAST)
    return autorun.execute(page, BASE, solve_fn, table, lambda m: None, **kw)


def test_full_run_answers_all_rewrites_done_task_and_completes(env):
    page, cfg, fake, table = env
    start(page)
    progress = []
    res = run(env, on_progress=lambda d, t, n: progress.append((d, t)))
    assert [o.position for o in res.outcomes] == list(range(1, 10))
    st = {o.position: o.status for o in res.outcomes}
    assert st[2] == "rewritten" and all(st[p] == "answered" for p in st if p != 2), st
    assert [p for p, _ in fake.submits] == list(range(1, 10))                    # с первого по последнее, по разу
    assert re.search(r'name="answerAction"\s+correct', dict(fake.submits)[2])    # «Сохранить», а не «Ответить»
    assert not re.search(r'name="answerAction"', dict(fake.submits)[1])          # у нового ответа такого поля нет
    assert res.completed and fake.completed == 1                                 # форма CompleteTest отправлена
    assert progress[0] == (1, 9) and progress[-1] == (9, 9)


def test_failed_task_blocks_finishing(env):
    page, cfg, fake, table = env
    start(page)
    res = run(env, Echo(broken_for=("Дан треугольник",)))
    st = {o.position: o.status for o in res.outcomes}
    assert st[3] == "failed" and "нет ответа модели" in next(o.note for o in res.outcomes if o.position == 3)
    assert not res.completed and fake.completed == 0 and "доделайте вручную" in res.note   # не завершаем с дырой
    assert 3 not in [p for p, _ in fake.submits]                                  # неотвеченное не «отправляли»


def test_resume_skips_tasks_already_sent_by_the_bot(env):
    page, cfg, fake, table = env
    start(page)
    attempt = autorun.attempt_id(autorun.parse_exercise(page.content()).form_action)
    assert attempt == re.search(r"testResultId=(\d+)", sample("1. Взаимное")).group(1)       # id попытки из формы страницы
    for pos in (1, 2):
        table.upsert({"task_key": f"{attempt}:{pos}", "work_id": attempt, "position": pos, "status": "answered"})
    res = run(env, resume=True)
    assert [o.status for o in res.outcomes[:2]] == ["skipped_done"] * 2
    assert [p for p, _ in fake.submits] == list(range(3, 10))


def test_dry_mode_fills_but_never_submits_or_finishes(env):
    page, cfg, fake, table = env
    start(page)
    res = run(env, submit=False)
    assert {o.status for o in res.outcomes} == {"filled"} and len(res.outcomes) == 9
    assert fake.submits == [] and fake.completed == 0 and not res.completed and "без отправки" in res.note
    assert table.read() == []                                                     # в «отправленные» ничего не записано


def test_stop_request_halts_after_current_task(env):
    page, cfg, fake, table = env
    start(page)
    control = RunControl()
    res = run(env, control=control, on_progress=lambda d, t, n: control.stop() if d == 2 else None)
    assert res.stopped and not res.completed and fake.completed == 0
    assert [p for p, _ in fake.submits] == [1, 2]                                 # после стопа новых отправок нет


def test_pause_blocks_until_resume(env):
    import threading
    import time
    page, cfg, fake, table = env
    start(page)
    control = RunControl()
    control.pause()
    t = threading.Timer(1.5, control.resume)
    t.start()
    t0 = time.time()
    res = run(env, control=control)
    assert time.time() - t0 >= 1.4 and res.completed                              # стояли на паузе, потом дошли до конца


def test_fatal_solver_error_aborts_run(env):
    page, cfg, fake, table = env
    start(page)
    calls = []

    def solve_fn(task, log):
        calls.append(task.position)
        if len(calls) == 3:
            raise FatalSolveError("дневной лимит исчерпан")
        return solve_task(Echo(), task)

    res = autorun.execute(page, BASE, solve_fn, table, lambda m: None, pace=FAST)
    assert res.fatal == "дневной лимит исчерпан" and not res.completed and fake.completed == 0
    assert [p for p, _ in fake.submits] == [1, 2]                                 # дальше не идём
