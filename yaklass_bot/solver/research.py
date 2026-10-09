"""Обязательный поиск перед ответом: запросы -> SearXNG -> выдержки со страниц -> контекст для модели.

Не полагаемся на то, что модель сама решит искать: уверенная в себе модель этого не делает."""
from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from ..models import Task
from .base import Provider, SolveError, _extract_json
from .search import SearchError, search_results

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"
MAX_PAGE_BYTES = 1_500_000


def _clean_task_text(task: Task) -> str:
    t = re.sub(r"\[IMG:[^\]]*\]", " ", task.text)
    t = re.sub(r"\[\[[^\]]*\]\]", "…", t)                 # поля ответа -> многоточие
    t = re.sub(r"\\\(|\\\)", "", t)
    t = re.sub(r"\(\s*Выбери[^)]*\)|\(\s*Запиши[^)]*\)|\(\s*Перетащи[^)]*\)|\(\s*Перенеси[^)]*\)", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _items(task: Task) -> list[str]:
    """Подпункты задания (строки с полями ответа): «1. To make something clear … [[поле]]» -> короткий запрос."""
    items = []
    for line in task.text.splitlines():
        if "[[" not in line:
            continue
        t = re.sub(r"\[\[[^\]]*\]\]|\[IMG:[^\]]*\]|\\\(|\\\)", " ", line)
        t = re.sub(r"^\s*\d+[.)]\s*", "", t.strip())
        t = re.sub(r"\s+", " ", t).strip(" …:;|")
        if len(t) > 12:
            items.append(t[:140])
    return items[:4]


def heuristic_queries(task: Task) -> list[str]:
    """Запросы без участия модели: по одному на подпункт; если подпунктов нет — всё условие."""
    items = _items(task)
    if items:
        return items
    text = _clean_task_text(task)
    q = [text[:200]]
    opts = [o.text for f in task.fields for o in f.options if o.text][:4]
    if opts and len(text) < 160:
        q.append(f"{text[:120]} {' '.join(opts)}"[:200])
    return [x for x in q if len(x) > 8]


def make_queries(provider: Provider, task: Task, k: int | None = None, allow_skip: bool = True) -> list[str] | None:
    """Просим модель сформулировать запросы (по одному на подпункт, если их несколько).
    None — модель считает задание вычислительным, поиск не нужен. При любой неудаче — эвристика."""
    k = k or min(3, max(2, len(task.fields)))
    skip = ('Если задание — чистое вычисление или преобразование (уравнение, разложение, площадь по числам), '
            'где ответ получается расчётом, а не поиском фактов, верни {"queries": []}. ' if allow_skip else "")
    prompt = (f"Сформулируй до {k} коротких поисковых запросов (3–10 слов, на языке задания), "
              "по которым можно найти правильный ответ на школьное задание: по одному на каждый подпункт, "
              "если их несколько. Не решай задание. " + skip +
              'Верни только JSON: {"queries": ["...", "..."]}.\n\nЗадание:\n' + _clean_task_text(task))
    try:
        data = _extract_json(provider.ask(prompt))
        raw = data.get("queries")
        qs = [str(x).strip() for x in raw if str(x).strip()][:k] if isinstance(raw, list) else []
    except (SolveError, AttributeError, TypeError):
        return heuristic_queries(task)
    if not qs:
        return None if (allow_skip and isinstance(raw, list)) else heuristic_queries(task)
    return qs


# ---------- выдержки со страниц ----------

def _is_public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    return all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[а-яёa-z0-9]{4,}", text.lower())}


def page_excerpt(url: str, task_text: str, max_chars: int = 1200, allow_private: bool = False) -> str:
    """Самые релевантные заданию абзацы страницы (по пересечению слов) или ''."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return ""
    if not allow_private and not _is_public(u.hostname):
        return ""            # результаты поиска — недоверенные данные: во внутреннюю сеть не ходим
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"},
                         timeout=12, stream=True)
        ctype = r.headers.get("content-type", "")
        if r.status_code != 200 or "html" not in ctype and "text" not in ctype:
            return ""
        raw = r.raw.read(MAX_PAGE_BYTES, decode_content=True)
    except requests.RequestException:
        return ""
    soup = BeautifulSoup(raw, "lxml")
    for t in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
        t.decompose()
    want = _stems(task_text)
    scored = []
    for i, el in enumerate(soup.select("p, li, td, h1, h2, h3, blockquote")):
        txt = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
        if len(txt) < 25:
            continue
        score = len(want & _stems(txt))
        if score >= 2:
            scored.append((score, i, txt[:400]))
    best = sorted(scored, key=lambda x: -x[0])[:6]
    out, total = [], 0
    for _, _, txt in sorted(best, key=lambda x: x[1]):          # в исходном порядке
        if total + len(txt) > max_chars:
            break
        out.append(txt)
        total += len(txt)
    return "\n".join(out)


@dataclass
class Research:
    context: str = ""
    skipped: bool = False      # модель сочла задание вычислительным — поиск не выполнялся


def gather_context(provider: Provider, task: Task, searxng_url: str, *, n_results: int = 6,
                   fetch_pages: int = 3, max_chars: int = 4000, allow_skip: bool = True,
                   allow_private: bool = False,
                   log: Callable[[str], None] = lambda s: None) -> Research:
    """Контекст для итогового ответа (не длиннее max_chars: он должен помещаться в окно модели).
    Сбои поиска не фатальны: возвращает то, что удалось."""
    task_text = _clean_task_text(task)
    queries = make_queries(provider, task, allow_skip=allow_skip)
    if queries is None:
        log("  поиск пропущен: вычислительное задание — ответ получается расчётом")
        return Research(skipped=True)
    seen: dict[str, dict] = {}
    for q in queries:
        try:
            items = search_results(searxng_url, q, n_results)
        except SearchError as e:
            log(f"  поиск: «{q}»  ⚠ {e}")
            continue
        log(f"  поиск: «{q}» → {len(items)} рез.")
        for it in items:
            seen.setdefault(it["url"], it)
    if not seen:
        return Research()
    full, snippets, read = [], [], 0
    for it in seen.values():
        excerpt = ""
        if read < fetch_pages and it["url"]:
            excerpt = page_excerpt(it["url"], task_text, allow_private=allow_private)
            read += bool(excerpt)
        if excerpt:
            full.append(f"[{it['title'][:80]}] ({it['url']})\n{excerpt}")
        elif it["content"]:
            snippets.append(f"[{it['title'][:80]}] ({it['url']})\n{it['content']}")
    out, total = [], 0
    for part in full + snippets:               # выдержки со страниц важнее сниппетов
        if total + len(part) > max_chars:
            part = part[: max_chars - total]
            if len(part) < 120:
                break
        out.append(part)
        total += len(part)
        if total >= max_chars:
            break
    log(f"  прочитано страниц: {read}, в контексте источников: {len(out)}, символов: {total}")
    return Research(context="\n\n".join(out))
