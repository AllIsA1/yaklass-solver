/* Сервис-воркер расширения: привязка по коду из Telegram-бота, связь с сервером (WebSocket /v1/agent), команды из
   Telegram, выполнение работ, история. Страницы ЯКласса обрабатывает контент-скрипт. */
import { ConnectionCodeError, originPattern, parseConnectionCode, wsUrl } from "./connect.js";
import { Runner, STATUS_NAMES, pending, summarize } from "./runner.js";
import { link } from "./solver.js";
import { openTab, snap, trackTabs } from "./tabs.js";

const VERSION = chrome.runtime.getManifest().version;
const DEFAULT_BASE = "https://www.yaklass.ru";
const HISTORY_MAX = 300, LOG_MAX = 400;
const COMMANDS = ["status", "list_works", "start_work", "pause", "resume", "stop"];

let cfg = { serverUrl: "", token: "", username: "", deviceName: "", baseUrl: DEFAULT_BASE };
let ws = null, wsTimer = null, pingTimer = null, backoff = 2000, connected = false, lastError = "";
let run = null;                                          // {runner, workId, state: running|paused, title}
let logLines = [];
let works = [];

trackTabs();

// ---------------------------------------------------------------- хранилище и события
async function loadConfig() {
  const d = await chrome.storage.local.get(["config"]);
  cfg = { ...cfg, ...(d.config || {}) };
  if (!cfg.deviceName) cfg.deviceName = `Расширение (${(navigator.userAgentData && navigator.userAgentData.platform) || "браузер"})`;
}
const saveConfig = () => chrome.storage.local.set({ config: cfg });
const config = () => cfg;

function log(text) {
  for (const line of String(text).split("\n")) {
    if (!line.trim()) continue;
    logLines.push(`${new Date().toLocaleTimeString("ru-RU")}  ${line}`);
  }
  if (logLines.length > LOG_MAX) logLines = logLines.slice(-LOG_MAX);
  broadcast({ event: "log" });
}

function broadcast(msg) { chrome.runtime.sendMessage(msg).catch(() => {}); }            // попап может быть закрыт

function send(ev) {
  if (ws && ws.readyState === WebSocket.OPEN) { try { ws.send(JSON.stringify(ev)); } catch (e) { /* связь оборвалась */ } }
}

function setState(state, workId = "", title = "") {
  send({ type: "status", state, work_id: workId, title });
  broadcast({ event: "state" });
}

const ack = (cmd, ok, msg = "") => send({ type: "ack", cmd, ok, msg });

// ---------------------------------------------------------------- связь с сервером
function connect() {
  clearTimeout(wsTimer);
  if (!cfg.serverUrl || !cfg.token) { setConnected(false, "Не привязано"); return; }
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  let welcomed = false;
  try { ws = new WebSocket(wsUrl(cfg.serverUrl)); } catch (e) { scheduleReconnect(); return; }
  ws.onopen = () => ws.send(JSON.stringify({ type: "auth", token: cfg.token }));
  ws.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch (err) { return; }
    if (msg.type === "welcome") {
      welcomed = true; backoff = 2000;
      setConnected(true, "");
      send({ type: "hello", device: cfg.deviceName, client: "extension", version: VERSION });
      setState(run ? run.state : "idle", run ? run.workId : "", run ? run.title : "");
      clearInterval(pingTimer);
      pingTimer = setInterval(() => send({ type: "ping" }), 20000);      // трафик не даёт воркеру «уснуть»
    } else if (msg.type === "cmd") handleCommand(msg).catch((err) => { log(`Ошибка команды: ${err.message}`); send({ type: "error", message: String(err.message).slice(0, 300) }); });
  };
  ws.onclose = (e) => {
    clearInterval(pingTimer);
    if (e.code === 4401 || e.code === 4403) {                            // токен недействителен / отозван
      cfg.token = ""; cfg.username = ""; saveConfig();
      setConnected(false, "Расширение отвязано: получите новый код командой /link в боте");
      return;
    }
    setConnected(false, welcomed ? "Связь с сервером потеряна" : "Нет связи с сервером");
    scheduleReconnect();
  };
  ws.onerror = () => {};
}

function scheduleReconnect() {
  clearTimeout(wsTimer);
  wsTimer = setTimeout(connect, backoff);
  backoff = Math.min(backoff * 2, 60000);
}

function setConnected(ok, note) {
  connected = ok; lastError = note;
  broadcast({ event: "connection" });
}

function disconnect() {
  clearTimeout(wsTimer); clearInterval(pingTimer);
  if (ws) { ws.onclose = null; try { ws.close(); } catch (e) { /* уже закрыт */ } }
  ws = null; connected = false;
}

