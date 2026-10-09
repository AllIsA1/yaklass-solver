"""Решение задания с учётом режима поиска из config.toml."""
from __future__ import annotations

from collections.abc import Callable

from .config import Config
from .models import Task
from .solver.base import Answer, EmptyReply, Provider, solve_task
from .solver.research import gather_context
from .solver.search import search as searx


def solve(cfg: Config, provider: Provider, task: Task, log: Callable[[str], None] = print,
          **solve_kwargs) -> Answer:
    """always — обязательный поиск перед ответом (по умолчанию, если задан SearXNG);
    model — модель сама просит поиск; off — без поиска."""
    url = cfg.searxng_url
    mode = cfg.search_mode if url else "off"
    if mode == "always":
        r = gather_context(provider, task, url, n_results=cfg.search_results, fetch_pages=cfg.search_fetch_pages,
                           max_chars=cfg.search_context_chars, allow_skip=cfg.search_skip_computational, log=log)
        if not r.context and not r.skipped:
            log("  ⚠ поиск ничего не дал — ответ только по знаниям модели")
        return _solve_degrading(provider, task, r.context, log, **solve_kwargs)
    if mode == "model":
        return solve_task(provider, task, search=lambda q: searx(url, q, cfg.search_results),
                          on_search=lambda q, e: log(f"  поиск: «{q}»" + (f"  ⚠ {e}" if e else "")), **solve_kwargs)
    return solve_task(provider, task, **solve_kwargs)


def _solve_degrading(provider: Provider, task: Task, context: str, log, **solve_kwargs) -> Answer:
    """Пустой ответ модели (не влез контекст / всё ушло в рассуждения) — повторяем с меньшим контекстом,
    в крайнем случае без него."""
    tries = [context]
    if len(context) > 1500:
        tries.append(context[:1500])
    if context:
        tries.append("")
    for i, ctx in enumerate(tries):
        try:
            return solve_task(provider, task, context=ctx, **solve_kwargs)
        except EmptyReply as e:
            if i == len(tries) - 1:
                raise
            log(f"  ⚠ {e}; повторяю с {'меньшим контекстом' if tries[i + 1] else 'ответом без материалов'}")
    raise AssertionError("unreachable")
