"""Работа с браузером: список работ, вход в ЯКласс, выполнение работы. Playwright живёт в ОДНОМ потоке
(его sync-API нельзя использовать из разных потоков), остальные потоки отправляют ему задания."""
from __future__ import annotations

import glob
import json
import os
import queue
import shutil
import sys
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path

from yaklass_bot.autorun import RunResult, execute
from yaklass_bot.control import RunControl
from yaklass_bot.guard import BLOCKED_MSG, SiteBlocked, check_page, is_blocked, page_title
from yaklass_bot.models import Work
from yaklass_bot.pace import Pace
from yaklass_bot.parser import parse_preview, parse_works_list
from yaklass_bot.runner import wait_page_ready
from yaklass_bot.session import load_cookies, to_playwright_cookies
from yaklass_bot.storage import tasks_table

from .settings import Store, data_dir


class NotLoggedIn(RuntimeError):
    """В браузере нет входа в ЯКласс."""


class BackendError(RuntimeError):
    """Сбой с понятной пользователю причиной (не про вход): нет браузера, не читаются куки и т.п."""


def friendly_error(e: BaseException) -> str:
    """Техническое исключение (Playwright и пр.) -> сообщение, из которого понятно, что делать."""
    t = str(e)
    low = t.lower()
    if "executable doesn't exist" in low or "playwright install" in low:
        return ("Не найден браузер для выполнения. Установите Google Chrome / Microsoft Edge и выберите его в "
                "Настройки → Браузер или нажмите «Скачать Chromium» там же.")
    if "failed to launch" in low or "browser has been closed" in low:
        return "Браузер не запустился или был закрыт. Проверьте выбор браузера в Настройки → Браузер."
    return t.strip().splitlines()[0][:300] if t.strip() else e.__class__.__name__


