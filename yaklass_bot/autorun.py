"""Автономное выполнение работы: старт -> задания с первого по последнее: решить, заполнить и ОТПРАВИТЬ
(или ПЕРЕПИСАТЬ уже данный ответ кнопкой «Сохранить») -> завершить работу.

Это единственное место, где что-то отправляется. Из консоли запускается флагом `run --auto`, из приложения —
командой пользователя. Режим `submit=False` только заполняет ответы (для проверки).
Шаг завершения написан по сэмплам страницы «Завершение работы» (форма /TestWorkRun/CompleteTest);
на живом сайте он не проверялся: если что-то не находится, работа остаётся открытой для ручного завершения.
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

from .config import Config
from .control import RunControl, Stopped
from .filler import fill_task
from .guard import SiteBlocked, SiteChallenge, check_page
from .models import Task
from .pace import Pace
from .parser import page_kind, parse_exercise, parse_nav, parse_overview, parse_preview
from .pipeline import solve
from .runner import choose_work, handle_preview, resolve_url, wait_page_ready
from .session import DEFAULT_UA, load_cookies, to_playwright_cookies
from .solver import FatalSolveError, SolveError, make_provider
from .solver.base import Answer
from .storage import tasks_table

NEW_BTN = "#submitAnswerBtn"          # «Ответить» — на задание ещё нет ответа
REWRITE_BTN = "#correctAnswerBtn"     # «Сохранить» — ответ уже дан, переписываем (активна, только если ответ изменён)
SUBMIT_BTN = f"{NEW_BTN}, {REWRITE_BTN}"
COMPLETE_BTN = ('form[action*="/TestWorkRun/CompleteTest"] button[type=submit], '
                'form[action*="/TestWorkRun/CompleteTest"] input[type=submit]')
CONFIRM_RX = re.compile(r"^\s*(да|заверш|подтверд|ок\b|ok\b|сдать)", re.I)
MIN_SECONDS_LEFT = 45         # меньше — уже не решаем, сразу к завершению
SUBMIT_WAIT_S = 12

SolveFn = Callable[[Task, Callable[[str], None]], Answer]       # (задание, log) -> ответ
Log = Callable[[str], None]


@dataclass
class Outcome:
    position: int
    title: str
    # answered | rewritten | unchanged | skipped_done | filled (без отправки) | theory | unsupported | failed | timeout
    status: str
    note: str = ""
    confidence: float | None = None


STATUS_NAMES = {"answered": "новых ответов", "rewritten": "переписано", "unchanged": "без изменений",
                "skipped_done": "пропущено", "theory": "теория", "unsupported": "без поддержки",
                "failed": "ошибки", "timeout": "не успели", "filled": "заполнено"}
SENT = ("answered", "rewritten", "unchanged", "skipped_done")
PENDING = ("failed", "unsupported", "timeout")


@dataclass
class RunResult:
    outcomes: list[Outcome] = field(default_factory=list)
    completed: bool = False          # работа завершена на сайте
    stopped: bool = False            # остановлено пользователем
    fatal: str = ""                  # причина аварийной остановки (лимит, сервер недоступен)
    note: str = ""

    @property
    def pending(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.status in PENDING]

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for o in self.outcomes:
            out[o.status] = out.get(o.status, 0) + 1
        return out


def attempt_id(url: str) -> str:
    q = parse_qs(urlparse(url).query)
    return (q.get("testResultId") or q.get("twId") or ["unknown"])[0]


def _pw_error():
    from playwright.sync_api import Error
    return Error


def submit_answer(page) -> tuple[bool, str, str]:
    """Нажимает «Ответить» (новый ответ) или «Сохранить» (переписывание).
    Возвращает (успех, заметка, вид: answered | rewritten | unchanged)."""
    PwError = _pw_error()
    rewrite = page.locator(REWRITE_BTN).count() > 0
    btn = page.locator(REWRITE_BTN if rewrite else NEW_BTN).first
    btn.scroll_into_view_if_needed()
    if rewrite:
        # кнопка «Сохранить» активируется страницей при изменении ответа; не активна = ответ тот же
        end = time.time() + 3
        while time.time() < end and btn.is_disabled():
            time.sleep(0.2)
        if btn.is_disabled():
            return True, "ответ совпадает с сохранённым — менять нечего", "unchanged"
    before = page.url
    page.evaluate("window.__yk_marker = 1")
    btn.click()
    kind = "rewritten" if rewrite else "answered"
    end = time.time() + SUBMIT_WAIT_S
    while time.time() < end:
        time.sleep(0.4)
        try:
            if page.url != before or page.evaluate("window.__yk_marker === undefined"):
                return True, "", kind
            err = page.locator("#errorSummary").inner_text().strip()
            if err:
                return False, err, kind
        except PwError:
            return True, "", kind        # контекст уничтожен — страница уходит на другой адрес
    return True, "явного подтверждения отправки нет (возможно, ответ принят без перезагрузки)", kind


def _wait_task_page(page) -> None:
    try:
        wait_page_ready(page)
    except Exception:  # noqa: BLE001
        pass


def do_task(page, solve_fn: SolveFn, table, key: str, log: Log, *, submit: bool = True,
            pace: Pace | None = None, control: RunControl | None = None) -> Outcome:
    pace = pace or Pace()
    html = page.content()
    if page_kind(html) == "other":
        check_page(html)                       # блокировка IP / проверка «не робот» -> SiteBlocked/SiteChallenge
        return Outcome(0, "", "failed", f"неожиданная страница: {page.url}")
    task = parse_exercise(html)
    base = dict(position=task.position, title=task.title)
    if task.is_theory:
        return Outcome(**base, status="theory")
    if not task.supported:
        return Outcome(**base, status="unsupported", note="аудио или нет полей ответа")
    if task.time_left_s is not None and task.time_left_s < MIN_SECONDS_LEFT:
        return Outcome(**base, status="timeout", note=f"осталось {task.time_left_s} с")
    if page.locator(REWRITE_BTN).count():
        log("  задание уже выполнено — перезаписываю ответ")
    t0 = time.time()
    if control:
        control.checkpoint()
    try:
        answer = solve_fn(task, log)
    except FatalSolveError:
        raise
    except SolveError as e:
        return Outcome(**base, status="failed", note=f"нет ответа модели: {e}")
    results = fill_task(page, task, answer, pace, control)
    if not all(r.ok for r in results):
        time.sleep(1.0)
        results = fill_task(page, task, answer, pace, control)         # одна повторная попытка
    bad = [r for r in results if not r.ok]
    if bad:
        return Outcome(**base, status="failed", confidence=answer.confidence,
                       note="не удалось заполнить: " + "; ".join(f"{r.field} ({r.note or 'не встало'})" for r in bad))
    if not submit:
        return Outcome(**base, status="filled", confidence=answer.confidence)
    pace.wait(pace.before_submit, control)
    ok, note, kind = submit_answer(page)
    if not ok:
        return Outcome(**base, status="failed", confidence=answer.confidence, note=f"сайт не принял ответ: {note}")
    table.upsert({"task_key": key, "work_id": key.split(":")[0], "position": task.position, "status": kind,
                  "answer_json": json.dumps(answer.values, ensure_ascii=False), "confidence": f"{answer.confidence:.2f}",
                  "spent_s": int(time.time() - t0)})
    return Outcome(**base, status=kind, confidence=answer.confidence, note=note)


def _visible(loc) -> bool:
    try:
        return loc.count() > 0 and loc.first.is_visible()
    except Exception:  # noqa: BLE001
        return False


def finish_work(page, base_url: str, log: Log, finish_anyway: bool = False) -> bool:
    """Открывает «Список заданий» и жмёт «Завершить» в форме завершения. Форма есть, только когда на все
    задания дан ответ. Не проверено на живом сайте."""
    PwError = _pw_error()
    link = page.locator("a.test-ex-nav-v2__overview").first
    if link.count():
        page.goto(urljoin(page.url, link.get_attribute("href")))
        page.wait_for_load_state("load")
    ov = parse_overview(page.content())
    if not ov.can_complete:
        if ov.unanswered:
            log("  на странице завершения без ответа: " + ", ".join(r.title for r in ov.unanswered))
        if not finish_anyway:
            log("  формы завершения нет — работа не завершена")
            return False
        btn = page.locator("#finishTestBtn")            # путь «Завершить» с недоотвеченной работы (непроверенный)
        if not btn.count():
            return False
        btn.first.click()
        time.sleep(1.5)
        if not page.locator(COMPLETE_BTN).count():
            return False
    ctl = page.locator(COMPLETE_BTN).first
    if not _visible(ctl):
        return False
    page.once("dialog", lambda d: d.accept())            # на случай нативного confirm()
    before = page.url
    ctl.click()
    time.sleep(1.5)
    try:
        modal = page.locator("[role=dialog] button, .ui-dialog button, .yk-dialog button, .modal button") \
            .filter(has_text=CONFIRM_RX)
        if _visible(modal):
            modal.first.click()
        page.wait_for_url(lambda u: u != before, timeout=20_000)
    except PwError:
        pass
    return True


def _work_loop(page, solve_fn: SolveFn, table, log: Log, outcomes: list[Outcome], *, resume: bool = False,
               submit: bool = True, pace: Pace | None = None, control: RunControl | None = None,
               on_progress: Callable[[int, int, str], None] | None = None) -> list[Outcome]:
    """Добавляет результаты в `outcomes` по мере выполнения (чтобы при остановке они не терялись)."""
    pace = pace or Pace()
    _wait_task_page(page)
    html0 = page.content()
    if page_kind(html0) == "other":
        check_page(html0)                       # блокировка / проверка «не робот» -> понятная причина
        raise ValueError(f"неожиданная страница вместо задания: {page.url}")
    first = parse_exercise(html0)
    attempt = attempt_id(page.url)
    if attempt == "unknown":
        attempt = attempt_id(first.form_action)      # адрес мог не содержать id попытки — он есть в action формы
    nav = parse_nav(page.content())
    hrefs = {n.position: n.href for n in nav if n.href}
    positions = sorted(n.position for n in nav) or [first.position]
    total = len(positions)
    log(f"\nПопытка {attempt}: заданий {total}; прохожу с первого по последнее")
    for idx, pos in enumerate(positions):
        if control:
            control.checkpoint()
        key = f"{attempt}:{pos}"
        prev = table.get(key)
        if resume and prev and prev["status"] in SENT:
            outcomes.append(Outcome(pos, "", "skipped_done", "уже отправлено в этой попытке (--resume)"))
            continue
        html = page.content()
        here = parse_exercise(html).position if page_kind(html) != "other" else -1
        if here != pos:
            if pos not in hrefs:
                outcomes.append(Outcome(pos, "", "failed", "нет ссылки на задание"))
                continue
            page.goto(urljoin(page.url, hrefs[pos]))
            _wait_task_page(page)
        hrefs.update({n.position: n.href for n in parse_nav(page.content()) if n.href})
        hrefs[pos] = page.url
        log(f"\n── Задание {pos}/{total} ──")
        o = do_task(page, solve_fn, table, key, log, submit=submit, pace=pace, control=control)
        o.position = o.position or pos
        outcomes.append(o)
        log(f"  результат: {o.status}" + (f" ({o.note})" if o.note else "")
            + (f", уверенность {o.confidence:.2f}" if o.confidence is not None else ""))
        if on_progress:
            on_progress(idx + 1, total, f"задание {pos}: {o.status}")
        if o.status == "timeout":
            break
        if idx < total - 1:
            pace.wait(pace.between_tasks, control)
    return outcomes


def execute(page, base_url: str, solve_fn: SolveFn, table, log: Log, *, resume: bool = False,
            finish_anyway: bool = False, submit: bool = True, pace: Pace | None = None,
            control: RunControl | None = None, on_progress: Callable[[int, int, str], None] | None = None,
            screenshot_dir: Path | None = None) -> RunResult:
    """Проход по работе с первого задания до последнего и (при полном успехе) завершение.
    Страница `page` уже должна быть на первом/текущем задании."""
    res = RunResult()
    try:
        _work_loop(page, solve_fn, table, log, res.outcomes, resume=resume, submit=submit, pace=pace,
                   control=control, on_progress=on_progress)
    except Stopped:
        res.stopped = True
        res.note = "остановлено пользователем"
        return res
    except FatalSolveError as e:
        res.fatal = str(e)
        res.note = f"остановлено: {e}"
        return res
    except (SiteBlocked, SiteChallenge, ValueError) as e:
        res.fatal = str(e)
        res.note = f"остановлено: {e}"
        return res
    if not submit:
        res.note = "режим без отправки: ответы выставлены, но не отправлены"
        return res
    if res.pending and not finish_anyway:
        res.note = "остались задания без ответа — работа не завершена, доделайте вручную"
        return res
    res.completed = finish_work(page, base_url, log, finish_anyway)
    if res.completed and screenshot_dir is not None:
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        path = screenshot_dir / f"{attempt_id(page.url)}-finished.png"
        page.screenshot(path=str(path), full_page=True)
        res.note = f"снимок итоговой страницы: {path}"
    elif not res.completed:
        res.note = "не удалось завершить работу автоматически"
    return res


def run_auto(cfg: Config, url: str, *, assume_yes: bool = False, finish_anyway: bool = False,
             resume: bool = False, start_delay: int = 5) -> list[Outcome]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError("Нужен Playwright: pip install playwright && playwright install chromium") from e
    if not assume_yes:
        print("АВТОНОМНЫЙ РЕЖИМ: скрипт сам нажмёт «Начать/Продолжить», ОТПРАВИТ ответы на все задания "
              "(уже выполненные — перезапишет) и завершит работу. Исправить после этого ничего нельзя.")
        if input("Продолжить? Введите «да»: ").strip().lower() not in ("да", "yes", "y"):
            print("Отменено.")
            return []

    start = resolve_url(cfg, url)
    host = urlparse(cfg.base_url).hostname or "yaklass.ru"
    domain = ".".join(host.split(".")[-2:])
    table = tasks_table(cfg.data_dir)
    log = lambda m: print(m, flush=True)  # noqa: E731
    provider = make_provider(cfg)
    solve_fn: SolveFn = lambda task, lg: solve(cfg, provider, task, log=lg)  # noqa: E731
    result = RunResult()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False, channel=cfg.run.get("channel") or None)
            ctx = browser.new_context(user_agent=cfg.run.get("user_agent") or DEFAULT_UA)
            ctx.add_cookies(to_playwright_cookies(load_cookies(cfg.browser, domain, cfg.cookie_file)))
            page = ctx.new_page()
            page.goto(start)
            page.wait_for_selector("#taskhtml, #test-preview, .wg-testworks", timeout=30_000)
            if page.locator(".wg-testworks").count():
                if not choose_work(page, cfg):
                    return []
                page.wait_for_selector("#taskhtml, #test-preview", timeout=30_000)
            if page.locator("#test-preview").count():
                if not handle_preview(page, parse_preview(page.content()), True, start_delay):
                    return []
                page.wait_for_selector("#taskhtml", timeout=30_000)
            result = execute(page, cfg.base_url, solve_fn, table, log, resume=resume, finish_anyway=finish_anyway,
                             screenshot_dir=cfg.data_dir / "runs")
            _report(result, log)
            if not result.completed:
                print("Окно остаётся открытым — доделайте вручную. Закройте его для выхода.")
                while not page.is_closed():
                    time.sleep(1)
            browser.close()
    except KeyboardInterrupt:
        print("\nПрервано.")
    finally:
        provider.close()
    return result.outcomes


def _report(r: RunResult, log: Log) -> None:
    s = r.summary()
    log("\nИтоги: " + ", ".join(f"{STATUS_NAMES.get(k, k)} {v}" for k, v in s.items()))
    for o in r.pending:
        log(f"  ⚠ задание {o.position}: {o.status} — {o.note}")
    log(("\nРабота завершена. " if r.completed else "\nРабота НЕ завершена. ") + r.note)
