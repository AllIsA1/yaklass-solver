/* Выполнение работы во вкладке браузера пользователя. Порт yaklass_bot/autorun.py (обычный и быстрый режимы).
   Страницы ЯКласса перезагружаются на каждом задании, поэтому цикл живёт здесь, а на странице — только контент-скрипт. */
import { FatalSolveError, SolveError, solve } from "./solver.js";
import { ask, loads, navigate, openTab, snap as takeSnap, waitLoadedAfter } from "./tabs.js";

export const PACE = {
  normal: { between_tasks: [1, 3], before_submit: [0.5, 1.5], field_pause: [0.25, 0.9], typing_ms: [40, 110] },
  fast: { between_tasks: [0.2, 0.6], before_submit: [0.1, 0.3], field_pause: [0.05, 0.15], typing_ms: [0, 8] },
};
export const STATUS_NAMES = { answered: "новых ответов", rewritten: "переписано", unchanged: "без изменений",
  theory: "теория", unsupported: "без поддержки", failed: "ошибки", timeout: "не успели", filled: "заполнено" };
const SENT = ["answered", "rewritten", "unchanged"];
const PENDING = ["failed", "unsupported", "timeout"];
const MIN_SECONDS_LEFT = 45, SUBMIT_WAIT_MS = 12000;

export class Stopped extends Error {}
export class RunAbort extends Error {}                       // понятная причина остановки (блокировка, нет входа)

const rnd = (lo, hi) => lo + Math.random() * (hi - lo);

export class Control {
  constructor() { this.stopped = false; this.paused = false; this._wake = []; }
  stop() { this.stopped = true; this._release(); }
  pause() { this.paused = true; }
  resume() { this.paused = false; this._release(); }
  _release() { for (const w of this._wake.splice(0)) w(); }
  async checkpoint() {
    if (this.stopped) throw new Stopped();
    while (this.paused && !this.stopped) await new Promise((r) => this._wake.push(r));
    if (this.stopped) throw new Stopped();
  }
  async sleep(ms) {                                          // пауза, которую можно прервать остановкой
    await this.checkpoint();
    await new Promise((resolve) => { const t = setTimeout(resolve, ms); this._wake.push(() => { clearTimeout(t); resolve(); }); });
    await this.checkpoint();
  }
  range(r) { return this.sleep(rnd(r[0], r[1]) * 1000); }
}

const abs = (base, href) => new URL(href, base).href;

export class Runner {
  /** env: {config() -> {baseUrl, serverUrl, token}, log(text), progress(done,total,note)} */
  constructor(env) { this.env = env; this.control = new Control(); this.tabId = null; }

  async run(workId, mode, style) {
    const cfg = this.env.config();
    const pace = PACE[style] || PACE.normal;
    const runId = crypto.randomUUID().replace(/-/g, "");
    const res = { outcomes: [], completed: false, stopped: false, fatal: "", note: "", title: "", earned: null,
                  total: null, percent: null };
    const log = (m) => this.env.log(m);
    try {
      this.tabId = await openTab(`${cfg.baseUrl}/TestWorkRun/Preview/${workId}`);
      let s = await this.page();
      let maxPoints = null, limitS = null;
      if (s.preview.is_preview) {
        const info = s.preview;
        if (!info.can_start) throw new RunAbort("Запуск невозможен: нет кнопки «Начать» или не осталось попыток");
        maxPoints = info.max_points; limitS = info.time_limit_min ? info.time_limit_min * 60 : null;
        res.title = info.title;
        log(`Нажимаю «${info.start_label}»` + (info.is_continue ? "" : " (тратится попытка)"));
        const before = loads(this.tabId);
        await ask(this.tabId, { cmd: "clickSelector", selector: '#start-action-btn, form[action*="/TestWorkRun/Start/"] button' });
        await waitLoadedAfter(this.tabId, before, 30000);
        s = await this.page();
      }
      if (!res.title) res.title = s.title;
      await this.workLoop(s, mode, pace, runId, res, maxPoints, limitS);
      if (mode !== "auto") { res.note = "режим без отправки: ответы выставлены, но не отправлены"; return res; }
      if (res.outcomes.some((o) => PENDING.includes(o.status))) {
        res.note = "остались задания без ответа — работа не завершена, доделайте вручную";
        return res;
      }
      res.completed = await this.finishWork(res, log);
      if (!res.completed && !res.note) res.note = "не удалось завершить работу автоматически";
    } catch (e) {
      if (e instanceof Stopped) { res.stopped = true; res.note = "остановлено пользователем"; }
      else if (e instanceof FatalSolveError || e instanceof RunAbort) { res.fatal = e.message; res.note = `остановлено: ${e.message}`; }
      else throw e;
    }
    return res;
  }

