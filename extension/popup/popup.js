import { originPattern, parseConnectionCode } from "../src/connect.js";

const $ = (id) => document.getElementById(id);
const call = (api, args = {}) => chrome.runtime.sendMessage({ api, ...args });
let S = null;

const pts = (v) => { const f = Number(v || 0); return Number.isInteger(f) ? String(f) : f.toFixed(1).replace(".", ","); };
const dur = (s) => { const m = Math.floor(s / 60), r = s % 60; return m ? `${m} мин ${r} с` : `${r} с`; };

document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll("nav button").forEach((x) => x.classList.toggle("on", x === b));
  for (const sec of document.querySelectorAll("main > section")) sec.hidden = sec.id !== `tab-${b.dataset.tab}`;
  if (b.dataset.tab === "log") $("log").scrollTop = $("log").scrollHeight;
}));

function pill() {
  const p = $("conn");
  if (!S.linked) { p.textContent = "Не привязано"; p.className = "pill warn"; }
  else if (S.connected) { p.textContent = "Подключено" + (S.username ? ` · @${S.username}` : ""); p.className = "pill ok"; }
  else { p.textContent = "Нет связи"; p.className = "pill bad"; }
}

function renderHome() {
  $("link-box").hidden = S.linked; $("linked-box").hidden = !S.linked;
  $("who").textContent = S.username ? `@${S.username}` : "";
  $("conn-note").textContent = S.note || (S.connected ? "Подключено к серверу. Бот управляет расширением командами." : "");
  const r = S.run;
  $("run-text").textContent = r ? `${r.state === "paused" ? "Пауза" : "Выполняется"}: «${r.title || r.workId}»` + (r.progress ? ` — ${r.progress.note}` : "")
    : "Выберите работу на вкладке «Работы» или запустите её командой из Telegram.";
  $("bar-wrap").hidden = !(r && r.progress);
  if (r && r.progress) { $("bar-fill").style.width = `${Math.round((100 * r.progress.done) / Math.max(r.progress.total, 1))}%`; $("bar-label").textContent = `${r.progress.done} из ${r.progress.total}`; }
  $("btn-pause").hidden = !(r && r.state === "running"); $("btn-resume").hidden = !(r && r.state === "paused"); $("btn-stop").hidden = !r;
}

function renderWorks() {
  const box = $("works"); box.textContent = "";
  $("works-empty").hidden = S.works.length > 0;
  $("works-count").textContent = S.works.length ? `Найдено работ: ${S.works.length}` : "";
  for (const w of S.works) {
    const d = document.createElement("div"); d.className = "item";
    const chip = document.createElement("span"); chip.className = "chip"; chip.textContent = w.subject ? w.subject[0].toUpperCase() + w.subject.slice(1) : "";
    const t = document.createElement("div"); t.className = "title"; t.textContent = w.title;
    const m = document.createElement("div"); m.className = "meta"; m.textContent = w.deadline ? `Срок: до ${w.deadline}` : "";
    const acts = document.createElement("div"); acts.className = "acts";
    for (const [label, mode, style, cls] of [["Выполнить", "auto", "normal", "primary"], ["Быстро", "auto", "fast", ""], ["Только заполнить", "dry", "normal", "ghost"]]) {
      const b = document.createElement("button"); b.textContent = label; if (cls) b.className = cls; b.disabled = !S.linked || !!S.run;
      b.addEventListener("click", async () => {
        if (mode === "auto" && !confirm(`Выполнить «${w.title}»? Это может потратить попытку, и отменить результат нельзя.`)) return;
        const r = await call("startWork", { workId: w.id, mode, style });
        if (!r.ok) alert(r.why || r.error || "Не удалось запустить"); else load();
      });
      acts.append(b);
    }
    d.append(chip, t, m, acts); box.append(d);
  }
}

