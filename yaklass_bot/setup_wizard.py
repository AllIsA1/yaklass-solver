"""Интерактивная стартовая настройка: `yaklass-bot setup`."""
from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from .config import Config

BROWSERS = ["chrome", "chromium", "firefox", "edge", "brave", "opera", "opera_gx", "vivaldi", "librewolf",
            "safari", "arc"]
_WHICH = {"chrome": ("google-chrome", "google-chrome-stable", "chrome"), "chromium": ("chromium", "chromium-browser"),
          "firefox": ("firefox",), "edge": ("microsoft-edge", "msedge"), "brave": ("brave-browser", "brave"),
          "opera": ("opera",), "vivaldi": ("vivaldi",)}

# (название, base_url, модель, переменная ключа, нужен ключ, vision по умолчанию, json_mode)
PRESETS = [
    ("OpenAI", "https://api.openai.com/v1", "gpt-4o-mini", "OPENAI_API_KEY", True, True, False),
    ("DeepSeek", "https://api.deepseek.com/v1", "deepseek-chat", "DEEPSEEK_API_KEY", True, False, True),
    ("OpenRouter", "https://openrouter.ai/api/v1", "openai/gpt-4o-mini", "OPENROUTER_API_KEY", True, True, False),
    ("Ollama (на этой машине или в сети)", "http://localhost:11434/v1", "llama3.1", "", False, False, True),
    ("Другой OpenAI-совместимый сервер", "", "", "OPENAI_API_KEY", True, False, False),
]


class ConsoleIO:
    def say(self, text: str = "") -> None:
        print(text)

    def ask(self, prompt: str, default: str = "") -> str:
        v = input(f"{prompt}" + (f" [{default}]" if default else "") + ": ").strip()
        return v or default

    def secret(self, prompt: str) -> str:
        return getpass.getpass(f"{prompt} (ввод скрыт): ").strip()

    def yesno(self, prompt: str, default: bool = True) -> bool:
        while True:
            v = input(f"{prompt} [{'Д/н' if default else 'д/Н'}]: ").strip().lower()
            if not v:
                return default
            if v in ("д", "да", "y", "yes"):
                return True
            if v in ("н", "нет", "n", "no"):
                return False

    def choose(self, prompt: str, options: Sequence[str], default: int = 0) -> int:
        self.say(prompt)
        for i, o in enumerate(options, 1):
            self.say(f"  {i}) {o}")
        while True:
            v = input(f"Номер [{default + 1}]: ").strip()
            if not v:
                return default
            if v.isdigit() and 1 <= int(v) <= len(options):
                return int(v) - 1


# ---------- проверки (вынесены, чтобы подменять в тестах) ----------

def check_cookies(cfg: Config) -> int:
    """Сколько кук yaklass.ru нашлось в браузере (значения не читаем и не показываем)."""
    from urllib.parse import urlparse

    from .session import load_cookies
    host = urlparse(cfg.base_url).hostname or "yaklass.ru"
    return len(list(load_cookies(cfg.browser, ".".join(host.split(".")[-2:]), cfg.cookie_file)))


def check_api(cfg: Config) -> str:
    from .solver.api import ApiProvider
    return ApiProvider(cfg.api).ask("Ответь одним словом: ok").strip()[:60]


def check_searx(url: str) -> int:
    from .solver.search import search
    text = search(url, "тест", 3)
    return 0 if text.startswith("(") else text.count("\n") + 1


