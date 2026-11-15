"""«Присутствие» на странице задания: окно браузера можно свернуть или увести курсор — работа продолжается.

ЯКласс перестал показывать задания, если вкладка не на экране или курсор ушёл со страницы. Две части:

1. PRESENCE_JS — скрипт, который ставится в каждую страницу до скриптов сайта (Playwright: add_init_script).
   Страница всегда «видна» и «в фокусе» (document.hidden / visibilityState / hasFocus), события ухода
   (visibilitychange, blur окна, mouseleave/mouseout за пределы окна, pagehide-заморозка) до сайта не доходят,
   а requestAnimationFrame продолжает работать и в свёрнутом окне.
2. Presence — движение курсора по области задания настоящими (isTrusted) событиями через CDP (page.mouse).
   Синтетические события из JS сайт может отличить — поэтому их не шлём. Курсор двигается во всех ожиданиях
   (паузы темпа, расписание скрытного режима) и пока модель решает задание: решение идёт в отдельном потоке,
   а поток Playwright тем временем «водит мышью». Свёрнутое окно этому не мешает: события уходят прямо во вкладку.
"""
from __future__ import annotations

import math
import random
import threading
import time
from collections.abc import Callable
from typing import TypeVar

from . import control as _control

T = TypeVar("T")

PRESENCE_JS = r"""
(() => {
  if (window.__ykPresence) return;
  Object.defineProperty(window, "__ykPresence", { value: true, enumerable: false });
  // В расширении скрипт работает и в обычной вкладке пользователя: тогда подмена включается только на время
  // автоматического выполнения. Контент-скрипт ставит data-yk-auto="1"/"0" (асинхронно, после загрузки) и метку
  // в sessionStorage вкладки — по ней подмена действует с самого начала следующей страницы, до ответа контент-скрипта.
  // В приложении подмена включена всегда.
  const gated = window.__ykPresenceGated === true;
  let early = false;
  if (gated) { try { early = sessionStorage.getItem("__yk_auto") === "1"; } catch (e) { early = false; } }
  const on = () => {
    if (!gated) return true;
    const a = document.documentElement && document.documentElement.getAttribute("data-yk-auto");
    return a === null ? early : a === "1";
  };

  const natives = new WeakMap();
  const fnToString = Function.prototype.toString;
  const patchedToString = function toString() {
    return natives.has(this) ? natives.get(this) : fnToString.call(this);
  };
  natives.set(patchedToString, fnToString.call(fnToString));
  Function.prototype.toString = patchedToString;
  const native = (fn, name) => { natives.set(fn, `function ${name}() { [native code] }`); return fn; };

  const proto = Document.prototype;
  const getter = (obj, prop) => { const d = Object.getOwnPropertyDescriptor(obj, prop); return d && d.get; };
  const realHidden = getter(proto, "hidden");
  const realState = getter(proto, "visibilityState");
  const realHasFocus = proto.hasFocus;
  const reallyHidden = () => { try { return realHidden ? realHidden.call(document) : false; } catch (e) { return false; } };

  const define = (prop, real, fake) => {
    if (!real) return;
    Object.defineProperty(proto, prop, {
      configurable: true, enumerable: true,
      get: native(function () { return on() ? fake : real.call(this); }, `get ${prop}`),
    });
  };
  define("hidden", realHidden, false);
  define("visibilityState", realState, "visible");
  define("webkitHidden", getter(proto, "webkitHidden"), false);
  define("webkitVisibilityState", getter(proto, "webkitVisibilityState"), "visible");
  proto.hasFocus = native(function hasFocus() { return on() ? true : realHasFocus.call(this); }, "hasFocus");

  // События ухода: перехватываем в фазе захвата на window — наш обработчик поставлен раньше скриптов сайта
  const outOfWindow = (e) => !e.relatedTarget && !e.toElement;
  const rules = {
    visibilitychange: () => true, webkitvisibilitychange: () => true, freeze: () => true,
    blur: (e) => e.target === window || e.target === document,
    focusout: (e) => e.target === window || e.target === document || !e.relatedTarget && e.target === document.body,
    mouseleave: (e) => e.target === document || e.target === document.documentElement || e.target === document.body
                       || outOfWindow(e),
    mouseout: outOfWindow, pointerout: outOfWindow,
    pointerleave: (e) => e.target === document || e.target === document.documentElement || outOfWindow(e),
  };
  for (const [type, test] of Object.entries(rules)) {
    window.addEventListener(type, (e) => {
      if (on() && test(e)) e.stopImmediatePropagation();
    }, true);
    document.addEventListener(type, (e) => {
      if (on() && test(e)) e.stopImmediatePropagation();
    }, true);
  }

  // Последнее настоящее движение мыши: приложение не «водит» курсором, пока над страницей двигает мышью человек
  const last = { x: -1, y: -1, t: -1e9 };
  window.addEventListener("mousemove", (e) => {
    if (e.isTrusted) { last.x = e.clientX; last.y = e.clientY; last.t = performance.now(); }
  }, { capture: true, passive: true });
  Object.defineProperty(window, "__ykLastMove", {
    value: () => [last.x, last.y, performance.now() - last.t], enumerable: false,
  });

  // Свёрнутое окно не рисуется, и requestAnimationFrame замирает — подстраховываемся таймером
  const realRaf = window.requestAnimationFrame, realCaf = window.cancelAnimationFrame;
  if (realRaf && realCaf) {
    const pending = new Map();
    let seq = 0;
    window.requestAnimationFrame = native(function requestAnimationFrame(cb) {
      const id = ++seq;
      let done = false;
      const run = (t) => { if (done) return; done = true; pending.delete(id); cb(t); };
      const raf = realRaf.call(window, run);
      const timer = on() && reallyHidden() ? setTimeout(() => { realCaf.call(window, raf); run(performance.now()); }, 16) : 0;
      pending.set(id, [raf, timer]);
      return id;
    }, "requestAnimationFrame");
    window.cancelAnimationFrame = native(function cancelAnimationFrame(id) {
      const p = pending.get(id);
      if (!p) return;
      pending.delete(id);
      realCaf.call(window, p[0]);
      if (p[1]) clearTimeout(p[1]);
    }, "cancelAnimationFrame");
  }
})();
"""

