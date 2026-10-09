"""Агент: держит связь с сервером (WebSocket), следит за новыми работами, выполняет работы по команде
пользователя (из приложения или из Telegram). Не зависит от GUI: события уходят через emit(name, payload)."""
from __future__ import annotations

import json
import random
import threading
import time
from collections.abc import Callable
from datetime import datetime

import websocket

from yaklass_bot.autorun import STATUS_NAMES
from yaklass_bot.guard import SiteBlocked, SiteChallenge
from yaklass_bot.control import RunControl
from yaklass_bot.solver import FatalSolveError, SolveError
from yaklass_bot.storage import works_table

from . import __version__, solvers, updater
from .backend import Backend, BackendError, NotLoggedIn, friendly_error
from .settings import Store, data_dir

Emit = Callable[[str, dict], None]
BLOCK_PAUSE_S = 6 * 3600           # пауза автоматических опросов после блокировки IP
FAIL_CODES = (4401, 4403)          # токен недействителен / отозван


def friendly_ws_error(err) -> str:
    """Техническую ошибку рукопожатия WebSocket -> понятное сообщение."""
    t = str(err)
    if "Handshake status 404" in t or "Handshake status 426" in t:
        return ("Сервер не принял WebSocket (ответ 404/426): в nginx на сервере не настроена передача заголовков "
                "Upgrade и Connection для этого адреса — см. docs/SERVER.md, раздел nginx.")
    if "Handshake status 502" in t or "Handshake status 503" in t or "Handshake status 504" in t:
        return "Сервер недоступен (nginx отвечает 502/503/504): сервис yaklass-solver не запущен или упал."
    if "certificate" in t.lower() or "ssl" in t.lower():
        return f"Ошибка сертификата HTTPS на сервере: {t[:160]}"
    if "Name or service not known" in t or "getaddrinfo" in t or "Errno -2" in t:
        return "Не удалось найти сервер по адресу из настроек — проверьте домен."
    if "Connection refused" in t:
        return "Сервер отклонил соединение — проверьте адрес и порт в настройках."
    return t[:240]


def _fmt_deadline(ms: int | None) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%d.%m %H:%M") if ms else ""