// ---------------------------------------------------------------- команды из Telegram
async function handleCommand(msg) {
  const cmd = msg.cmd;
  if (!COMMANDS.includes(cmd)) return;
  log(`Команда из Telegram: ${cmd}`);
  if (cmd === "status") setState(run ? run.state : "idle", run ? run.workId : "", run ? run.title : "");
  else if (cmd === "list_works") { const w = await listWorks(); send({ type: "works", works: w.slice(0, 10), new: false }); }
  else if (cmd === "start_work") {
    const r = await startWork(String(msg.work_id || ""), msg.mode || "auto", msg.style || "normal", true);
    ack(cmd, r.ok, r.why || "");
  } else if (cmd === "pause") pauseRun();
  else if (cmd === "resume") resumeRun();
  else if (cmd === "stop") stopRun();
}

// ---------------------------------------------------------------- работы
async function listWorks() {
  if (run) return works;
  let tabId;
  try {
    tabId = await openTab(`${cfg.baseUrl}/TestWork`, false);
    const s = await snap(tabId);
    if (s.blocked) { log("ЯКласс показал страницу блокировки или проверки — список работ недоступен"); send({ type: "error", message: "ЯКласс показал страницу блокировки или проверки" }); return works; }
    if (!s.works.length && !/\/TestWork/i.test(s.url)) { send({ type: "error", message: "В браузере не выполнен вход в ЯКласс" }); log("Не выполнен вход в ЯКласс"); return works; }
    works = s.works.map((w) => ({ id: w.work_id, subject: w.subject, title: w.title, deadline: fmtDeadline(w.deadline_utc_ms) }));
    await chrome.storage.local.set({ works });
    broadcast({ event: "works" });
    return works;
  } catch (e) {
    log(`Не удалось получить список работ: ${e.message}`);
    return works;
  } finally {
    if (tabId) chrome.tabs.remove(tabId).catch(() => {});
  }
}

function fmtDeadline(ms) {
  if (!ms) return "";
  const d = new Date(ms);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getDate())}.${p(d.getMonth() + 1)} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

async function startWork(workId, mode, style, remote) {
  if (!/^\d{1,12}$/.test(workId)) return { ok: false, why: "Некорректный номер работы" };
  if (!["auto", "dry"].includes(mode)) return { ok: false, why: "Некорректный режим" };
  if (!["normal", "fast"].includes(style)) {
    return { ok: false, why: "Скрытный режим в расширении пока недоступен — выберите обычный или быстрый" };
  }
  if (!cfg.serverUrl || !cfg.token) return { ok: false, why: "Расширение не привязано к Telegram" };
  if (run) return { ok: false, why: "Уже выполняется другая работа" };
  const known = works.find((w) => w.id === workId);
  const runner = new Runner({ config, log, progress: (done, total, note) => { if (run) run.progress = { done, total, note }; send({ type: "progress", work_id: workId, done, total, note }); broadcast({ event: "progress" }); } });
  run = { runner, workId, state: "running", title: known ? known.title : "", mode, style, startedAt: Date.now(), progress: null };
  setState("running", workId, run.title);
  const started = run;
  (async () => {
    let res;
    try {
      res = await runner.run(workId, mode, style);
    } catch (e) {
      log(`Ошибка выполнения: ${e.message}`);
      send({ type: "error", message: `Ошибка выполнения: ${e.message}`.slice(0, 300) });
      await addHistory({ work_id: workId, title: run ? run.title : "", mode, style, completed: false, stopped: false, fatal: "",
                         percent: null, earned: null, total: null, summary: {}, note: `Ошибка: ${e.message}`.slice(0, 300), problems: [],
                         elapsed_s: Math.round((Date.now() - started.startedAt) / 1000) });
      res = null;
    }
    if (res) {
      const title = res.title || started.title;
      const summary = summarize(res.outcomes);
      const payload = { type: "finished", work_id: workId, title, completed: res.completed, summary, note: res.note };
      if (res.percent !== null) Object.assign(payload, { earned: res.earned, total: res.total, percent: res.percent });
      send(payload);
      log(`\nИтоги: ${Object.entries(summary).map(([k, v]) => `${k} ${v}`).join(", ")}\n${res.completed ? "Работа завершена." : "Работа НЕ завершена."} ${res.note}`);
      await addHistory({ work_id: workId, title, mode, style, completed: res.completed, stopped: res.stopped, fatal: res.fatal,
                         percent: res.percent, earned: res.earned, total: res.total, summary, note: res.note,
                         style_label: mode === "dry" ? "без отправки" : style === "fast" ? "быстро" : "обычно",
                         problems: pending(res.outcomes).slice(0, 20).map((o) => ({ position: o.position, status: STATUS_NAMES[o.status] || o.status, note: (o.note || "").slice(0, 200) })),
                         elapsed_s: Math.round((Date.now() - started.startedAt) / 1000) });
    }
    run = null;
    setState("idle");
  })();
  return { ok: true };
}

function pauseRun() { if (run && run.state === "running") { run.runner.control.pause(); run.state = "paused"; setState("paused", run.workId, run.title); } }
function resumeRun() { if (run && run.state === "paused") { run.runner.control.resume(); run.state = "running"; setState("running", run.workId, run.title); } }
function stopRun() { if (run) run.runner.control.stop(); }

