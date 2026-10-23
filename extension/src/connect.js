/* Код подключения «домен:код» (порт yaklass_bot/connect.py): бот выдаёт одну строку, адрес сервера — внутри неё. */
const CODE = "[A-Za-z0-9]{4}-?[A-Za-z0-9]{4}";
const CODE_RX = new RegExp(`^${CODE}$`);
const DOMAIN_CODE_RX = new RegExp(`^(?:(https?)://)?([^\\s/:@?#]+(?::\\d{1,5})?):(${CODE})$`);
const HOST_RX = /^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*$/;
const LOCAL = ["localhost", "127.0.0.1", "::1"];

export class ConnectionCodeError extends Error {}

function serverUrl(host, scheme) {
  const name = /:\d+$/.test(host) ? host.replace(/:\d+$/, "") : host;
  if (!HOST_RX.test(name.toLowerCase())) throw new ConnectionCodeError("в коде подключения неверный адрес сервера");
  const local = LOCAL.includes(name.toLowerCase());
  if (scheme === "http" && !local) throw new ConnectionCodeError("сервер должен работать по https");
  return `${local ? "http" : "https"}://${host.toLowerCase()}`;
}

function normalizeServerUrl(url) {
  let u;
  try { u = new URL(url.trim()); } catch (e) { throw new ConnectionCodeError("в коде подключения неверный адрес сервера"); }
  if (!["https:", "http:"].includes(u.protocol) || !u.hostname) throw new ConnectionCodeError("в коде подключения неверный адрес сервера");
  if (u.protocol === "http:" && !LOCAL.includes(u.hostname)) throw new ConnectionCodeError("сервер должен работать по https");
  return `${u.protocol}//${u.host}`;
}

/** -> {url: адрес сервера | null, code: "XXXX-XXXX"}; для строки без адреса url = null. */
export function parseConnectionCode(text) {
  const t = String(text || "").trim().replace(/^[`'"«»]+|[`'"«»]+$/g, "").replace(/\s+/g, "");
  let url = null, code;
  if (t.toLowerCase().startsWith("yk1.")) {                               // прежний формат
    const parts = t.split(".");
    if (parts.length < 3) throw new ConnectionCodeError("код подключения неполный — скопируйте его из бота целиком");
    let decoded;
    try {
      const b64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
      decoded = new TextDecoder().decode(Uint8Array.from(atob(b64 + "=".repeat((4 - (b64.length % 4)) % 4)), (c) => c.charCodeAt(0)));
    } catch (e) { throw new ConnectionCodeError("код подключения повреждён — скопируйте его из бота целиком"); }
    url = normalizeServerUrl(decoded);
    code = parts.slice(2).join(".");
  } else {
    const m = DOMAIN_CODE_RX.exec(t);
    if (m) { url = serverUrl(m[2], (m[1] || "").toLowerCase() || null); code = m[3]; }
    else code = t;
  }
  if (!CODE_RX.test(code)) throw new ConnectionCodeError("неверный код подключения — отправьте боту /link и скопируйте новый");
  const c = code.replace(/-/g, "").toUpperCase();
  return { url, code: `${c.slice(0, 4)}-${c.slice(4)}` };
}

/** Маска адреса сервера для chrome.permissions: порты в шаблонах не указываются. */
export function originPattern(url) {
  const u = new URL(url);
  return `${u.protocol}//${u.hostname}/*`;
}

export function wsUrl(url) {
  const u = new URL(url);
  return `${u.protocol === "https:" ? "wss:" : "ws:"}//${u.host}/v1/agent`;
}
