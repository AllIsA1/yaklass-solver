"""Запасная модель сервера: переход при недоступности основной, «отдых» основной, ошибки запроса не переводятся."""
import logging

import pytest
import requests

from yaklass_bot.solver.api import ApiProvider
from yaklass_bot.solver.base import Provider, ProviderUnavailable, SolveError
from yaklass_server.fallback import FallbackProvider
from yaklass_server.settings import Settings
from yaklass_server.solver_service import SolverService


class Fake(Provider):
    def __init__(self, name, fail=None, vision=True):
        self.name, self.fail, self.calls, self.supports_images, self.seen_images = name, fail, 0, vision, []

    def ask(self, prompt, images=()):
        self.calls += 1
        self.seen_images.append(len(images))
        if self.fail:
            raise self.fail
        return self.name


def make(primary, fallback, cooldown=60.0):
    now = [0.0]
    return FallbackProvider(primary, fallback, cooldown, clock=lambda: now[0]), now


def test_primary_is_used_while_healthy():
    p, f = Fake("P"), Fake("F")
    fb, _ = make(p, f)
    assert fb.ask("q") == "P" and f.calls == 0 and not fb.primary_down


def test_switches_to_fallback_and_rests_primary_then_returns(caplog):
    p, f = Fake("P", ProviderUnavailable("API 503")), Fake("F")
    fb, now = make(p, f, cooldown=60)
    with caplog.at_level(logging.WARNING, logger="yaklass.server"):
        assert fb.ask("1") == "F"                                   # основная упала -> запасная в том же запросе
        assert fb.ask("2") == "F" and p.calls == 1                  # пока «отдыхает» — основную не дёргаем
    assert fb.switches == 1 and fb.fallback_calls == 2 and "перехожу" in caplog.text
    now[0] = 61
    p.fail = None
    assert fb.ask("3") == "P" and not fb.primary_down               # после паузы основная снова первая


def test_primary_is_retried_after_cooldown_even_if_still_down():
    p, f = Fake("P", ProviderUnavailable("x")), Fake("F")
    fb, now = make(p, f, cooldown=10)
    fb.ask("1")
    now[0] = 11
    assert fb.ask("2") == "F" and p.calls == 2 and fb.switches == 2


def test_request_errors_do_not_trigger_fallback():
    p, f = Fake("P", SolveError("API 400: bad request")), Fake("F")
    fb, _ = make(p, f)
    with pytest.raises(SolveError):
        fb.ask("q")
    assert f.calls == 0 and not fb.primary_down


def test_both_down_raises_and_primary_gets_last_chance():
    p, f = Fake("P", ProviderUnavailable("a")), Fake("F", ProviderUnavailable("b"))
    fb, _ = make(p, f)
    with pytest.raises(ProviderUnavailable, match="b"):
        fb.ask("1")
    p.fail = None                                                  # основная ожила, а запасная всё ещё лежит
    assert fb.ask("2") == "P"


def test_images_are_dropped_for_a_text_only_fallback():
    p, f = Fake("P", ProviderUnavailable("a")), Fake("F", vision=False)
    fb, _ = make(p, f)
    fb.ask("q", images=[object(), object()])
    assert p.seen_images == [2] and f.seen_images == [0]


def test_api_provider_maps_failures_to_unavailable(monkeypatch):
    prov = ApiProvider({"base_url": "http://x/v1", "model": "m", "api_key": "k", "api_key_env": ""})

    class R:
        def __init__(self, code): self.status_code, self.text = code, "boom"

    for code, cls in ((503, ProviderUnavailable), (429, ProviderUnavailable), (401, ProviderUnavailable),
                      (402, ProviderUnavailable), (404, ProviderUnavailable), (400, SolveError), (422, SolveError)):
        monkeypatch.setattr(requests, "post", lambda *a, code=code, **k: R(code))
        with pytest.raises(cls) as e:
            prov.ask("q")
        assert (type(e.value) is cls) or cls is SolveError and not isinstance(e.value, ProviderUnavailable)

    def boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(requests, "post", boom)
    with pytest.raises(ProviderUnavailable, match="недоступен"):
        prov.ask("q")


def test_service_builds_fallback_only_when_configured():
    base = dict(llm_api_key="k", llm_base_url="https://a.example/v1", llm_model="main", search_mode="off")
    assert isinstance(SolverService(Settings(**base)).provider, ApiProvider)
    svc = SolverService(Settings(**base, llm_fallback_model="backup", llm_fallback_base_url="https://b.example/v1",
                                 llm_fallback_vision=False))
    fb = svc.provider
    assert isinstance(fb, FallbackProvider) and fb.primary.model == "main" and fb.fallback.model == "backup"
    assert fb.fallback.url.startswith("https://b.example") and fb.fallback.key == "k"      # ключ — как у основной
    assert fb.fallback.supports_images is False and fb.supports_images is True


def test_settings_validation_and_env(monkeypatch):
    with pytest.raises(ValueError, match="LLM_FALLBACK_MODEL"):
        Settings(llm_fallback_api_key="x").validate()
    for k, v in {"LLM_FALLBACK_MODEL": "backup", "LLM_FALLBACK_VISION": "false", "LLM_FALLBACK_COOLDOWN_S": "30"}.items():
        monkeypatch.setenv(k, v)
    s = Settings.from_env("/nonexistent")
    assert s.llm_fallback_model == "backup" and s.llm_fallback_vision is False and s.llm_fallback_json_mode is None
    assert s.llm_fallback_cooldown_s == 30
