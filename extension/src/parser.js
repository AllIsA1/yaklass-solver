/* Разбор страниц ЯКласса по DOM. Порт yaklass_bot/parser.py: те же правила и те же поля (snake_case), чтобы
   результат можно было сверять с Python-разбором на одних и тех же страницах (tests/test_extension.py).
   Работает в контент-скрипте (document) и в тестах (любой Document). Глобальный объект: YKParser. */
(function (root) {
  "use strict";

  const BLOCK_TAGS = new Set(["div", "p", "br", "li", "tr", "h1", "h2", "h3", "h4"]);

  // ---------------------------------------------------------------- вспомогательное
  const num = (text) => {
    if (!text) return null;
    const m = /\d+(?:[.,]\d+)?/.exec(text);
    return m ? parseFloat(m[0].replace(",", ".")) : null;
  };
  const int = (text) => {
    const n = num(text);
    return n === null ? null : Math.trunc(n);
  };
  const lname = (el) => (el.localName || el.nodeName || "").toLowerCase().split(":").pop();
  const classes = (el) => Array.from(el.classList || []);
  const hasClass = (el, c) => !!(el.classList && el.classList.contains(c));
  const abs = (src) => (src.startsWith("//") ? "https:" + src : src);
  const attrEsc = (s) => String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"');

  /* get_text(strip=True) / get_text(sep, strip=True) из BeautifulSoup: текстовые узлы обрезаются и склеиваются. */
  function textOf(el, sep = "", strip = true) {
    const parts = [];
    const walk = (n) => {
      for (const c of n.childNodes) {
        if (c.nodeType === 3) {
          const t = strip ? c.nodeValue.trim() : c.nodeValue;
          if (!strip || t) parts.push(t);
        } else if (c.nodeType === 1) {
          const nm = lname(c);
          if (nm !== "script" && nm !== "style") walk(c);
        }
      }
    };
    walk(el);
    return parts.join(sep);
  }

  const q1 = (el, sel) => el.querySelector(sel);
  const qa = (el, sel) => Array.from(el.querySelectorAll(sel));
  const closestClass = (el, cls) => {
    for (let p = el.parentElement; p; p = p.parentElement) if (hasClass(p, cls)) return p;
    return null;
  };

  // ---------------------------------------------------------------- MathML -> текст (yaklass_bot/mathml.py)
  function kids(el) {
    return Array.from(el.childNodes).filter((c) => c.nodeType === 1);
  }

  function mathText(el) {
    if (el.nodeType === 3) return el.nodeValue.trim();
    if (el.nodeType !== 1) return "";
    const name = lname(el);
    if (hasClass(el, "ykl-input")) return `[[${el.getAttribute("data-name") || ""}]]`;
    const k = kids(el);
    if (name === "mfrac" && k.length === 2) return `(${mathText(k[0])})/(${mathText(k[1])})`;
    if (name === "msub" && k.length === 2) return `${mathText(k[0])}_{${mathText(k[1])}}`;
    if (name === "msup" && k.length === 2) return `${mathText(k[0])}^{${mathText(k[1])}}`;
    if (name === "msubsup" && k.length === 3) return `${mathText(k[0])}_{${mathText(k[1])}}^{${mathText(k[2])}}`;
    if (name === "msqrt") return `sqrt(${k.map(mathText).join("")})`;
    if (name === "mroot" && k.length === 2) return `root(${mathText(k[1])}, ${mathText(k[0])})`;
    if (name === "mfenced") {
      const o = el.hasAttribute("open") ? el.getAttribute("open") : "(";
      const c = el.hasAttribute("close") ? el.getAttribute("close") : ")";
      return o + k.map(mathText).join(",") + c;
    }
    if (name === "mo") return ` ${textOf(el)} `;
    return Array.from(el.childNodes).map(mathText).join("");
  }

  // ---------------------------------------------------------------- текст задания
  function groupName(ul) {
    const inp = q1(ul, "input[name]");
    const name = inp ? inp.getAttribute("name") : "group";
    return inp && inp.getAttribute("type") === "radio" ? name : name.split("|")[0] + "|*";
  }

  function render(node, out, images) {
    for (const ch of node.childNodes) {
      if (ch.nodeType === 8) continue;                                   // комментарий
      if (ch.nodeType === 3) { out.push(ch.nodeValue.replace(/ /g, " ")); continue; }
      if (ch.nodeType !== 1) continue;
      const name = lname(ch);
      const cls = classes(ch);
      const nameAttr = () => (ch.hasAttribute("name") ? ch.getAttribute("name") : "None");
      if (name === "math") {
        out.push(" " + mathText(ch).replace(/\s+/g, " ").trim() + " ");
      } else if (name === "select") {
        out.push(` [[${nameAttr()}]] `);
      } else if (name === "input" && [null, "text", "number"].includes(ch.getAttribute("type"))) {
        out.push(` [[${nameAttr()}]] `);
      } else if (name === "textarea") {
        out.push(` [[${nameAttr()}]] `);
      } else if (name === "ul" && cls.includes("gxs-answer-select")) {
        out.push(` [[${groupName(ch)}]] `);                              // варианты перечисляем отдельно
      } else if (cls.includes("gxs-dnd-field")) {
        const inp = q1(ch, "input[name]");
        out.push(inp ? ` [[${inp.getAttribute("name")}]] ` : " ");
      } else if (cls.includes("answer-box")) {
        continue;                                                        // варианты dnd — в Field.options
      } else if (name === "img") {
        images.push(abs(ch.getAttribute("src") || ""));
        out.push(` [IMG:${ch.getAttribute("alt") || ""}] `);
      } else if (name === "meta" || name === "script" || name === "style") {
        continue;
      } else if (name === "td" || name === "th") {
        out.push(` ${inline(ch, images)} |`);
      } else {
        if (BLOCK_TAGS.has(name)) out.push("\n");
        render(ch, out, images);
        if (BLOCK_TAGS.has(name)) out.push("\n");
      }
    }
  }

  function inline(node, images) {
    const out = [];
    render(node, out, images || []);
    return out.join("").replace(/ /g, " ").replace(/\s+/g, " ").trim();
  }

  // ---------------------------------------------------------------- поля ответа
  function maxPoints(el) {
    for (const c of classes(el)) if (c.startsWith("maxpoints")) return num(c.slice("maxpoints".length));
    return null;
  }

  function labelOption(inp, value, rootEl) {
    const id = inp.getAttribute("id");
    const lab = id ? rootEl.querySelector(`label[for="${attrEsc(id)}"]`) : null;
    const holder = lab ? q1(lab, ".select-text") || lab : null;
    const img = holder ? q1(holder, "img") : null;
    return {
      value,
      text: holder ? inline(holder) : "",
      image: img && img.getAttribute("src") ? abs(img.getAttribute("src")) : "",
    };
  }

  function collectFields(rootEl) {
    const fields = new Map();
    const inGroup = (el) => {
      for (let p = el.parentElement; p; p = p.parentElement) if (lname(p) === "ul" && hasClass(p, "gxs-answer-select")) return true;
      return false;
    };

    for (const ul of qa(rootEl, "ul.gxs-answer-select")) {                // радио / чекбоксы
      const inputs = qa(ul, "input[type=radio], input[type=checkbox]");
      if (!inputs.length) continue;
      const kind = inputs[0].getAttribute("type");
      const f = { name: groupName(ul), kind, options: [], max_points: maxPoints(ul), size: null };
      for (const inp of inputs) {
        const val = kind === "radio" ? inp.getAttribute("value") : inp.getAttribute("name");
        f.options.push(labelOption(inp, val || "", ul));
      }
      fields.set(f.name, f);
    }
    for (const el of qa(rootEl, "select[name]")) {                        // выпадающие списки
      fields.set(el.getAttribute("name"), {
        name: el.getAttribute("name"), kind: "dropdown", max_points: maxPoints(el), size: null,
        options: qa(el, "option").filter((o) => o.getAttribute("value"))
          .map((o) => ({ value: o.getAttribute("value") || "", text: textOf(o).replace(/ /g, " "), image: "" })),
      });
    }
    for (const el of qa(rootEl, "input[name], textarea[name]")) {         // текстовые поля
      const t = lname(el) === "input" ? el.getAttribute("type") || "text" : "textarea";
      if (!["text", "number", "textarea"].includes(t) || inGroup(el)) continue;
      if (closestClass(el, "gxs-dnd-field")) continue;
      fields.set(el.getAttribute("name"), {
        name: el.getAttribute("name"), kind: "text", options: [], max_points: maxPoints(el), size: int(el.getAttribute("size")),
      });
    }
    for (const el of qa(rootEl, ".ykl-input[data-name]")) {               // поля формул (input создаёт JS сайта)
      const n = el.getAttribute("data-name");
      if (fields.has(n)) continue;
      fields.set(n, {
        name: n, kind: "text", options: [], size: int(el.getAttribute("data-size")),
        max_points: maxPoints(closestClass(el, "gxst-formula-box-wrap") || el),
      });
    }
    for (const boxField of qa(rootEl, ".gxs-dnd-field")) {                // перетаскивание
      const inp = q1(boxField, "input[name]");
      if (!inp) continue;
      const boxId = boxField.getAttribute("data-boxid");
      const box = boxId ? rootEl.querySelector(`[id="${attrEsc(boxId)}"]`) : null;
      const options = [];
      for (const opt of box ? qa(box, ".gxs-dnd-option[data-id]") : []) {
        const img = q1(opt, "img");
        options.push({ value: opt.getAttribute("data-id"), text: inline(opt),
                       image: img && img.getAttribute("src") ? abs(img.getAttribute("src")) : "" });
      }
      fields.set(inp.getAttribute("name"), { name: inp.getAttribute("name"), kind: "dnd", options,
                                              max_points: maxPoints(boxField), size: null });
    }
    return Array.from(fields.values());
  }

  // ---------------------------------------------------------------- страницы
  function pageKind(doc) {
    const head = q1(doc, ".exercise-content-header .pull-left");
    if (!q1(doc, "#taskhtml") || !head) return "other";
    return head.textContent.includes("Теория") ? "theory" : "task";
  }

  function parseExercise(doc) {
    const form = q1(doc, "form.taskForm");
    const wrapper = q1(doc, "#taskhtml");
    if (!wrapper) throw new Error("не найден #taskhtml — это не страница задания");
    const out = [], images = [];
    render(wrapper, out, images);
    const text = out.join("").replace(/[ \t]+/g, " ").replace(/\n\s*\n+/g, "\n").trim();
    const pts = q1(doc, ".obj-points");
    const cur = q1(doc, ".test-ex-nav-v2__item.is-current");
    const timer = q1(doc, ".tst-time");
    let position = 0;
    if (form && form.getAttribute("action")) {
      try { position = parseInt(new URL(form.getAttribute("action"), "https://x.invalid").searchParams.get("exercisePosition") || "0", 10) || 0; }
      catch (e) { position = 0; }
    }
    let title;
    if (cur) {
      title = cur.getAttribute("title") || "";
    } else {                                                            // тренировочные задания и теория
      const posEl = q1(doc, "#itempos"), nameEl = q1(doc, "#itemtitle");
      position = position || (posEl ? int(posEl.textContent) || 0 : 0);
      title = nameEl ? textOf(nameEl) : "";
    }
    const nxt = q1(doc, ".nav-cell-next a[href]");
    return {
      position, title, points: pts ? num(pts.textContent) : null, text,
      fields: collectFields(wrapper), images,
      has_audio: !!q1(wrapper, "audio, [class*=audio]"),
      form_action: form ? form.getAttribute("action") || "" : "",
      time_left_s: timer ? int(timer.getAttribute("data-left-time")) : null,
      is_theory: pageKind(doc) === "theory",
      next_url: nxt ? nxt.getAttribute("href") : "",
    };
  }

  function parseNav(doc) {
    const items = [];
    for (const li of qa(doc, ".test-ex-nav-v2__item")) {
      const link = q1(li, ".test-ex-nav-v2__link");
      const pos = link ? int(link.textContent) : null;
      if (pos === null) continue;
      items.push({
        position: pos, title: li.getAttribute("title") || "",
        href: link && lname(link) === "a" ? link.getAttribute("href") || "" : "",
        is_current: hasClass(li, "is-current"), is_answered: hasClass(li, "is-answered"),
      });
    }
    return items;
  }

  function parseOverview(doc) {
    const rows = [];
    for (const row of qa(doc, ".test-exercise-list-v2__row")) {
      const name = q1(row, ".test-exercise-list-v2__name"), status = q1(row, ".test-exercise-list-v2__status");
      const title = name ? textOf(name, " ") : "";
      const st = status ? textOf(status, " ") : "";
      const m = /^\s*(\d+)\./.exec(title);
      rows.push({ position: m ? parseInt(m[1], 10) : rows.length + 1, title, status: st,
                  answered: st.toLowerCase().includes("ответ получен") });
    }
    const form = q1(doc, 'form[action*="/TestWorkRun/CompleteTest"]');
    return { rows, complete_action: form ? form.getAttribute("action") : "",
             has_finish_button: !!q1(doc, "#finishTestBtn") };
  }

  function parseResult(doc) {
    let earned = 0, total = 0, seen = 0, pending = 0;
    for (const cell of qa(doc, ".test-exercise-list-v2__row .points-cell-v2__value")) {
      const got = q1(cell, ".points-cell-v2__earned");
      const m = /\/\s*(\d+(?:[.,]\d+)?)/.exec(textOf(cell, " "));
      if (!m) continue;
      seen += 1;
      total += parseFloat(m[1].replace(",", "."));
      const g = got ? num(textOf(got)) : null;
      if (g === null) pending += 1; else earned += g;
    }
    if (!seen) return { earned: null, total: null, pending: 0, known: false, percent: null };
    const known = pending === 0 && total > 0;
    return { earned, total, pending, known, percent: known ? Math.round((1000 * earned) / total) / 10 : null };
  }

  const workTitle = (doc) => { const el = q1(doc, "#itemtitle"); return el ? textOf(el, " ") : ""; };

  function minutes(raw) {
    if (!raw) return null;
    let m = /^\s*(\d+):(\d{1,2})(?::\d{1,2})?\s*$/.exec(raw);
    if (m) return parseInt(m[1], 10) * 60 + parseInt(m[2], 10) || null;
    m = /(\d+)\s*мин/.exec(raw);
    return m ? parseInt(m[1], 10) : null;
  }

  function parsePreview(doc) {
    const info = {};
    for (const p of qa(doc, "#test-preview .info p")) {
      const val = q1(p, ".value");
      if (!val) continue;
      const label = textOf(p, " ").replace(textOf(val, " "), "").replace(/^[ :]+|[ :]+$/g, "");
      info[label] = textOf(val);
    }
    const find = (...keys) => { for (const [k, v] of Object.entries(info)) if (keys.some((x) => k.toLowerCase().includes(x))) return v; return null; };
    const btn = q1(doc, "#start-action-btn") || q1(doc, 'form[action*="/TestWorkRun/Start/"] button');
    const title = q1(doc, "#itemtitle");
    return {
      title: title ? textOf(title) : "",
      time_limit_min: minutes(find("время выполнения", "время на")),
      tasks_count: int(find("количество заданий")),
      max_points: int(find("максимальное количество баллов")),
      attempts_left: int(find("попыт")),
      can_start: !!btn && !btn.hasAttribute("disabled"),
      start_label: btn ? textOf(btn) : "",
      is_continue: !!btn && textOf(btn).toLowerCase().includes("продолж"),
      is_preview: !!q1(doc, "#test-preview"),
    };
  }

  function parseWorksList(doc) {
    const works = [];
    for (const section of qa(doc, "section.wg-testworks")) {
      const header = q1(section, "h3");
      if (!header || !header.textContent.includes("Новые работы")) continue;
      for (const tr of qa(section, "tbody tr")) {
        const a = q1(tr, "td.testwork a[href]");
        if (!a) continue;
        const href = a.getAttribute("href");
        const m = /\/TestWorkRun\/Preview\/(\d+)/.exec(href);
        if (!m) continue;
        const subj = q1(tr, "td.subject"), t = q1(tr, "td.date time");
        works.push({
          work_id: m[1],
          subject: subj ? textOf(subj, " ").replace("Предмет", "").trim() : "",
          title: textOf(a),
          deadline_utc_ms: t && t.getAttribute("data-utc-date") ? parseInt(t.getAttribute("data-utc-date"), 10) : null,
          preview_url: href,
        });
      }
    }
    return works;
  }

  const attemptId = (url) => {
    try {
      const q = new URL(url, "https://x.invalid").searchParams;
      return q.get("testResultId") || q.get("twId") || "unknown";
    } catch (e) { return "unknown"; }
  };

  const api = { num, int, pageKind, parseExercise, parseNav, parseOverview, parseResult, parsePreview,
                parseWorksList, workTitle, attemptId, inline, textOf };
  root.YKParser = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