class Agent:
    def __init__(self, store: Store, backend: Backend, emit: Emit | None = None, directory=None):
        self.store, self.backend = store, backend
        self._emit = emit or (lambda name, payload: None)
        self.dir = directory or data_dir()
        self.state = "idle"                    # idle | running | paused
        self.current_work: str | None = None
        self.connected = False
        self.username = ""
        self.min_interval = 0
        self.works: list[dict] = []
        self.login_ok: bool | None = None      # None — ещё не проверяли
        self._control: RunControl | None = None
        self._ws: websocket.WebSocketApp | None = None
        self._send_lock = threading.Lock()
        self._works_lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads: list[threading.Thread] = []
        self._last_conn: tuple[bool, str] | None = None      # чтобы не сыпать одинаковыми событиями
        self._last_ws_error = ""
        self._last_logged = ""
        self.update_api = updater.API          # адрес API релизов (в тестах подменяется)
        self.update_plan = updater.detect_plan()   # как это приложение можно обновить (AppImage / exe / только вручную)
        self.release = None                    # последний найденный более новый релиз
        self.restart_fn = updater.restart      # в тестах подменяется
        self._blocked_until = 0.0              # сайт отклонил запросы: до этого времени автоматических опросов нет
        self._block_reported = False

    # ------------------------------------------------------------ служебное
    def emit(self, name: str, **payload) -> None:
        self._emit(name, payload)

    def log(self, text: str) -> None:
        """В журнал; одинаковое подряд идущее сообщение (например, ошибка при каждом опросе) — один раз."""
        if text == self._last_logged:
            return
        self._last_logged = text
        self.emit("log", text=text)

    def _set_state(self, state: str, work: str | None = None) -> None:
        self.state, self.current_work = state, work if state != "idle" else None
        self.emit("state", state=self.state, work_id=self.current_work)
        self._send({"type": "status", "state": self.state, "work_id": self.current_work or ""})

    def _send(self, ev: dict) -> None:
        with self._send_lock:
            if self._ws is not None and self.connected:
                try:
                    self._ws.send(json.dumps(ev, ensure_ascii=False))
                except Exception:  # noqa: BLE001 — соединение умерло, цикл переподключится
                    pass

    # ------------------------------------------------------------ жизненный цикл
    def start(self) -> None:
        for target in (self._ws_loop, self._poll_loop, self._update_loop):
            t = threading.Thread(target=target, daemon=True, name=target.__name__)
            t.start()
            self._threads.append(t)

    def shutdown(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._control:
            self._control.stop()
        self.reconnect()
        for t in self._threads:
            t.join(timeout=5)
        self.backend.close()

    def reconnect(self) -> None:
        """Переподключиться (после смены сервера/токена)."""
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
        self._wake.set()

    # ------------------------------------------------------------ связь с сервером
    def _set_connected(self, ok: bool, msg: str = "") -> None:
        """Событие «связь» — только когда состояние или сообщение действительно изменились
        (цикл переподключения вызывает это постоянно, журнал не должен забиваться повторами)."""
        self.connected = ok
        if self._last_conn != (ok, msg):
            self._last_conn = (ok, msg)
            self.emit("connection", connected=ok, message=msg)

    def _ws_loop(self) -> None:
        backoff = 2.0
        while not self._stop.is_set():
            s, token = self.store.load(), self.store.token
            if not (s.server_url and token):
                self._set_connected(False, "Не привязано: введите код из бота (/link)")
                self._wake.wait(3)
                self._wake.clear()
                continue
            info = {"code": None, "welcomed": False}

            def on_open(ws, token=token):
                ws.send(json.dumps({"type": "auth", "token": token}))

            def on_message(ws, raw, info=info):
                try:
                    msg = json.loads(raw)
                except ValueError:
                    return
                if msg.get("type") == "welcome":
                    info["welcomed"] = True
                    self._last_ws_error = ""
                    self.min_interval = int(msg.get("min_task_interval_s") or 0)
                    self._set_connected(True, "Подключено к серверу")
                    self._send({"type": "hello", "device": s.device_name})
                    self._send({"type": "status", "state": self.state, "work_id": self.current_work or ""})
                elif msg.get("type") == "cmd":
                    threading.Thread(target=self._handle_command, args=(msg,), daemon=True).start()

            def on_close(ws, code, reason, info=info):
                info["code"] = code

            def on_error(ws, err):
                text = f"Связь с сервером: {friendly_ws_error(err)}"
                if text != self._last_ws_error:                 # одна и та же ошибка при каждой попытке — только раз
                    self._last_ws_error = text
                    self.log(text)

            self._ws = websocket.WebSocketApp(ws_url_for(s.server_url), on_open=on_open, on_message=on_message,
                                              on_close=on_close, on_error=on_error)
            self._ws.run_forever(ping_interval=25, ping_timeout=10)
            self._set_connected(False, "Нет связи с сервером")
            if info["code"] in FAIL_CODES:
                self.store.token = ""
                self.emit("unlinked", message="Приложение отвязано (токен отозван). Привяжите заново: /link в боте.")
                continue
            if info["welcomed"]:
                backoff = 2.0
            if self._wake.wait(backoff) or self._stop.is_set():
                self._wake.clear()
                continue
            backoff = min(60.0, backoff * 2)

    # ------------------------------------------------------------ команды от сервера (Telegram)
    def _ack(self, cmd: str, ok: bool, msg: str = "") -> None:
        self._send({"type": "ack", "cmd": cmd, "ok": ok, "msg": msg})

    def _handle_command(self, msg: dict) -> None:
        cmd = msg.get("cmd")
        self.log(f"Команда из Telegram: {cmd}")
        try:
            if cmd == "status":
                self._send({"type": "status", "state": self.state, "work_id": self.current_work or ""})
            elif cmd == "list_works":
                works = self.refresh_works()
                self._send({"type": "works", "works": works[:10], "new": False})
            elif cmd == "start_work":
                ok, why = self.start_work(str(msg.get("work_id", "")), msg.get("mode", "auto"), remote=True)
                self._ack(cmd, ok, why)
            elif cmd == "pause":
                self.pause()
            elif cmd == "resume":
                self.resume()
            elif cmd == "stop":
                self.stop_run()
        except Exception as e:  # noqa: BLE001
            self._send({"type": "error", "message": str(e)[:300]})

    # ------------------------------------------------------------ работы
    def refresh_works(self, notify_new: bool = False) -> list[dict]:
        with self._works_lock:
            try:
                works = self.backend.list_works()
            except NotLoggedIn as e:
                first = self.login_ok is not False
                self.login_ok = False
                msg = str(e) or "Не выполнен вход в ЯКласс"
                self.emit("login_required", message=msg)       # GUI показывает при каждой проверке, не только в первый раз
                if first:
                    self._send({"type": "error", "message": "На компьютере не выполнен вход в ЯКласс"})
                return []
            except (SiteBlocked, SiteChallenge) as e:
                msg = str(e)
                if isinstance(e, SiteBlocked):
                    self._blocked_until = time.time() + BLOCK_PAUSE_S      # не заходим на сайт автоматически, пока он отклоняет запросы
                    if not self._block_reported:
                        self._block_reported = True
                        self._send({"type": "error", "message": "ЯКласс отклонил запросы приложения (403): автоматические проверки приостановлены"})
                self.log(f"ЯКласс: {msg}")
                self.emit("login_status", ok=None, message=msg)
                return []
            except Exception as e:  # noqa: BLE001 — нет браузера, куки не читаются, сайт недоступен...
                msg = str(e) if isinstance(e, BackendError) else friendly_error(e)
                self.log(f"Не удалось получить список работ: {msg}")
                self.emit("login_status", ok=None, message=msg)
                return []
            self.login_ok = True
            self._blocked_until, self._block_reported = 0.0, False
            self._last_logged = ""            # успех закрывает серию ошибок: такая же ошибка потом — снова в журнал
            self.emit("login_status", ok=True, message="")
            items = [{"id": w.work_id, "subject": w.subject, "title": w.title, "deadline": _fmt_deadline(w.deadline_utc_ms)}
                     for w in works]
            table = works_table(self.dir)
            known = {r["work_id"] for r in table.read()}
            new = [i for i in items if i["id"] not in known]
            for i in new:
                table.upsert({"work_id": i["id"], "subject": i["subject"], "title": i["title"], "status": "new",
                              "first_seen": datetime.now().isoformat(timespec="seconds")})
            self.works = items
            self.emit("works", works=items, new_ids=[i["id"] for i in new])
            if notify_new and new:
                self._send({"type": "works", "works": new[:10], "new": True})
            return items

    def should_poll(self) -> bool:
        """Можно ли сейчас заходить на сайт автоматически: приложение простаивает и сайт не отклонял запросы недавно."""
        return self.state == "idle" and time.time() >= self._blocked_until

    def poll_due(self) -> bool:
        """Автоматический опрос включён пользователем и сейчас допустим."""
        return self.store.load().auto_poll and self.should_poll()

    def _poll_loop(self) -> None:
        delay = 90.0                          # не сразу при запуске: лишний автоматический заход на сайт ни к чему
        while not self._stop.wait(delay):
            base = max(300.0, self.store.load().poll_minutes * 60.0)
            delay = base * random.uniform(0.8, 1.25)       # без строгой периодичности
            if self.poll_due():
                self.refresh_works(notify_new=True)

    # ------------------------------------------------------------ новые версии (только уведомление)
    def check_updates(self, manual: bool = False) -> None:
        """Проверка GitHub Releases. Ничего не скачивает: сообщает о версии, ссылку открывает пользователь.
        Автоматическая проверка молчит об ошибках (приватный репозиторий, нет сети); ручная — отвечает всегда."""
        s = self.store.load()
        try:
            rel, newer = updater.check(s.update_repo, updater.update_token(), api=self.update_api)
        except updater.UpdateError as e:
            if manual:
                self.emit("update_status", ok=False, message=str(e))
            return
        if newer:
            self.release = rel
            self.emit("update_available", version=rel.version, current=__version__, notes=rel.notes, url=rel.url,
                      can_install=self.update_plan.mode != "none" and updater.pick_asset(rel, self.update_plan.mode) is not None)
        elif manual:
            self.emit("update_status", ok=True, message=f"У вас последняя версия ({__version__}).")

    def install_update(self) -> None:
        """Скачать и заменить файл приложения, закрыть всё и перезапуститься. Бросает UpdateError с понятной причиной."""
        if self.state != "idle":
            raise updater.UpdateError("Идёт выполнение работы: обновитесь после её окончания.")
        if self.release is None:
            raise updater.UpdateError("Сначала проверьте обновления.")
        plan = self.update_plan
        updater.install(self.release, plan, self.dir / "updates", updater.update_token(),
                        progress=lambda done, total: self.emit("update_progress", done=done, total=total))
        self.log(f"Установлена версия {self.release.version}, перезапускаюсь…")
        self.shutdown()                        # закрыть связь, потоки и браузер до замены процесса
        self.restart_fn(plan.target, plan.mode)

    def _update_loop(self) -> None:
        delay = 20.0                          # через 20 с после запуска и далее раз в 12 часов
        while not self._stop.wait(delay):
            delay = 12 * 3600.0
            if self.store.load().check_updates:
                self.check_updates()

    def start_work(self, work_id: str, mode: str = "auto", remote: bool = False) -> tuple[bool, str]:
        s = self.store.load()
        if remote and not s.remote_start_allowed:
            return False, "Удалённый запуск отключён в настройках приложения"
        if not work_id.isdigit():
            return False, "Некорректный номер работы"
        if mode not in ("auto", "dry"):
            return False, "Некорректный режим"
        if self.state != "idle":
            return False, "Уже выполняется другая работа"
        try:
            solver = solvers.make_solver(s, self.store.token, self.store.own_api_key)
        except (FatalSolveError, SolveError) as e:
            return False, str(e)
        except Exception as e:  # noqa: BLE001
            return False, f"Не удалось подготовить решатель: {e}"
        self._control = RunControl()
        self._set_state("running", work_id)
        threading.Thread(target=self._run_work, args=(work_id, mode, solver, self._control), daemon=True,
                         name="run-work").start()
        return True, ""

    def _run_work(self, work_id: str, mode: str, solver, control: RunControl) -> None:
        s = self.store.load()
        pace = s.pace_obj().with_min_between(self.min_interval)

        def on_progress(done: int, total: int, note: str) -> None:
            self.emit("progress", work_id=work_id, done=done, total=total, note=note)
            self._send({"type": "progress", "work_id": work_id, "done": done, "total": total, "note": note})

        try:
            res = self.backend.run_work(work_id, mode, lambda task, lg: solver.solve(task, lg), pace, control,
                                        self.log, on_progress)
            summary = {STATUS_NAMES.get(k, k): v for k, v in res.summary().items()}
            payload = {"work_id": work_id, "completed": res.completed, "summary": summary, "note": res.note,
                       "stopped": res.stopped, "fatal": res.fatal}
            self.emit("finished", **payload)
            self._send({"type": "finished", **{k: payload[k] for k in ("work_id", "completed", "summary", "note")}})
        except Exception as e:  # noqa: BLE001
            self.log(f"Ошибка выполнения: {e}")
            self.emit("error", message=str(e))
            self._send({"type": "error", "message": f"Ошибка выполнения: {e}"[:300]})
        finally:
            self._control = None
            self._set_state("idle")

    def pause(self) -> None:
        if self._control and self.state == "running":
            self._control.pause()
            self._set_state("paused", self.current_work)

    def resume(self) -> None:
        if self._control and self.state == "paused":
            self._control.resume()
            self._set_state("running", self.current_work)

    def stop_run(self) -> None:
        if self._control:
            self._control.stop()

    # ------------------------------------------------------------ действия GUI
    def link(self, code: str) -> str:
        s = self.store.load()
        if not s.server_url:
            raise solvers.LinkError("Укажите адрес сервера в настройках")
        token, username = solvers.link(s.server_url, code, s.device_name)
        self.store.token, self.username = token, username
        self.reconnect()
        self.emit("linked", username=username)
        return username

    def unlink(self) -> None:
        self.store.token = ""
        self.username = ""
        self.reconnect()
        self.emit("unlinked", message="Приложение отвязано от Telegram")

    def check_login(self) -> None:
        """Проверить вход в ЯКласс (режим «куки браузера»: окно входа не нужно)."""
        def job():
            self.emit("login_status", ok=None, checking=True, message="Проверяю вход…")
            self.refresh_works()
        threading.Thread(target=job, daemon=True, name="check-login").start()

    def login_yaklass(self) -> None:
        """Профиль приложения: открыть окно для входа. Режим кук: просто проверить вход."""
        if self.store.load().browser_mode == "cookies":
            return self.check_login()

        def job():
            try:
                self.emit("login_status", ok=None, checking=True, message="Войдите в ЯКласс в открывшемся окне и закройте его")
                self.backend.login()
            except Exception as e:  # noqa: BLE001
                msg = f"Не удалось открыть окно входа: {friendly_error(e)}"
                self.log(msg)
                self.emit("login_status", ok=None, message=msg)
                return
            self.refresh_works()
        threading.Thread(target=job, daemon=True, name="login").start()

    def server_info(self) -> dict:
        s, token = self.store.load(), self.store.token
        return solvers.me(s.server_url, token)


def ws_url_for(server_url: str) -> str:
    return solvers.ws_url(server_url)
