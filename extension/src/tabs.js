/* Вкладка выполнения работы: открыть, перейти, дождаться загрузки, достучаться до контент-скрипта. */
const state = new Map();                       // tabId -> {loads, status}
const waiters = new Set();

export function trackTabs() {
  chrome.tabs.onUpdated.addListener((id, info) => {
    const s = state.get(id) || { loads: 0, status: "complete" };
    if (info.status === "loading") { s.loads += 1; s.status = "loading"; }
    if (info.status === "complete") s.status = "complete";
    state.set(id, s);
    for (const w of Array.from(waiters)) w();
  });
  chrome.tabs.onRemoved.addListener((id) => { state.delete(id); for (const w of Array.from(waiters)) w(); });
}

export const loads = (id) => (state.get(id) || { loads: 0 }).loads;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Ждёт загрузки страницы ПОСЛЕ действия: счётчик загрузок вырос и вкладка снова «complete». */
export function waitLoadedAfter(id, before, timeoutMs = 20000) {
  return new Promise((resolve) => {
    const check = () => {
      const s = state.get(id);
      if (s && s.loads > before && s.status === "complete") { cleanup(); resolve(true); }
    };
    const timer = setTimeout(() => { cleanup(); resolve(false); }, timeoutMs);
    const cleanup = () => { clearTimeout(timer); waiters.delete(check); };
    waiters.add(check);
    check();
  });
}

export async function openTab(url, active = true) {
  const tab = await chrome.tabs.create({ url, active });
  state.set(tab.id, { loads: 1, status: "loading" });
  await waitLoadedAfter(tab.id, 0, 45000);
  return tab.id;
}

export async function navigate(id, url, timeoutMs = 45000) {
  const before = loads(id);
  await chrome.tabs.update(id, { url });
  return waitLoadedAfter(id, before, timeoutMs);
}

export async function tabExists(id) {
  try { await chrome.tabs.get(id); return true; } catch (e) { return false; }
}

/** Сообщение контент-скрипту; пока страница грузится или скрипт не появился — повторяет. */
export async function ask(id, msg, timeoutMs = 20000) {
  const end = Date.now() + timeoutMs;
  let lastErr = "";
  while (Date.now() < end) {
    try {
      const r = await chrome.tabs.sendMessage(id, msg);
      if (r !== undefined) return r;
    } catch (e) { lastErr = String((e && e.message) || e); }
    if (!(await tabExists(id))) throw new Error("Вкладка с работой закрыта");
    await sleep(300);
  }
  throw new Error(`Страница не отвечает (${lastErr || "нет ответа"})`);
}

export async function snap(id, timeoutMs) {
  const r = await ask(id, { cmd: "snapshot" }, timeoutMs);
  return r.snap;
}
