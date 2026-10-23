/* Запросы к серверу: привязка, /v1/me и решение заданий (/v1/solve) с повторами при сбоях шлюза.
   Порт yaklass_app/solvers.py. Адрес сервера пользователю не показывается — он приходит в коде подключения. */
const TRANSIENT = [500, 502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 527, 530];
const ATTEMPTS = 4, BACKOFF_S = 3;

export class SolveError extends Error {}
export class FatalSolveError extends SolveError {}            // продолжать бессмысленно: лимит, отвязан, сервер недоступен
export class LinkError extends Error {}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const detailOf = async (r) => { try { const d = (await r.json()).detail; return typeof d === "string" ? d : ""; } catch (e) { return ""; } };

export async function link(serverUrl, code, deviceName) {
  let r;
  try {
    r = await fetch(serverUrl + "/v1/link", { method: "POST", headers: { "Content-Type": "application/json" },
                                              body: JSON.stringify({ code, device_name: deviceName }) });
  } catch (e) { throw new LinkError("Сервер недоступен: проверьте интернет и попробуйте ещё раз"); }
  if (r.status === 429) throw new LinkError("Слишком много попыток. Подождите минуту.");
  if (r.status !== 200) throw new LinkError((await detailOf(r)) || `Ошибка ${r.status}`);
  const d = await r.json();
  return { token: d.token, username: d.username || "" };
}

export async function me(serverUrl, token) {
  const r = await fetch(serverUrl + "/v1/me", { headers: { Authorization: `Bearer ${token}` } });
  if (r.status === 401 || r.status === 403) throw new FatalSolveError("Расширение отвязано или доступ закрыт — привяжите заново (/link в боте).");
  if (!r.ok) throw new SolveError(`Ошибка сервера ${r.status}`);
  return r.json();
}

export function serializeTask(task) {
  return { position: task.position, title: task.title, points: task.points, text: task.text, images: task.images,
           fields: task.fields.map((f) => ({ name: f.name, kind: f.kind, max_points: f.max_points, size: f.size,
                                            options: f.options.map((o) => ({ value: o.value, text: o.text, image: o.image })) })) };
}

/** -> {values, confidence, evidence, remaining}. log — для сообщений вроде «сервер просит подождать». */
export async function solve(serverUrl, token, task, runId, log = () => {}, sleepFn = sleep) {
  let last = "";
  for (let attempt = 0; attempt < ATTEMPTS; attempt++) {
    let r;
    try {
      r = await fetch(serverUrl + "/v1/solve", {
        method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ task: serializeTask(task), run_id: runId }),
      });
    } catch (e) {
      last = "сервер недоступен";
      await sleepFn(2000 * (attempt + 1));
      continue;
    }
    if (r.status === 200) {
      const d = await r.json();
      return { values: d.answers, confidence: Number(d.confidence), evidence: d.evidence || "", remaining: d.remaining_today };
    }
    const detail = await detailOf(r);
    if (r.status === 401 || r.status === 403) throw new FatalSolveError("Расширение отвязано или доступ закрыт — привяжите заново (/link в боте).");
    if (r.status === 429 && detail.includes("лимит")) throw new FatalSolveError(detail);
    if (r.status === 429) {                                               // слишком часто: подождать и повторить
      const wait = Math.min(30, parseInt(r.headers.get("Retry-After") || "5", 10) || 5);
      log(`  сервер просит подождать ${wait} с`);
      await sleepFn(wait * 1000);
      last = "слишком частые запросы";
      continue;
    }
    if (r.status === 413 || r.status === 422) throw new SolveError(`сервер отклонил задание: ${detail || r.status}`);
    if (TRANSIENT.includes(r.status)) {                                   // шлюз/прокси/сеть: повторить
      last = detail || `ошибка сервера ${r.status}`;
      if (attempt < ATTEMPTS - 1) {
        log(`  сервер ответил ${r.status}; повторю через ${BACKOFF_S * (attempt + 1)} с`);
        await sleepFn(BACKOFF_S * (attempt + 1) * 1000);
        continue;
      }
      throw new SolveError(last);
    }
    throw new SolveError(detail || `ошибка сервера ${r.status}`);
  }
  throw new FatalSolveError(`Сервер недоступен: ${last}`);
}