AREA_JS = """() => {
  const el = document.querySelector('#taskhtml') || document.querySelector('.test-ex-content, main') || document.body;
  const r = el.getBoundingClientRect();
  const W = window.innerWidth || document.documentElement.clientWidth || 1280;
  const H = window.innerHeight || document.documentElement.clientHeight || 800;
  const x0 = Math.max(8, r.left), y0 = Math.max(8, r.top);
  const x1 = Math.min(W - 8, r.right), y1 = Math.min(H - 8, r.bottom);
  if (x1 - x0 < 40 || y1 - y0 < 40) return [W * 0.2, H * 0.2, W * 0.8, H * 0.8];
  return [x0, y0, x1, y1];
}"""

LAST_MOVE_JS = "() => (window.__ykLastMove ? window.__ykLastMove() : null)"
USER_QUIET_S = 3.0           # человек двигал мышью над страницей недавнее этого — не вмешиваемся
TICK_S = (0.6, 2.2)          # как часто «шевелится» мышь во время ожиданий
AREA_TTL_S = 4.0             # область задания перечитывается не чаще (страница перерисовывается)


def install(ctx) -> None:
    """Скрипт присутствия — во все страницы контекста, включая уже открытые."""
    add = getattr(ctx, "add_init_script", None)
    if add is None:                       # не Playwright-контекст (заглушка в тестах)
        return
    add(PRESENCE_JS)
    for p in list(getattr(ctx, "pages", []) or []):
        try:
            p.evaluate(PRESENCE_JS)
        except Exception:  # noqa: BLE001 — страница закрывается/перезагружается
            pass