// ---------------------------------------------------------------- история
async function addHistory(entry) {
  const d = await chrome.storage.local.get(["history"]);
  const items = (d.history || []).concat([{ ts: Math.floor(Date.now() / 1000), style_label: entry.mode === "dry" ? "без отправки" : "обычно", ...entry }]);
  await chrome.storage.local.set({ history: items.slice(-HISTORY_MAX) });
  broadcast({ event: "history" });
}

// ---------------------------------------------------------------- сообщения из окна расширения
const api = {
  async getState() {
    const d = await chrome.storage.local.get(["history", "works", "update"]);
    return {
      version: VERSION, linked: !!(cfg.serverUrl && cfg.token), username: cfg.username, connected, note: lastError,
      run: run ? { state: run.state, workId: run.workId, title: run.title, progress: run.progress } : null,
      works: works.length ? works : d.works || [], history: (d.history || []).slice().reverse(), log: logLines,
      update: d.update || null, deviceName: cfg.deviceName,
    };
  },
  async link({ text }) {
    let parsed;
    try { parsed = parseConnectionCode(text); } catch (e) { if (e instanceof ConnectionCodeError) return { ok: false, error: e.message }; throw e; }
    const server = parsed.url || cfg.serverUrl;
    if (!server) return { ok: false, error: "Вставьте код подключения целиком: его присылает бот в ответ на /link" };
    try {
      const r = await link(server, parsed.code, cfg.deviceName);
      cfg.serverUrl = server; cfg.token = r.token; cfg.username = r.username;
      await saveConfig();
      disconnect(); backoff = 2000; connect();
      return { ok: true, username: r.username };
    } catch (e) { return { ok: false, error: e.message }; }
  },
  async origin({ text }) {                                                // какой адрес запросить у пользователя (разрешение)
    try { const p = parseConnectionCode(text); return { ok: true, pattern: p.url ? originPattern(p.url) : "" }; }
    catch (e) { return { ok: false, error: e.message }; }
  },
  async unlink() { disconnect(); cfg.token = ""; cfg.username = ""; await saveConfig(); setConnected(false, "Не привязано"); return { ok: true }; },
  async listWorks() { const w = await listWorks(); return { ok: true, works: w }; },
  async startWork({ workId, mode, style }) { return startWork(String(workId), mode, style, false); },
  async pause() { pauseRun(); return { ok: true }; },
  async resume() { resumeRun(); return { ok: true }; },
  async stop() { stopRun(); return { ok: true }; },
  async clearHistory() { await chrome.storage.local.set({ history: [] }); broadcast({ event: "history" }); return { ok: true }; },
  async setDeviceName({ name }) { cfg.deviceName = String(name || "").slice(0, 40) || cfg.deviceName; await saveConfig(); return { ok: true }; },
};

chrome.runtime.onMessage.addListener((msg, sender, respond) => {
  if (msg && msg.content === "hello" && sender.tab) {                    // контент-скрипт: «это моя вкладка с работой?»
    respond({ auto: !!(run && run.runner.tabId === sender.tab.id) });
    return false;
  }
  const own = sender.url && sender.url.startsWith(chrome.runtime.getURL(""));   // страницы самого расширения, не сайта
  if (!msg || !msg.api || !own || !api[msg.api]) return false;
  api[msg.api](msg).then(respond, (e) => respond({ ok: false, error: String((e && e.message) || e) }));
  return true;
});

// ---------------------------------------------------------------- запуск, будильник, проверка обновлений
async function checkUpdate() {
  try {
    const r = await fetch("https://api.github.com/repos/AllIsA1/yaklass-solver/releases/latest", { headers: { Accept: "application/vnd.github+json" } });
    if (!r.ok) return;
    const d = await r.json();
    const tag = String(d.tag_name || "").replace(/^v/i, "");
    const newer = (a, b) => { const x = a.split(".").map(Number), y = b.split(".").map(Number); for (let i = 0; i < 3; i++) { if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) > (y[i] || 0); } return false; };
    await chrome.storage.local.set({ update: newer(tag, VERSION) ? { version: tag, url: d.html_url } : null });
    broadcast({ event: "update" });
  } catch (e) { /* нет сети или репозиторий приватный — молча */ }
}

chrome.storage.onChanged.addListener((changes, area) => {            // настройки поменяли снаружи (в т. ч. тесты)
  if (area === "local" && changes.config && changes.config.newValue) cfg = { ...cfg, ...changes.config.newValue };
});

chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === "keepalive") { if (cfg.serverUrl && cfg.token && !(ws && ws.readyState <= 1)) connect(); }
  if (a.name === "update") checkUpdate();
});

async function init() {
  await loadConfig();
  const d = await chrome.storage.local.get(["works"]);
  works = d.works || [];
  chrome.alarms.create("keepalive", { periodInMinutes: 0.5 });
  chrome.alarms.create("update", { delayInMinutes: 1, periodInMinutes: 720 });
  connect();
}
chrome.runtime.onStartup.addListener(init);
chrome.runtime.onInstalled.addListener(init);
init();
