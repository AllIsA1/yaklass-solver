"""Присутствие на странице: окно свёрнуто / курсор ушёл — сайт этого не видит, курсор «на задании»."""
import threading
from pathlib import Path

import pytest

from yaklass_bot import control
from yaklass_bot.presence import PRESENCE_JS, Presence, current_call

ROOT = Path(__file__).resolve().parents[1]


def test_extension_page_hook_carries_same_presence_script():
    hook = (ROOT / "extension/src/page-hook.js").read_text(encoding="utf-8")
    assert PRESENCE_JS.strip() in hook, "extension/src/page-hook.js отстал от yaklass_bot/presence.py"
    assert hook.index("__ykPresenceGated = true") < hook.index(PRESENCE_JS.strip()[:40])


class FakeMouse:
    def __init__(self):
        self.moves, self.threads = [], set()

    def move(self, x, y, steps=1):
        self.moves.append((x, y))
        self.threads.add(threading.get_ident())


class FakePage:
    def __init__(self, last_move=None):
        self.mouse = FakeMouse()
        self.last_move = last_move

    def evaluate(self, js):
        if "__ykLastMove" in js:
            return self.last_move
        return [100, 100, 700, 500]


def test_cursor_is_left_alone_while_user_moves_mouse_over_page():
    page = FakePage(last_move=[400, 300, 500])                # человек двигал мышью полсекунды назад
    p = Presence(page)
    p.nudge()
    assert page.mouse.moves == [] and p.skipped == 1
    page.last_move = [400, 300, 5000]                        # мышь давно не двигали — снова «водим»
    p.nudge()
    assert len(page.mouse.moves) == 1
    x, y = p.pos
    page.last_move = [x, y, 200]                             # последнее движение — наше же
    p.nudge()
    assert p.skipped == 1


def test_idle_sleep_moves_cursor_inside_task_area():
    page = FakePage()
    with Presence(page) as p:
        control.sleep(None, 1.5)
        control.RunControl().sleep(1.0)
    assert p.moves >= 2
    assert all(100 <= x <= 700 and 100 <= y <= 500 for x, y in page.mouse.moves[-4:])
    assert control.idle_hook() is None                       # хук снят после выхода


def test_solve_runs_in_worker_while_cursor_moves_in_owner_thread():
    page = FakePage()
    seen = {}

    def slow():
        seen["thread"] = threading.get_ident()
        import time
        time.sleep(2.4)                                      # дольше самого длинного интервала (2.2 с)
        return 42

    with Presence(page):
        assert current_call(slow) == 42
    assert seen["thread"] != threading.get_ident()
    assert page.mouse.threads == {threading.get_ident()}     # Playwright sync — только свой поток
    assert len(page.mouse.moves) >= 3                        # первый сдвиг — 1 вызов, следующие — по 2


def test_errors_from_solver_propagate():
    with Presence(FakePage()):
        with pytest.raises(ValueError):
            current_call(lambda: (_ for _ in ()).throw(ValueError("x")))
    assert current_call(lambda: 7) == 7                      # без присутствия — просто вызов


HTML = """<html><body><div id=taskhtml style='width:600px;height:400px;margin:40px'>t</div><script>
window.log=[]; window.tr=0;
document.addEventListener('visibilitychange',()=>log.push('vis'));
window.addEventListener('blur',()=>log.push('blur'));
document.addEventListener('mouseleave',()=>log.push('leave'));
document.addEventListener('mouseout',e=>{ if(!e.relatedTarget) log.push('out') });
document.addEventListener('mousemove',e=>{ if(e.isTrusted) tr++ });
</script></body></html>"""


def _browser():
    pw_mod = pytest.importorskip("playwright.sync_api")
    return pw_mod.sync_playwright


def test_page_does_not_see_leaving_and_gets_trusted_moves():
    sync_playwright = _browser()
    from yaklass_bot.presence import install
    try:
        pw = sync_playwright().start()
        b = pw.chromium.launch(headless=True)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"нет Chromium Playwright: {e}")
    try:
        ctx = b.new_context()
        install(ctx)
        page = ctx.new_page()
        page.route("http://presence.test/", lambda r: r.fulfill(body=HTML, content_type="text/html"))
        page.goto("http://presence.test/")
        page.evaluate("""()=>{document.dispatchEvent(new Event('visibilitychange'));
            window.dispatchEvent(new FocusEvent('blur'));
            document.dispatchEvent(new MouseEvent('mouseleave'));
            document.body.dispatchEvent(new MouseEvent('mouseout',{bubbles:true,relatedTarget:null}));}""")
        with Presence(page) as p:
            control.sleep(None, 2.5)
            page.mouse.move(20, 20)                          # «человек» повёл мышью — эмуляция уступает
            moves = p.moves
            p.nudge()
            assert p.moves == moves and p.skipped == 1
        st = page.evaluate("""()=>({log, tr, hidden: document.hidden, vis: document.visibilityState,
            focus: document.hasFocus(), src: String(Object.getOwnPropertyDescriptor(Document.prototype,'hidden').get)})""")
        assert st["log"] == []
        assert st["hidden"] is False and st["vis"] == "visible" and st["focus"] is True
        assert "[native code]" in st["src"]
        assert st["tr"] > 0 and p.moves >= 2
    finally:
        b.close()
        pw.stop()