  /** Снимок страницы с проверкой на блокировку и вход. */
  async page(timeoutMs) {
    const s = await takeSnap(this.tabId, timeoutMs);
    if (s.blocked === "blocked") throw new RunAbort("ЯКласс показал страницу «403: Доступ запрещён, подозрительная активность». Подождите и попробуйте позже.");
    if (s.blocked === "challenge") throw new RunAbort("ЯКласс показал проверку «вы не робот». Пройдите её на сайте и повторите.");
    if (s.kind === "other" && !s.preview.is_preview && !s.overview.rows.length && /login|account/i.test(s.url)) {
      throw new RunAbort("Не выполнен вход в ЯКласс: войдите на сайте в этом браузере и повторите.");
    }
    return s;
  }

  async workLoop(first, mode, pace, runId, res, maxPoints, limitS) {
    const c = this.control;
    if (first.kind === "other") throw new RunAbort(`Неожиданная страница вместо задания: ${first.url}`);
    const base = first.url;
    const hrefs = new Map(first.nav.filter((n) => n.href).map((n) => [n.position, abs(base, n.href)]));
    const positions = first.nav.map((n) => n.position).sort((a, b) => a - b);
    if (!positions.length && first.task) positions.push(first.task.position);
    const total = positions.length;
    this.env.log(`\nЗаданий: ${total}; прохожу с первого по последнее`);
    let cur = first;
    for (let idx = 0; idx < positions.length; idx++) {
      await c.checkpoint();
      if (idx > 0) cur = await this.page();                  // после отправки сайт мог перейти на другую страницу
      const pos = positions[idx];
      const here = cur.task ? cur.task.position : -1;
      if (here !== pos) {
        if (!hrefs.has(pos)) { res.outcomes.push({ position: pos, title: "", status: "failed", note: "нет ссылки на задание" }); continue; }
        await navigate(this.tabId, hrefs.get(pos));
        cur = await this.page();
      }
      for (const n of cur.nav) if (n.href) hrefs.set(n.position, abs(base, n.href));
      hrefs.set(pos, cur.url);
      this.env.log(`\n── Задание ${pos}/${total} ──`);
      const o = await this.doTask(cur, mode, pace, runId, { maxPoints, limitS });
      o.position = o.position || pos;
      res.outcomes.push(o);
      this.env.log(`  результат: ${o.status}` + (o.note ? ` (${o.note})` : ""));
      this.env.progress(idx + 1, total, `задание ${pos}: ${o.status}`);
      if (o.status === "timeout") break;
      if (idx < positions.length - 1) await c.range(pace.between_tasks);
    }
  }

  async doTask(s, mode, pace, runId, ctx) {
    const c = this.control, cfg = this.env.config();
    if (s.blocked || !s.task) return { position: 0, title: "", status: "failed", note: s.taskError || "неожиданная страница" };
    const task = s.task;
    const base = { position: task.position, title: task.title };
    if (task.is_theory || (!task.fields.length && s.readButton)) {                // теория
      if (mode !== "auto") return { ...base, status: "theory", note: "без отправки — «Прочитано» не нажато" };
      await c.range(pace.before_submit);
      const before = loads(this.tabId);
      const r = await ask(this.tabId, { cmd: "readTheory", isTheory: task.is_theory });
      if (r.ok) await waitLoadedAfter(this.tabId, before, 8000);
      return { ...base, status: "theory", note: r.ok ? "отмечено «Прочитано»" : r.note };
    }
    if (!task.fields.length || task.has_audio) return { ...base, status: "unsupported", note: "аудио или нет полей ответа" };
    if (task.time_left_s !== null && task.time_left_s < MIN_SECONDS_LEFT) return { ...base, status: "timeout", note: `осталось ${task.time_left_s} с` };
    if (task.fields.some((f) => f.kind === "dnd")) return { ...base, status: "unsupported", note: "перетаскивание в расширении пока не поддерживается" };
    if (s.hasRewrite) this.env.log("  задание уже выполнено — перезаписываю ответ");

    let ans;
    try { ans = await solve(cfg.serverUrl, cfg.token, task, runId, (m) => this.env.log(m)); }
    catch (e) {
      if (e instanceof FatalSolveError) throw e;
      if (e instanceof SolveError) return { ...base, status: "failed", note: `нет ответа модели: ${e.message}` };
      throw e;
    }
    await c.checkpoint();
    let fr = await ask(this.tabId, { cmd: "fill", task, values: ans.values, pace }, 120000);
    if (!fr.results.every((r) => r.ok)) {
      await c.sleep(1000);
      fr = await ask(this.tabId, { cmd: "fill", task, values: ans.values, pace }, 120000);
    }
    const bad = fr.results.filter((r) => !r.ok);
    if (bad.length) return { ...base, status: "failed", confidence: ans.confidence,
                             note: "не удалось заполнить: " + bad.map((r) => `${r.field} (${r.note || "не встало"})`).join("; ") };
    if (mode !== "auto") return { ...base, status: "filled", confidence: ans.confidence };
    await c.range(pace.before_submit);
    return this.submit(base, ans);
  }

