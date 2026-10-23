/* Выставление ответов на странице задания событиями DOM. Отправку («Ответить») делает отдельная команда.
   Порт yaklass_bot/filler.py: те же типы полей, тот же поиск поля ввода. Глобальный объект: YKFiller. */
(function (root) {
  "use strict";

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const rnd = (lo, hi) => lo + Math.random() * (hi - lo);
  const esc = (s) => String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"');

  function optText(field, value) {
    const o = field.options.find((x) => x.value === value);
    return o ? o.text || o.image || value : value;
  }

  function setNativeValue(el, value) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);   // в обход перехвата value у фреймворков
  }

  function fire(el, type) {
    el.dispatchEvent(new Event(type, { bubbles: true, cancelable: true }));
  }

  function key(el, type, k) {
    el.dispatchEvent(new KeyboardEvent(type, { key: k, bubbles: true, cancelable: true }));
  }

  async function typeInto(el, text, typingMs) {
    el.scrollIntoView({ block: "center" });
    el.focus();
    el.click();
    setNativeValue(el, "");
    fire(el, "input");
    for (const ch of text) {                                            // без Enter!
      key(el, "keydown", ch);
      key(el, "keypress", ch);
      setNativeValue(el, el.value + ch);
      el.dispatchEvent(new InputEvent("input", { bubbles: true, data: ch, inputType: "insertText" }));
      key(el, "keyup", ch);
      await sleep(rnd(typingMs[0], typingMs[1]));
    }
    fire(el, "change");
  }

  function findTextInput(doc, name) {
    const base = name.split("|")[0];
    const sels = [`input[name="${esc(name)}"]`, `textarea[name="${esc(name)}"]`, `input#${CSS.escape(base)}`,
                  `[data-name="${esc(name)}"] input`, `input[data-name="${esc(name)}"]`];
    for (const s of sels) {
      let found;
      try { found = doc.querySelectorAll(s); } catch (e) { continue; }
      if (found.length === 1) return found[0];
    }
    return null;
  }

  function clickOptionLabel(doc, input) {
    const id = input.getAttribute("id");
    const label = id ? doc.querySelector(`label[for="${esc(id)}"]`) : null;
    const target = label || input;
    target.scrollIntoView({ block: "center" });
    target.click();                                                      // ярлык скрывает настоящий input — как у человека
  }

  async function fillField(doc, field, value, pace) {
    const res = (ok, note = "", wanted = null) => ({ field: field.name, wanted: wanted ?? value, ok, note });
    try {
      if (field.kind === "dropdown") {
        const sel = doc.querySelector(`select[name="${esc(field.name)}"]`);
        if (!sel) return res(false, "список не найден на странице", optText(field, value));
        sel.scrollIntoView({ block: "center" });
        sel.value = value;
        fire(sel, "input");
        fire(sel, "change");
        return res(sel.value === value, "", optText(field, value));
      }
      if (field.kind === "radio") {
        const inp = doc.querySelector(`input[type="radio"][name="${esc(field.name)}"][value="${esc(value)}"]`);
        if (!inp) return res(false, "вариант не найден на странице", optText(field, value));
        if (!inp.checked) clickOptionLabel(doc, inp);
        return res(inp.checked, "", optText(field, value));
      }
      if (field.kind === "checkbox") {
        const chosen = new Set(value.split(","));
        let ok = true;
        for (const o of field.options) {
          const inp = doc.querySelector(`input[type="checkbox"][name="${esc(o.value)}"]`);
          if (!inp) { ok = false; continue; }
          if (inp.checked !== chosen.has(o.value)) { clickOptionLabel(doc, inp); await sleep(rnd(150, 500)); }
          ok = ok && inp.checked === chosen.has(o.value);
        }
        return res(ok, "", value.split(",").map((v) => optText(field, v)).join("; "));
      }
      if (field.kind === "text") {
        const el = findTextInput(doc, field.name);
        if (!el) return res(false, "поле ввода не найдено на странице");
        await typeInto(el, value, pace.typing_ms);
        return res(el.value.trim() === value.trim());
      }
      if (field.kind === "dnd") return res(false, "перетаскивание в расширении пока не поддерживается", optText(field, value));
      return res(false, `неизвестный тип поля ${field.kind}`);
    } catch (e) {
      return res(false, String((e && e.message) || e));
    }
  }

  async function fillTask(doc, task, values, pace) {
    const results = [];
    for (const f of task.fields) {
      results.push(await fillField(doc, f, values[f.name], pace));
      await sleep(rnd(pace.field_pause[0] * 1000, pace.field_pause[1] * 1000));
    }
    return results;
  }

  const api = { fillTask, fillField, findTextInput };
  root.YKFiller = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