class Presence:
    """Курсор «на задании». Все вызовы — из потока Playwright (того, что открыл страницу)."""

    def __init__(self, page, rng: random.Random | None = None, clock: Callable[[], float] = time.monotonic):
        self.page, self.rng, self.clock = page, rng or random.Random(), clock
        self.owner = threading.get_ident()
        self.pos: tuple[float, float] | None = None
        self._next = 0.0
        self._area: tuple[float, float, float, float] | None = None
        self._area_at = -1e9
        self.moves = 0
        self.errors = 0
        self.skipped = 0

    # -- область, по которой водим
    def _area_now(self) -> tuple[float, float, float, float]:
        now = self.clock()
        if self._area is None or now - self._area_at > AREA_TTL_S:
            try:
                a = self.page.evaluate(AREA_JS)
                self._area = (float(a[0]), float(a[1]), float(a[2]), float(a[3]))
            except Exception:  # noqa: BLE001 — страница перезагружается: берём прежнюю или разумную область
                self._area = self._area or (200.0, 150.0, 900.0, 600.0)
            self._area_at = now
        return self._area

    def _target(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self._area_now()
        if self.pos is None or self.rng.random() < 0.25:            # иногда — в другое место задания
            return self.rng.uniform(x0, x1), self.rng.uniform(y0, y1)
        x, y = self.pos                                              # обычно — небольшое смещение (читает, «ведёт» строку)
        r = self.rng.uniform(15, 140)
        a = self.rng.uniform(0, 2 * math.pi)
        return min(x1, max(x0, x + r * math.cos(a))), min(y1, max(y0, y + r * 0.6 * math.sin(a)))

    def user_active(self) -> bool:
        """Над страницей двигает мышью человек (последнее движение — не наше и недавнее).
        Системный курсор эмуляция не трогает: события уходят прямо во вкладку. Но если смешивать их с настоящими,
        страница видела бы «скачущий» курсор, поэтому, пока человек водит мышью, приложение молчит."""
        try:
            last = self.page.evaluate(LAST_MOVE_JS)
        except Exception:  # noqa: BLE001
            return False
        if not last or last[2] > USER_QUIET_S * 1000:
            return False
        if self.pos is None:
            return True
        return abs(last[0] - self.pos[0]) > 3 or abs(last[1] - self.pos[1]) > 3

    def nudge(self) -> None:
        """Один «человеческий» сдвиг курсора внутри области задания."""
        if threading.get_ident() != self.owner:
            return                                                   # Playwright sync — только из своего потока
        if self.user_active():
            self.pos = None                                          # потом начнём с новой точки, а не «прыжком назад»
            self.skipped += 1
            return
        x, y = self._target()
        try:
            if self.pos is None:
                self.page.mouse.move(x, y)
            else:                                                     # слегка изогнутый путь, не прямая
                sx, sy = self.pos
                mx = (sx + x) / 2 + self.rng.uniform(-30, 30)
                my = (sy + y) / 2 + self.rng.uniform(-20, 20)
                self.page.mouse.move(mx, my, steps=self.rng.randint(3, 8))
                self.page.mouse.move(x, y, steps=self.rng.randint(3, 8))
            self.pos = (x, y)
            self.moves += 1
        except Exception:  # noqa: BLE001 — страница уходит / контекст уничтожен: не мешаем основной работе
            self.errors += 1
            self._area = None

    def tick(self) -> None:
        """Вызывается часто; сам решает, пора ли сдвинуть курсор."""
        now = self.clock()
        if now >= self._next:
            self.nudge()
            self._next = self.clock() + self.rng.uniform(*TICK_S)

    def call(self, fn: Callable[[], T], poll_s: float = 0.15) -> T:
        """fn() в отдельном потоке (долгий запрос к модели), а этот поток тем временем водит мышью."""
        box: dict = {}

        def worker() -> None:
            try:
                box["value"] = fn()
            except BaseException as e:  # noqa: BLE001 — пробрасываем в вызывающий поток
                box["error"] = e

        t = threading.Thread(target=worker, name="yk-solve", daemon=True)
        t.start()
        while t.is_alive():
            self.tick()
            t.join(poll_s)
        if "error" in box:
            raise box["error"]
        return box["value"]

    # -- текущее присутствие потока: control.sleep «шевелит» мышью в любых паузах
    def __enter__(self) -> "Presence":
        self._prev = _control.idle_hook()
        _control.set_idle_hook(self.tick)
        return self

    def __exit__(self, *exc) -> None:
        _control.set_idle_hook(self._prev)


def current_call(fn: Callable[[], T]) -> T:
    """fn() с движением курсора, если у потока есть активное присутствие; иначе просто fn()."""
    hook = _control.idle_hook()
    owner = getattr(hook, "__self__", None)
    if isinstance(owner, Presence):
        return owner.call(fn)
    return fn()