def install_chromium(log: Callable[[str], None]) -> bool:
    """Скачивает Chromium для Playwright (~150 МБ) штатным установщиком из пакета playwright."""
    import subprocess

    from playwright._impl._driver import compute_driver_executable, get_driver_env
    ensure_browsers_path()
    exe, cli = compute_driver_executable()
    proc = subprocess.Popen([exe, cli, "install", "chromium"], env=get_driver_env(), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    last = ""
    for line in proc.stdout or []:
        line = line.strip()
        if line and line != last and not set(line) <= set("|■ 0123456789%.ofMiB"):   # без шума полосы загрузки
            log(line)
            last = line
    return proc.wait() == 0


def playwright_cache_dir() -> Path:
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if env and env != "0":
        return Path(env)
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "ms-playwright"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def ensure_browsers_path() -> None:
    """В приложении, собранном PyInstaller, Playwright по умолчанию ищет браузеры ВНУТРИ самого приложения
    (PLAYWRIGHT_BROWSERS_PATH=0, см. playwright/_impl/_transport.py) — там их нет и писать нельзя (AppImage только для чтения).
    Явно указываем обычный кэш (~/.cache/ms-playwright и т.п.): и поиск, и «Скачать Chromium» работают в одном месте."""
    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(playwright_cache_dir())


def find_system_browsers() -> list[tuple[str, str]]:
    """Браузеры на этом компьютере, пригодные для Playwright (семейство Chromium): [(название, путь)].
    Сначала скачанные Playwright (любой версии — лучше «не та» ревизия, чем никакой), затем системные."""
    found: list[tuple[str, str]] = []
    cache = playwright_cache_dir()
    pats = {"win32": ["chromium-*/chrome-win*/chrome.exe"],
            "darwin": ["chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium"]}.get(
        sys.platform, ["chromium-*/chrome-linux*/chrome"])
    for pat in pats:
        for path in sorted(glob.glob(str(cache / pat)), reverse=True):          # новые ревизии первыми
            if os.access(path, os.X_OK):
                found.append((f"Chromium Playwright ({Path(path).parents[1].name})", path))
    names = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
             "microsoft-edge-stable", "brave-browser"]
    for n in names:
        p = shutil.which(n)
        if p:
            found.append((n, p))
    if sys.platform == "win32":
        for root in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            for rel in (r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe"):
                f = Path(root) / rel
                if f.exists():
                    found.append((f.parent.parent.name, str(f)))
    seen, out = set(), []
    for label, path in found:
        if path not in seen:
            seen.add(path)
            out.append((label, path))
    return out


MISSING_MARKERS = ("executable doesn't exist", "is not found at", "playwright install", "no such file or directory",
                   "failed to launch", "cannot execute", "permission denied")


def is_browser_missing(e: BaseException) -> bool:
    t = str(e).lower()
    return any(m in t for m in MISSING_MARKERS)


class Backend(ABC):
    @abstractmethod
    def list_works(self) -> list[Work]: ...

    @abstractmethod
    def run_work(self, work_id: str, mode: str, solve_fn, pace: Pace, control: RunControl,
                 log: Callable[[str], None], on_progress: Callable[[int, int, str], None]) -> RunResult: ...

    @abstractmethod
    def login(self) -> None: ...

    def close(self) -> None:
        pass


class PlaywrightBackend(Backend):
    def __init__(self, store: Store, directory: Path | None = None):
        self.store = store
        self.dir = directory or data_dir()
        self._jobs: "queue.Queue[tuple[Callable, Future] | None]" = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="playwright", daemon=True)
        self._pw = None
        self._lingering: list = []            # окна, оставленные открытыми для ручного завершения
        self._thread.start()

    # ---- очередь заданий для потока Playwright ----
    def _loop(self) -> None:
        while True:
            item = self._jobs.get()
            if item is None:
                break
            fn, fut = item
            try:
                fut.set_result(fn())
            except BaseException as e:  # noqa: BLE001 — передаём исключение вызывающему
                fut.set_exception(e)
        self._close_lingering()
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:  # noqa: BLE001
                pass

    def _call(self, fn: Callable):
        fut: Future = Future()
        self._jobs.put((fn, fut))
        return fut.result()

    def close(self) -> None:
        self._jobs.put(None)
        self._thread.join(timeout=10)

    # ---- браузер ----
    def _playwright(self):
        if self._pw is None:
            ensure_browsers_path()
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
        return self._pw

    def _close_lingering(self) -> None:
        for closer in self._lingering:
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass
        self._lingering.clear()

    def attempts(self, s) -> list[tuple[str, dict]]:
        """Порядок подбора браузера: свой путь -> выбранный канал -> встроенный Chromium -> любой найденный на ПК.
        Последний удавшийся вариант пробуется первым."""
        out: list[tuple[str, dict]] = []
        if s.browser_path:
            out.append((f"указанный путь {s.browser_path}", {"executable_path": s.browser_path}))
        if s.channel:
            out.append((f"канал {s.channel}", {"channel": s.channel}))
        out.append(("встроенный Chromium Playwright", {}))
        used = {s.browser_path}
        for label, path in find_system_browsers():
            if path not in used:
                used.add(path)
                out.append((label, {"executable_path": path}))
        good = getattr(self, "_good", None)
        if good and good in out:
            out.remove(good)
            out.insert(0, good)
        return out

    def _launch_any(self, s, make):
        """make(kwargs) -> объект браузера/контекста. Пробует варианты по порядку; если не нашёлся ни один —
        BackendError с перечнем того, что пробовали. Другие ошибки (не «нет браузера») пробрасываются как есть."""
        tried = []
        for label, kw in self.attempts(s):
            try:
                obj = make(kw)
            except Exception as e:  # noqa: BLE001
                if not is_browser_missing(e):
                    raise
                first = (str(e).strip().splitlines() or [""])[0][:110]
                tried.append(f"  • {label}: {first}")
                continue
            self._good = (label, kw)
            self.last_browser = label
            return obj
        raise BackendError("Не найден браузер для выполнения. Пробовал:\n" + "\n".join(tried) +
                           "\nУстановите Google Chrome / Chromium / Microsoft Edge, либо укажите путь к нему в "
                           "Настройки → Браузер, либо нажмите «Скачать Chromium».")

    # ---- куки профиля ----
    # Куки входа в ЯКласс — СЕССИОННЫЕ (без срока жизни). Chromium не сохраняет такие куки на диск при закрытии,
    # поэтому вход в профиле приложения «забывался» до следующего запуска. Снимок куки храним сами (права 600).
    cookie_domain_filter = "yaklass"

    @property
    def cookies_path(self) -> Path:
        return self.dir / "session-cookies.json"

    def _snapshot(self, ctx) -> None:
        try:
            cs = [c for c in ctx.cookies() if self.cookie_domain_filter in c.get("domain", "")]
        except Exception:  # noqa: BLE001 — контекст уже закрывается
            return
        if not cs:
            return                                   # пустым снимком не затираем сохранённое
        self.cookies_path.parent.mkdir(parents=True, exist_ok=True)
        self.cookies_path.write_text(json.dumps(cs), encoding="utf-8")
        try:
            self.cookies_path.chmod(0o600)
        except OSError:
            pass

    def _restore(self, ctx) -> None:
        try:
            cs = json.loads(self.cookies_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        horizon = time.time() + 14 * 86400
        for c in cs:
            if not c.get("expires") or c["expires"] <= 0:
                c["expires"] = horizon               # сессионную куку делаем «долгой» — иначе браузер её не отдаст
        try:
            ctx.add_cookies(cs)
        except Exception:  # noqa: BLE001
            pass

    def _open(self, headless: bool):
        """-> (context, closer). Вызывать только из потока Playwright (через _call)."""
        s = self.store.load()
        cookies = None
        if s.browser_mode != "profile":
            from urllib.parse import urlparse
            host = urlparse(s.base_url).hostname or "yaklass.ru"
            try:                                     # куки читаем ДО запуска браузера: дешёвая проверка и понятная причина
                cookies = to_playwright_cookies(load_cookies(s.cookie_browser, ".".join(host.split(".")[-2:])))
            except Exception as e:  # noqa: BLE001 — браузер не найден, БД кук заблокирована, шифрование...
                raise BackendError(f"Не удалось прочитать куки из {s.cookie_browser}: {e}") from e
            if not cookies:
                raise NotLoggedIn(f"В {s.cookie_browser} нет входа в ЯКласс: войдите на сайте в этом браузере")
        pw = self._playwright()
        if s.browser_mode == "profile":
            profile = self.dir / "profile"
            profile.mkdir(parents=True, exist_ok=True)
            ctx = self._launch_any(s, lambda kw: pw.chromium.launch_persistent_context(
                str(profile), headless=headless, no_viewport=True, **kw))
            self._restore(ctx)
            return ctx, ctx.close
        browser = self._launch_any(s, lambda kw: pw.chromium.launch(headless=headless, **kw))
        ctx = browser.new_context()
        ctx.add_cookies(cookies)
        return ctx, browser.close

    @staticmethod
    def wait_or_guard(page, selector: str, timeout_s: float) -> bool:
        """Ждёт элемент; на странице блокировки сразу бросает SiteBlocked, в конце — SiteBlocked/SiteChallenge,
        если вместо страницы заглушка защиты. True — элемент найден, False — не появился (страница обычная)."""
        end = time.time() + timeout_s
        while time.time() < end:
            try:
                if page.locator(selector).count():
                    return True
                html = page.content()
            except Exception:  # noqa: BLE001 — страница перезагружается
                time.sleep(0.5)
                continue
            if is_blocked(html, page_title(html)):
                raise SiteBlocked(BLOCKED_MSG)
            time.sleep(0.5)
        try:
            check_page(page.content())
        except SiteBlocked:
            raise
        except Exception:  # SiteChallenge и пр.
            raise
        return False

    # ---- операции ----
    def list_works(self) -> list[Work]:
        # Только настоящий браузер и ВИДИМОЕ окно: обычный HTTP-запрос ЯКласс закрывает JS-проверкой (servicepipe),
        # а скрытый (headless) браузер получал «403 подозрительная активность» — как и при входе/выполнении, открываем окно
        def job():
            self._close_lingering()
            s = self.store.load()
            ctx, closer = self._open(headless=False)
            try:
                page = ctx.new_page()
                page.goto(s.base_url + "/TestWork")
                if not self.wait_or_guard(page, ".wg-testworks", 25):
                    raise NotLoggedIn("Не выполнен вход в ЯКласс: раздел работ не найден")
                works = parse_works_list(page.content())
                if s.browser_mode == "profile":
                    self._snapshot(ctx)
                return works
            finally:
                closer()
        return self._call(job)

    def login(self) -> None:
        def job():
            self._close_lingering()
            s = self.store.load()
            ctx, closer = self._open(headless=False)
            try:
                page = ctx.new_page()
                page.goto(s.base_url + "/TestWork")
                while True:                          # пока открыто окно — периодически снимаем куки (сессионные!)
                    time.sleep(1.0)
                    try:
                        if not ctx.pages:
                            break
                    except Exception:  # noqa: BLE001
                        break
                    self._snapshot(ctx)
            finally:
                try:
                    closer()
                except Exception:  # noqa: BLE001
                    pass
        self._call(job)

    def run_work(self, work_id, mode, solve_fn, pace, control, log, on_progress) -> RunResult:
        def job():
            self._close_lingering()
            s = self.store.load()
            base = s.base_url
            ctx, closer = self._open(headless=not s.show_browser)
            keep = False
            try:
                page = ctx.new_page()
                page.goto(f"{base}/TestWorkRun/Preview/{work_id}")
                try:
                    self.wait_or_guard(page, "#taskhtml, #test-preview", 30)
                except Exception as e:  # noqa: BLE001
                    if e.__class__.__name__ in ("SiteBlocked", "SiteChallenge"):
                        return RunResult(fatal=str(e), note=f"остановлено: {e}")
                    raise
                if page.locator("#test-preview").count():
                    info = parse_preview(page.content())
                    if not info.start_allowed:
                        return RunResult(note="Запуск невозможен: нет кнопки «Начать» или не осталось попыток")
                    log(f"Нажимаю «{info.start_label}»" + ("" if info.is_continue else " (тратится попытка)"))
                    page.locator('#start-action-btn, form[action*="/TestWorkRun/Start/"] button').first.click()
                wait_page_ready(page)
                res = execute(page, base, solve_fn, tasks_table(self.dir), log, submit=(mode == "auto"), pace=pace,
                              control=control, on_progress=on_progress, screenshot_dir=self.dir / "runs")
                if s.browser_mode == "profile":
                    self._snapshot(ctx)
                keep = not res.completed and not res.fatal and not (res.stopped and mode == "auto")
                if keep:                       # окно остаётся: проверить заполнение / доделать вручную
                    self._lingering.append(closer)
                return res
            finally:
                if not keep:
                    try:
                        closer()
                    except Exception:  # noqa: BLE001
                        pass
        return self._call(job)
