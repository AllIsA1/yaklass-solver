import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from yaklass_bot.parser import parse_exercise
from yaklass_bot.solver.base import Provider, SolveError, build_prompt, solve_task
from yaklass_bot.solver.search import SearchError, search

S = Path(__file__).parent.parent / "samples"


def task():
    return parse_exercise(next(S.glob("Верно ли это*.html")).read_text(encoding="utf-8"))


class Seq(Provider):
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def ask(self, prompt, images=()):
        self.prompts.append(prompt)
        return self.replies.pop(0)


T = task()
NAME = T.fields[0].name
ANSWER = '{"answers": {"%s": 2}, "confidence": 0.8}' % NAME


def test_model_requests_search_then_answers():
    seen = []
    p = Seq('{"search": "конституция права человека"}', ANSWER)
    a = solve_task(p, T, search=lambda q: seen.append(q) or "- Статья 2: права и свободы человека", on_search=lambda q, e: None)
    assert seen == ["конституция права человека"] and a.confidence == 0.8
    assert "Результаты поиска по «конституция права человека»" in p.prompts[1]
    assert "Статья 2" in p.prompts[1] and "Статья 2" not in p.prompts[0]


def test_search_rules_only_shown_when_search_enabled():
    assert '"search"' in build_prompt(T, max_searches=3)
    assert '"search"' not in build_prompt(T)


def test_search_limit_does_not_loop_forever():
    p = Seq(*['{"search": "ещё"}'] * 10)
    with pytest.raises(SolveError):
        solve_task(p, T, search=lambda q: "x", max_searches=2)
    assert len(p.prompts) <= 2 + 2 + 1      # 2 поиска + повторы после лимита, а не бесконечно


def test_search_failure_is_reported_and_model_still_answers():
    events = []

    def broken(q):
        raise SearchError("SearXNG недоступен")

    p = Seq('{"search": "x"}', ANSWER)
    a = solve_task(p, T, search=broken, on_search=lambda q, e: events.append((q, e)))
    assert events == [("x", "SearXNG недоступен")] and a.values[NAME]
    assert "поиск недоступен" in p.prompts[1]


def test_search_request_ignored_when_search_disabled():
    p = Seq('{"search": "x"}', ANSWER)       # без search-функции это просто некорректный ответ -> повтор
    assert solve_task(p, T).confidence == 0.8 and len(p.prompts) == 2


# ---------- настоящий HTTP-сервер вместо SearXNG ----------

def _server(status=200, body=None):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body if body is not None else {}).encode())
        def log_message(self, *a): pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def test_searx_formats_results():
    srv, url = _server(body={"results": [{"title": "Конституция РФ", "url": "http://x", "content": "Основной закон"}]})
    try:
        assert search(url, "q") == "- Конституция РФ (http://x): Основной закон"
    finally:
        srv.shutdown()


def test_searx_403_hint_and_empty_results():
    srv, url = _server(status=403)
    try:
        with pytest.raises(SearchError, match="json"):
            search(url, "q")
    finally:
        srv.shutdown()
    srv, url = _server(body={"results": []})
    try:
        assert search(url, "q") == "(ничего не найдено)"
    finally:
        srv.shutdown()
    with pytest.raises(SearchError, match="недоступен"):
        search("http://127.0.0.1:1", "q")