function renderHistory() {
  const box = $("history"); box.textContent = "";
  $("history-empty").hidden = S.history.length > 0; $("btn-clear").hidden = !S.history.length;
  const done = S.history.filter((e) => e.percent !== null && e.percent !== undefined);
  $("history-count").textContent = S.history.length ? `Запусков: ${S.history.length}` + (done.length ? `, средний результат ${Math.round(done.reduce((a, e) => a + e.percent, 0) / done.length)}%` : "") : "";
  for (const e of S.history) {
    const d = document.createElement("div"); d.className = "item hist";
    const left = document.createElement("div");
    const t = document.createElement("div"); t.className = "title"; t.textContent = e.title || `Работа ${e.work_id}`;
    const m = document.createElement("div"); m.className = "meta";
    m.textContent = [new Date(e.ts * 1000).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" }), e.elapsed_s != null ? dur(e.elapsed_s) : "", e.style_label || ""].filter(Boolean).join("  ·  ");
    left.append(t, m);
    if ((e.problems || []).length) {
      const p = document.createElement("div"); p.className = "meta"; p.title = e.problems.map((x) => `№${x.position} — ${x.status}: ${x.note}`).join("\n");
      p.textContent = "Не выполнено: " + e.problems.slice(0, 4).map((x) => `№${x.position}: ${x.status}`).join("; "); left.append(p);
    }
    const right = document.createElement("div"); right.style.textAlign = "right";
    if (e.percent !== null && e.percent !== undefined && e.total) {
      const b = document.createElement("div"); b.className = "big"; b.textContent = `${Math.round(e.percent)}%`;
      const s = document.createElement("div"); s.className = "meta"; s.textContent = `${pts(e.earned)}/${pts(e.total)} баллов`; right.append(b, s);
    }
    const chip = document.createElement("span"); chip.className = "chip" + (e.completed ? "" : " new");
    chip.textContent = e.mode === "dry" ? "Без отправки" : e.stopped ? "Остановлена" : e.completed ? "Завершена" : "Не завершена"; right.append(chip);
    d.append(left, right); box.append(d);
  }
}

function render() {
  pill(); renderHome(); renderWorks(); renderHistory();
  $("log").textContent = S.log.join("\n");
  $("version").textContent = `Версия ${S.version}`;
  const u = $("update");
  u.hidden = !S.update;
  if (S.update) { u.textContent = `Доступна версия ${S.update.version}. Скачайте архив расширения на `; const a = document.createElement("a"); a.href = S.update.url; a.target = "_blank"; a.textContent = "странице релиза"; u.append(a, " и замените папку."); }
}

async function load() { S = await call("getState"); render(); }

$("btn-link").addEventListener("click", async () => {
  const text = $("code").value, err = $("link-err");
  err.hidden = true;
  let pattern = "";
  try { const p = parseConnectionCode(text); if (p.url) pattern = originPattern(p.url); }
  catch (e) { err.textContent = e.message; err.hidden = false; return; }
  if (pattern) {                                                    // разрешение на обращение к серверу из кода (жест пользователя — этот клик)
    const granted = await chrome.permissions.request({ origins: [pattern] }).catch(() => false);
    if (!granted) { err.textContent = "Без разрешения на доступ к серверу привязка невозможна."; err.hidden = false; return; }
  }
  $("btn-link").disabled = true;
  const r = await call("link", { text });
  $("btn-link").disabled = false;
  if (!r.ok) { err.textContent = r.error || "Не удалось привязать"; err.hidden = false; return; }
  $("code").value = ""; load();
});
$("code").addEventListener("keydown", (e) => { if (e.key === "Enter") $("btn-link").click(); });
$("btn-unlink").addEventListener("click", async () => { await call("unlink"); load(); });
$("btn-pause").addEventListener("click", () => call("pause"));
$("btn-resume").addEventListener("click", () => call("resume"));
$("btn-stop").addEventListener("click", () => call("stop"));
$("btn-refresh").addEventListener("click", async () => { $("btn-refresh").disabled = true; await call("listWorks"); $("btn-refresh").disabled = false; load(); });
$("btn-clear").addEventListener("click", async () => { if (confirm("Очистить историю?")) { await call("clearHistory"); load(); } });

chrome.runtime.onMessage.addListener((m) => { if (m && m.event) load(); });
load();