  async submit(base, ans) {
    const before = loads(this.tabId);
    const r = await ask(this.tabId, { cmd: "submit" });
    if (!r.ok) return { ...base, status: "failed", confidence: ans.confidence, note: `сайт не принял ответ: ${r.note}` };
    if (r.unchanged) return { ...base, status: "unchanged", confidence: ans.confidence, note: "ответ совпадает с сохранённым — менять нечего" };
    const kind = r.rewrite ? "rewritten" : "answered";
    if (await waitLoadedAfter(this.tabId, before, SUBMIT_WAIT_MS)) return { ...base, status: kind, confidence: ans.confidence };
    try {
      const e = await ask(this.tabId, { cmd: "errorText" }, 4000);
      if (e.text) return { ...base, status: "failed", confidence: ans.confidence, note: `сайт не принял ответ: ${e.text}` };
    } catch (e) { /* страница уже уходит — считаем отправленным */ }
    return { ...base, status: kind, confidence: ans.confidence, note: "явного подтверждения отправки нет (возможно, ответ принят без перезагрузки)" };
  }

  // ---------------------------------------------------------------- завершение работы
  async finishWork(res, log) {
    const c = this.control;
    let s = await this.page();
    if (!s.overview.rows.length && s.overviewHref) { await navigate(this.tabId, s.overviewHref); s = await this.page(); }
    const unanswered = s.overview.rows.filter((r) => !r.answered);
    if (unanswered.length) log("  на странице завершения без ответа: " + unanswered.map((r) => r.title).join(", "));
    let ok = false;
    if (s.overview.complete_action || s.completeForm) ok = await this.completeByForm(log);
    else if (s.finishButton) {
      if (unanswered.length) { log("  есть задания без ответа — работа не завершена"); return false; }
      ok = await this.completeByButton(log);
    } else { log("  кнопки завершения нет — работа не завершена"); return false; }
    if (ok) await this.readResult(res, log);
    await c.checkpoint();
    return ok;
  }

  async completeByForm(log) {
    const before = loads(this.tabId);
    const r = await ask(this.tabId, { cmd: "clickFinish" });
    if (!r.ok) return false;
    await this.control.sleep(1500);
    try { await ask(this.tabId, { cmd: "confirmDialog" }, 1500); } catch (e) { /* страница уже ушла */ }
    await waitLoadedAfter(this.tabId, before, 20000);
    return true;
  }

  async completeByButton(log) {
    const before = loads(this.tabId);
    const r = await ask(this.tabId, { cmd: "clickFinish" });
    if (!r.ok) return false;
    let pressed = "";
    for (let i = 0; i < 20 && !pressed; i++) {
      await this.control.sleep(400);
      try { const d = await ask(this.tabId, { cmd: "confirmDialog" }, 1500); if (d.ok) pressed = d.text; } catch (e) { break; }
    }
    if (pressed) log(`  подтверждение завершения: «${pressed}»`);
    else log("  окно подтверждения не появилось (возможно, подтверждение нативным окном браузера)");
    if (await waitLoadedAfter(this.tabId, before, 20000)) return true;
    try { const s = await this.page(4000); if (!s.finishButton) return true; } catch (e) { return true; }
    log("  страница не сменилась после подтверждения — завершение не подтверждено");
    return false;
  }

  async readResult(res, log) {
    try {
      await this.control.sleep(1000);
      let s = await this.page();
      if (!res.title && s.title) res.title = s.title;
      let r = s.result;
      if (!r.known && s.overviewHref) { await navigate(this.tabId, s.overviewHref); s = await this.page(); r = s.result; }
      if (r.known) {
        res.earned = r.earned; res.total = r.total; res.percent = r.percent;
        log(`  результат: ${r.earned} из ${r.total} баллов — ${r.percent}%`);
      } else log("  результат: на странице нет итоговых баллов" + (r.pending ? ` (не проверено заданий: ${r.pending})` : ""));
    } catch (e) { log(`  результат прочитать не удалось: ${e.message}`); }
  }
}

export function summarize(outcomes) {
  const out = {};
  for (const o of outcomes) { const k = STATUS_NAMES[o.status] || o.status; out[k] = (out[k] || 0) + 1; }
  return out;
}
export const pending = (outcomes) => outcomes.filter((o) => PENDING.includes(o.status));
export { SENT };
