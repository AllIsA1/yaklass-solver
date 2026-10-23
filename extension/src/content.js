/* Контент-скрипт: работает внутри страницы ЯКласса. Отвечает фоновому скрипту: «что на странице», «выставь ответы»,
   «нажми кнопку». Страницы ЯКласса перезагружаются на каждом задании, поэтому состояния здесь нет. */
(function () {
  "use strict";
  if (globalThis.__ykContentLoaded) return;
  globalThis.__ykContentLoaded = true;

  const P = globalThis.YKParser, F = globalThis.YKFiller;
  const READ_RX = /^\s*(прочитан|я прочитал|ознакомил)/i;
  const CONFIRM_RX = /^\s*(да|заверш|подтверд|ок\b|ok\b|сдать)/i;
  const CANCEL_RX = /^\s*(отмен|закры|нет\b|назад|cancel|no\b|close)/i;
  const NEW_BTN = "#submitAnswerBtn", REWRITE_BTN = "#correctAnswerBtn", FINISH_BTN = "#finishTestBtn";
  const DIALOG_BUTTONS = "[role=dialog] button, .ui-dialog button, .ui-dialog-buttonpane button, .yk-dialog button, " +
                         ".dialog-container button, .modal button";
  const COMPLETE_BTN = 'form[action*="/TestWorkRun/CompleteTest"] button[type=submit], ' +
                       'form[action*="/TestWorkRun/CompleteTest"] input[type=submit]';

  const visible = (el) => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) &&
                          getComputedStyle(el).visibility !== "hidden";
  const label = (el) => (el.innerText || el.value || el.textContent || "").trim();

  function blocked() {
    const html = document.documentElement.outerHTML.toLowerCase();
    const title = (document.title || "").toLowerCase();
    if (title.includes("403 error") || title.includes("403 forbidden")) return "blocked";
    if (html.includes("доступ запрещ") && (html.includes("подозрительн") || html.includes("403"))) return "blocked";
    if ((html.includes("servicepipe") || html.includes("js-challenge-loader") || html.includes("id_captcha_frame_div")) &&
        html.length < 20000) return "challenge";
    return "";
  }

  function readButton(isTheory) {
    const cands = Array.from(document.querySelectorAll("button, a, [role=button], input[type=button], input[type=submit]"))
      .filter((el) => READ_RX.test(label(el)) && visible(el));
    if (cands.length) return cands[0];
    if (isTheory) { const b = document.querySelector(NEW_BTN); if (b && visible(b)) return b; }
    return null;
  }

  function snapshot() {
    const kind = P.pageKind(document);
    const snap = {
      url: location.href, kind, title: P.workTitle(document), blocked: blocked(),
      nav: P.parseNav(document), overview: P.parseOverview(document), preview: P.parsePreview(document),
      result: P.parseResult(document), works: P.parseWorksList(document),
      hasRewrite: !!document.querySelector(REWRITE_BTN), hasNew: !!document.querySelector(NEW_BTN),
      finishButton: !!document.querySelector(FINISH_BTN), completeForm: !!document.querySelector(COMPLETE_BTN),
      readButton: !!readButton(kind === "theory"),
      overviewHref: (document.querySelector("a.test-ex-nav-v2__overview") || {}).href || "",
    };
    if (kind === "task" || kind === "theory") {
      try { snap.task = P.parseExercise(document); } catch (e) { snap.taskError = String(e.message || e); }
    }
    return snap;
  }

  function clickLater(el) { setTimeout(() => el.click(), 60); }          // ответ уйдёт раньше, чем страница выгрузится

  const handlers = {
    ping: () => ({ ok: true, url: location.href, ready: document.readyState }),
    snapshot: () => ({ ok: true, snap: snapshot() }),

    async fill({ task, values, pace }) {
      const results = await F.fillTask(document, task, values, pace);
      return { ok: true, results };
    },

    async submit() {                                                     // «Ответить» / «Сохранить» (если ответ уже был)
      const rewrite = !!document.querySelector(REWRITE_BTN);
      const btn = document.querySelector(rewrite ? REWRITE_BTN : NEW_BTN);
      if (!btn) return { ok: false, note: "кнопки «Ответить» нет" };
      btn.scrollIntoView({ block: "center" });
      if (rewrite) {                                                     // «Сохранить» активируется, когда ответ изменён
        for (let i = 0; i < 15 && btn.disabled; i++) await new Promise((r) => setTimeout(r, 200));
        if (btn.disabled) return { ok: true, unchanged: true, rewrite };  // ответ тот же — менять нечего
      }
      clickLater(btn);
      return { ok: true, rewrite };
    },

    errorText() {
      const el = document.querySelector("#errorSummary");
      return { ok: true, text: el ? (el.innerText || "").trim() : "" };
    },

    readTheory({ isTheory }) {
      const btn = readButton(isTheory);
      if (!btn) return { ok: false, note: "кнопки «Прочитано» нет — вероятно, уже отмечено" };
      if (btn.disabled) return { ok: false, note: "кнопка «Прочитано» неактивна — вероятно, уже отмечено" };
      btn.scrollIntoView({ block: "center" });
      clickLater(btn);
      return { ok: true };
    },

    clickSelector({ selector }) {
      const el = document.querySelector(selector);
      if (!el || !visible(el)) return { ok: false, note: "элемент не найден" };
      el.scrollIntoView({ block: "center" });
      clickLater(el);
      return { ok: true };
    },

    clickFinish() {                                                      // «Завершить» на «Списке заданий»
      const btn = document.querySelector(FINISH_BTN) || document.querySelector(COMPLETE_BTN);
      if (!btn) return { ok: false, note: "кнопки завершения нет" };
      btn.scrollIntoView({ block: "center" });
      const isForm = !document.querySelector(FINISH_BTN);
      if (isForm) clickLater(btn); else btn.click();                     // кнопка без формы откроет окно подтверждения
      return { ok: true, form: isForm };
    },

    confirmDialog() {                                                    // кнопка согласия в окне подтверждения
      for (const b of document.querySelectorAll(DIALOG_BUTTONS)) {
        const text = label(b);
        if (visible(b) && text && CONFIRM_RX.test(text) && !CANCEL_RX.test(text)) {
          clickLater(b);
          return { ok: true, text };
        }
      }
      return { ok: false };
    },
  };

  // выполняет ли расширение работу именно в этой вкладке (тогда окна подтверждения сайта принимаются само)
  chrome.runtime.sendMessage({ content: "hello" }).then((r) => {
    if (r && r.auto) document.documentElement.setAttribute("data-yk-auto", "1");
  }).catch(() => {});

  chrome.runtime.onMessage.addListener((msg, _sender, send) => {
    const h = msg && handlers[msg.cmd];
    if (!h) return false;
    Promise.resolve().then(() => h(msg)).then(send, (e) => send({ ok: false, note: String((e && e.message) || e) }));
    return true;                                                         // ответ асинхронный
  });
})();
