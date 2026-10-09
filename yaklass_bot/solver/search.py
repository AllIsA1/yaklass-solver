"""Поиск через SearXNG. В инстансе должен быть включён формат json
(settings.yml -> search: formats: [html, json]), иначе сервер отвечает 403."""
from __future__ import annotations

import requests


class SearchError(RuntimeError):
    pass


def search_results(url: str, query: str, n: int = 5) -> list[dict]:
    """[{title, url, content}] или SearchError."""
    try:
        r = requests.get(f"{url}/search", params={"q": query, "format": "json", "language": "all"}, timeout=20)
    except requests.RequestException as e:
        raise SearchError(f"SearXNG недоступен: {e}") from e
    if r.status_code == 403:
        raise SearchError("SearXNG вернул 403: включите формат json (search.formats: [html, json] в settings.yml)")
    if r.status_code != 200:
        raise SearchError(f"SearXNG вернул {r.status_code}")
    try:
        items = r.json().get("results", [])[:n]
    except ValueError as e:
        raise SearchError("SearXNG ответил не JSON (включён ли формат json?)") from e
    return [{"title": i.get("title", ""), "url": i.get("url", ""), "content": (i.get("content") or "")[:400]}
            for i in items]


def search(url: str, query: str, n: int = 5) -> str:
    """Результаты текстом для вставки в промпт или SearchError."""
    items = search_results(url, query, n)
    if not items:
        return "(ничего не найдено)"
    return "\n".join(f"- {i['title']} ({i['url']}): {i['content']}" for i in items)
