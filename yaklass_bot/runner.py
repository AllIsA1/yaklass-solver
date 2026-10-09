"""Тестовый режим (--dry-run): открывает страницу в видимом браузере, решает и
выставляет ответы, но НЕ отправляет их. Дальше человек сам жмёт «Ответить» и переходит.
"""
from __future__ import annotations

import time
from urllib.parse import urldefrag, urljoin, urlparse

from .config import Config
from .filler import fill_task
from .parser import page_kind, parse_exercise, parse_preview, parse_works_list
from .session import DEFAULT_UA, load_cookies, to_playwright_cookies
from .pipeline import solve
from .solver import SolveError, make_provider

POLL_S = 1.0
START_BTN = '#start-action-btn, form[action*="/TestWorkRun/Start/"] button'


def _limit_text(info) -> str:
    return f"{info.time_limit_min} мин" if info.time_limit_min else "без ограничения по времени"


def handle_preview(page, info, auto_start: bool, delay: int) -> bool:
    """Страница старта работы. «Начать» необратимо тратит попытку и запускает таймер —
    поэтому показываем условия и даём `delay` секунд на отмену (Ctrl+C)."""
    print(f"\n── Работа «{info.title}» ──")
    print(f"Заданий: {info.tasks_count or '?'}, баллов: {info.max_points or '?'}, "
          f"время: {_limit_text(info)}, попыток осталось: "
          f"{info.attempts_left if info.attempts_left is not None else 'не указано'}")
    if not info.can_start:
        print("Кнопки запуска на странице нет (работа недоступна или уже завершена).")
        return False
    if info.attempts_left == 0:
        print("Попыток не осталось — запуск не выполняется.")
        return False
    if not auto_start:
        print(f"Автозапуск отключён (--no-start): нажмите «{info.start_label}» сами.")
        return False
    note = "" if info.is_continue else " — это тратит попытку"
    print(f"Нажимаю «{info.start_label}» через {delay} с{note} (Ctrl+C — отмена)...")
    for _ in range(delay):
        time.sleep(1)
    page.locator(START_BTN).first.click()


def choose_work(page, cfg: Config) -> bool:
    """Начальная страница — список работ: выбрать работу и открыть её страницу старта."""
    works = parse_works_list(page.content())
    if not works:
        print("Новых работ нет.")
        return False
    if len(works) == 1:
        pick = works[0]
    else:
        print("\nНовые работы:")
        for i, w in enumerate(works, 1):
            print(f"  {i}) {w.subject} — {w.title}")
        try:
            pick = works[int(input("Номер работы: ")) - 1]
        except (ValueError, IndexError, EOFError):
            print("Работа не выбрана.")
            return False
    page.goto(resolve_url(cfg, pick.preview_url))
    return True


def wait_page_ready(page, timeout_ms: int = 8000) -> None:
    """Ждём, пока JS сайта превратит заглушки формул (mi.ykl-input) в настоящие поля ввода.
    Быстрее и надёжнее, чем networkidle (счётчики и реклама держат сеть занятой вечно)."""
    page.wait_for_selector("#taskhtml", timeout=30_000)
    try:
        page.wait_for_function(
            "() => document.querySelectorAll('input.gxst-formula-textbox').length"
            " >= document.querySelectorAll('mi.ykl-input').length", timeout=timeout_ms)
    except Exception:  # noqa: BLE001 — не критично: filler сам сообщит, если поле не найдено
        pass


def resolve_url(cfg: Config, url: str) -> str:
    full = urljoin(cfg.base_url + "/", url)
    if urlparse(full).hostname != urlparse(cfg.base_url).hostname:
        raise ValueError(f"Адрес должен быть на {urlparse(cfg.base_url).hostname}: {url}")
    return full


def _show(task, answer, results) -> None:
    print(f"\n── Задание {task.position}: {task.title} ──")
    low = answer.confidence < 0.5
    print(f"Уверенность модели: {answer.confidence:.2f}"
          + ("  ⚠ низкая — проверьте сами" if low else "  (оценка самой модели, не гарантия)"))
    if answer.evidence:
        print(f"Основание: {answer.evidence}")
    for r in results:
        mark = "✓" if r.ok else "✗"
        print(f"  {mark} {r.field}: {r.wanted}" + (f"   [{r.note}]" if r.note else ""))
    if not all(r.ok for r in results):
        print("  ⚠ часть полей выставить не удалось — заполните их вручную")
    print("Проверьте ответы и нажмите «Ответить» сами (скрипт кнопку не трогает).")


def process_page(page, cfg: Config, provider, auto_start: bool = True, delay: int = 5,
                 pick_work: bool = False) -> None:
    try:
        page.wait_for_selector("#taskhtml, #test-preview, .wg-testworks", timeout=15_000)
        if page.locator("#taskhtml").count():
            wait_page_ready(page)
    except Exception:  # noqa: BLE001
        pass
    if page.locator("#test-preview").count():
        handle_preview(page, parse_preview(page.content()), auto_start, delay)
        return
    if pick_work and page.locator(".wg-testworks").count():
        choose_work(page, cfg)
        return
    html = page.content()
    kind = page_kind(html)
    if kind == "other":
        return
    task = parse_exercise(html)
    if task.is_theory:
        print(f"\n«{task.title}» — теория, отвечать не на что. Листайте дальше.")
        return
    if not task.supported:
        print(f"\n── Задание {task.position}: {task.title} ── пропуск (аудио или нет полей ответа)")
        return
    try:
        answer = solve(cfg, provider, task, log=lambda m: print(m, flush=True))
    except SolveError as e:
        print(f"\n── Задание {task.position}: {task.title} ── не удалось получить ответ: {e}")
        return
    _show(task, answer, fill_task(page, task, answer))


def run_dry(cfg: Config, url: str, auto_start: bool = True, start_delay: int = 5) -> None:
    try:
        from playwright.sync_api import Error as PwError
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError("Нужен Playwright: pip install playwright && playwright install chromium") from e

    start = resolve_url(cfg, url)
    host = urlparse(cfg.base_url).hostname or "yaklass.ru"
    domain = ".".join(host.split(".")[-2:])
    provider = None
    try:
        with sync_playwright() as pw:
            provider = make_provider(cfg)
            channel = cfg.run.get("channel") or None
            browser = pw.chromium.launch(headless=False, channel=channel)
            ctx = browser.new_context(user_agent=cfg.run.get("user_agent") or DEFAULT_UA)
            ctx.add_cookies(to_playwright_cookies(load_cookies(cfg.browser, domain, cfg.cookie_file)))
            page = ctx.new_page()
            page.goto(start)
            print("Тестовый режим: ответы выставляются, но не отправляются. Закройте окно браузера для выхода.")
            last = None
            first = True
            try:
                while not page.is_closed():
                    cur = urldefrag(page.url)[0]
                    if cur != last:
                        last = cur
                        try:
                            process_page(page, cfg, provider, auto_start, start_delay, pick_work=first)
                            first = False
                        except PwError as e:
                            if page.is_closed():
                                break
                            print(f"Ошибка страницы: {e}")
                    time.sleep(POLL_S)
            finally:
                provider.close()
                provider = None
    except KeyboardInterrupt:
        pass
    finally:
        if provider is not None:       # упали до входа в цикл
            provider.close()