def chromium_installed() -> bool | None:
    """None — Playwright не установлен."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    with sync_playwright() as pw:
        return Path(pw.chromium.executable_path).exists()


def install_chromium() -> bool:
    return subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"]).returncode == 0


# ---------- шаги ----------

def _step_browser(io, cfg: Config, checks) -> None:
    detected = [b for b in BROWSERS if any(shutil.which(x) for x in _WHICH.get(b, ()))]
    labels = [b + ("  (найден)" if b in detected else "") for b in BROWSERS]
    default = BROWSERS.index(cfg.browser) if cfg.browser in BROWSERS else (
        BROWSERS.index(detected[0]) if detected else 0)
    cfg.browser = BROWSERS[io.choose("\n1. Из какого браузера брать куки? Вы должны быть в нём "
                                     "авторизованы в ЯКлассе.", labels, default)]
    if io.yesno("Проверить, что вход в ЯКласс виден скрипту? (закройте браузер, если он открыт)", True):
        try:
            n = checks.cookies(cfg)
            io.say(f"  найдено кук yaklass.ru: {n}" if n else
                   "  кук ЯКласса не найдено — войдите на сайте в этом браузере и повторите `yaklass-bot setup`.")
        except Exception as e:  # noqa: BLE001
            io.say(f"  не удалось прочитать куки: {e}")


def _step_llm(io, cfg: Config, key_file: Path, checks) -> None:
    io.say("\n2. Модель для решения заданий (любой OpenAI-совместимый API).")
    a = cfg.api
    p = PRESETS[io.choose("Какой сервер?", [x[0] for x in PRESETS], 0)]
    _, base, model, env, needs_key, vis, js = p
    a["base_url"] = io.ask("Адрес API (base_url)", base or a["base_url"])
    a["model"] = io.ask("Модель", model or a["model"])
    a["vision"] = io.yesno("Модель понимает картинки (vision)? Нужно для заданий с рисунками", vis)
    a["json_mode"] = js
    a["api_key_env"], a["api_key_file"] = "", ""
    if needs_key or io.yesno("Серверу нужен ключ API?", False):
        how = io.choose("Как хранить ключ?", [
            "Переменная окружения (рекомендуется, ключ не лежит в файлах)",
            f"Сохранить в файл {key_file} (доступ только вам)"], 0)
        if how == 0:
            a["api_key_env"] = io.ask("Имя переменной окружения", env or "OPENAI_API_KEY")
            if os.environ.get(a["api_key_env"]):
                io.say(f"  переменная {a['api_key_env']} уже задана в этом терминале")
            else:
                cmd = (f'setx {a["api_key_env"]} "ваш_ключ"' if os.name == "nt"
                       else f'export {a["api_key_env"]}="ваш_ключ"   # добавьте строку в ~/.bashrc или ~/.zshrc')
                io.say(f"  задайте её перед запуском:  {cmd}")
        else:
            secret = io.secret("Ключ API")
            if secret:
                key_file.parent.mkdir(parents=True, exist_ok=True)
                key_file.write_text(secret + "\n", encoding="utf-8")
                try:
                    key_file.chmod(0o600)
                except OSError:
                    pass
                a["api_key_file"] = str(key_file)
                io.say(f"  ключ сохранён в {key_file}")
    if io.yesno("Проверить подключение тестовым запросом? (потратит доли цента)", True):
        try:
            io.say(f"  ответ модели: {checks.api(cfg)!r}")
        except Exception as e:  # noqa: BLE001
            io.say(f"  не получилось: {e}\n  (настройки сохранятся, исправить можно позже: yaklass-bot setup)")


def _step_search(io, cfg: Config, checks) -> None:
    io.say("\n3. Поиск. Модели через API сами в интернет не ходят и в фактах (даты, названия) могут ошибаться.")
    if io.yesno("Подкладывать в запрос результаты вашего SearXNG? Рекомендуется для заданий на знание фактов",
                bool(cfg.searxng_url)):
        cfg.searxng_url = io.ask("Адрес SearXNG (в нём должен быть включён format=json)",
                                 cfg.searxng_url or "http://localhost:8080").rstrip("/")
        try:
            io.say(f"  SearXNG отвечает, найдено результатов: {checks.searx(cfg.searxng_url)}")
        except Exception as e:  # noqa: BLE001
            io.say(f"  проверка не прошла: {e}\n  (адрес сохранится; поиск будет недоступен, пока это не исправить)")
    else:
        cfg.searxng_url = ""


def _step_run_browser(io, cfg: Config, checks) -> None:
    found = [(ch, label) for ch, label, names in
             (("chrome", "Google Chrome", ("google-chrome", "google-chrome-stable", "chrome")),
              ("msedge", "Microsoft Edge", ("microsoft-edge", "msedge")))
             if any(shutil.which(n) for n in names)]
    options = ["Встроенный Chromium Playwright (скачивается один раз, ~150 МБ)"] + \
              [f"Установленный {label}" for _, label in found]
    cur = next((i for i, (ch, _) in enumerate(found, 1) if ch == cfg.run.get("channel")), 0)
    pick = io.choose("\n4. Браузер для режима `run` (в нём скрипт заполняет задания):", options,
                     cur or (1 if found else 0))
    cfg.run["channel"] = found[pick - 1][0] if pick else ""
    if pick == 0:
        state = checks.chromium()
        if state is None:
            io.say("  Playwright не установлен: pip install \"yaklass-bot[browser]\"")
        elif not state and io.yesno("  Chromium ещё не скачан. Скачать сейчас?", True):
            io.say("  скачиваю..." if checks.install_chromium() else "  не удалось, попробуйте: playwright install chromium")


def run_wizard(cfg: Config, path: Path, io=None, checks=None) -> Config:
    io = io or ConsoleIO()
    checks = checks or _Checks()
    io.say("Стартовая настройка yaklass-bot (Ctrl+C — отмена, ничего не сохранится)")
    io.say(f"Файл настроек: {path}")
    _step_browser(io, cfg, checks)
    _step_llm(io, cfg, path.parent / "api_key", checks)
    _step_search(io, cfg, checks)
    _step_run_browser(io, cfg, checks)
    cfg.save(path)
    io.say(f"\nГотово, настройки сохранены в {path}\n"
           "Попробуйте:  yaklass-bot run --dry-run --no-start\n"
           "(тренировочное задание: yaklass-bot run \"<ссылка>\" --dry-run)")
    return cfg


class _Checks:
    cookies = staticmethod(check_cookies)
    api = staticmethod(check_api)
    searx = staticmethod(check_searx)
    chromium = staticmethod(chromium_installed)
    install_chromium = staticmethod(install_chromium)
