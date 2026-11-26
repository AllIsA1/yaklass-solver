"""Решение задания с учётом режима поиска из config.toml."""
from __future__ import annotations

from collections.abc import Callable

from .config import Config
from .lang import is_english
from .models import Task
from .solver.audio import AudioConf, AudioHelper, AudioUnsupported, SttNotFound
from .solver.base import Answer, EmptyReply, Provider, solve_task
from .solver.research import gather_context
from .solver.search import search as searx


def prepare_audio(cfg: Config, provider: Provider, task: Task, log: Callable[[str], None] = print,
                  audio: AudioHelper | None = None) -> tuple[Task, list]:
    """Задание с аудио -> (задание с расшифровками, []) или (задание, записи для модели). Без аудио — как есть.
    AudioUnsupported, если слушать нечем. transcribe без /audio/transcriptions у сервиса (404/405) переходит на native,
    если модель принимает звук."""
    if not task.has_audio:
        return task, []
    helper = audio or AudioHelper(AudioConf.from_dict(cfg.audio, cfg.api))
    if helper.mode == "off":
        raise AudioUnsupported("аудирование выключено в настройках")
    # transcribe/chat — в текст; transcribe без /audio/transcriptions (404/405) — звук основной модели
    if helper.mode == "native" or (helper.stt_missing and provider.supports_audio):
        if not provider.supports_audio:
            raise AudioUnsupported("модель не принимает аудио — включите расшифровку (transcribe)")
        clips = helper.clips(task)
        log(f"  аудио: {len(clips)} запис(ь/и) приложено к запросу")
        return task, clips
    try:
        return helper.transcribed(task, log=log), []
    except SttNotFound as e:
        if not provider.supports_audio:
            raise
        log(f"  аудио: у сервиса нет расшифровки ({e}) — записи отправлены модели")
        return task, e.clips


def solve(cfg: Config, provider: Provider, task: Task, log: Callable[[str], None] = print,
          audio: AudioHelper | None = None, **solve_kwargs) -> Answer:
    """always — обязательный поиск перед ответом (по умолчанию, если задан SearXNG);
    model — модель сама просит поиск; off — без поиска.
    audio — как слушать аудирование (по умолчанию из cfg.audio)."""
    task, clips = prepare_audio(cfg, provider, task, log, audio)
    if clips:
        solve_kwargs["audio"] = clips
    url = cfg.searxng_url
    mode = cfg.search_mode if url else "off"
    if mode != "off" and (is_english(task) or task.has_audio):
        # грамматику не ищут в интернете: сниппеты чужих сайтов только сбивают с правильной формы;
        # в аудировании ответ — в самой записи
        mode = "off"
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
