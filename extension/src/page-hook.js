/* Выполняется в самой странице (world MAIN), до скриптов сайта. Действует, только пока расширение само выполняет
   работу в этой вкладке (контент-скрипт ставит data-yk-auto="1" и метку в sessionStorage); в обычной работе
   пользователя с сайтом ничего не меняется.
   1. Нативное confirm() сайта принимается автоматически — иначе страница зависла бы в ожидании нажатия.
   2. «Присутствие»: страница всегда видна и в фокусе, уход курсора/сворачивание окна до сайта не доходят
      (общий код с приложением — yaklass_bot/presence.py, PRESENCE_JS; тест сверяет копии).
   3. Если настоящая мышь не двигается над страницей (окно свёрнуто, курсор в другом окне) — курсор «водится» по
      области задания. Расширение не может слать настоящие (isTrusted) события без режима отладки, поэтому события
      синтетические; приложение (Playwright) шлёт настоящие. */
window.__ykPresenceGated = true;
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
(function () {
  const auto = () => {
    const a = document.documentElement && document.documentElement.getAttribute("data-yk-auto");
    if (a !== null) return a === "1";
    try { return sessionStorage.getItem("__yk_auto") === "1"; } catch (e) { return false; }
  };
  const original = window.confirm;
  window.confirm = function confirm(...args) {
    return auto() ? true : original.apply(this, args);
  };

  // курсор: только когда нет настоящих движений мыши
  let lastReal = 0, x = 0, y = 0;
  const realMove = (e) => { if (e.isTrusted) { lastReal = Date.now(); x = e.clientX; y = e.clientY; } };
  window.addEventListener("mousemove", realMove, { capture: true, passive: true });
  const area = () => {
    const el = document.querySelector("#taskhtml") || document.body;
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const W = window.innerWidth || 1280, H = window.innerHeight || 800;
    const x0 = Math.max(8, r.left), y0 = Math.max(8, r.top), x1 = Math.min(W - 8, r.right), y1 = Math.min(H - 8, r.bottom);
    return x1 - x0 < 40 || y1 - y0 < 40 ? [W * 0.2, H * 0.2, W * 0.8, H * 0.8] : [x0, y0, x1, y1];
  };
  const step = () => {
    if (!auto() || Date.now() - lastReal < 2500) return;
    const a = area();
    if (!a) return;
    if (!x || Math.random() < 0.25) { x = a[0] + Math.random() * (a[2] - a[0]); y = a[1] + Math.random() * (a[3] - a[1]); }
    else {
      const ang = Math.random() * Math.PI * 2, d = 15 + Math.random() * 120;
      x = Math.min(a[2], Math.max(a[0], x + d * Math.cos(ang)));
      y = Math.min(a[3], Math.max(a[1], y + d * 0.6 * Math.sin(ang)));
    }
    const target = document.elementFromPoint(x, y) || document.body;
    const init = { bubbles: true, cancelable: true, composed: true, view: window, clientX: x, clientY: y,
                   screenX: x + (window.screenX || 0), screenY: y + (window.screenY || 0) + 80 };
    try {
      target.dispatchEvent(new PointerEvent("pointermove", { ...init, pointerType: "mouse", isPrimary: true }));
      target.dispatchEvent(new MouseEvent("mousemove", init));
    } catch (e) { /* страница перерисовывается */ }
  };
  (function loop() { step(); setTimeout(loop, 600 + Math.random() * 1600); })();
})();
