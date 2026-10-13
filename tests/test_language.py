"""Задания по английскому: определение языка, подсказки в запросе, без веб-поиска."""
from yaklass_bot.config import Config
from yaklass_bot.lang import is_english, latin_share
from yaklass_bot.models import Field, Option, Task
from yaklass_bot.pipeline import solve
from yaklass_bot.solver.base import Provider, build_prompt, parse_answer


def task(text, fields=None):
    return Task(position=1, title="t", points=1, text=text, fields=fields or [Field("f|ta", "text")], images=[])


EN = task("Write in the correct form of the superlative adjective.\n cold | colder | the [[f|ta]] |")
RU = task("Найдите значение выражения: 2 + 3 = [[f|ta]]")
MIXED_RU_INSTR = task("Вставьте слово.\n She enjoys [[f|ta]] to music in the evening, and he is fond of reading books.")


def test_language_detection():
    assert is_english(EN) and not is_english(RU)
    assert is_english(MIXED_RU_INSTR) is False or latin_share(MIXED_RU_INSTR.text) > 0.4     # смесь не ломает разбор
    assert not is_english(task("[[f|ta]]"))                                                  # нечего определять


def test_english_prompt_has_grammar_rules_and_reasoning_not_math_hint():
    p = build_prompt(EN)
    assert "по английскому языку" in p and '"reasoning"' in p
    assert "только число/слово" not in p and "дробь вводится" not in p
    m = build_prompt(RU)
    assert "по английскому" not in m and "reasoning" not in m and "только число/слово" in m


def test_reasoning_key_is_ignored_by_parser():
    t = task("Write the form: [[f|ta]] (go) home. She is a student of English at school and likes it.")
    a = parse_answer(t, '{"reasoning": "present simple, 3rd person", "answers": {"f|ta": "goes"}, "confidence": 0.9}')
    assert a.values == {"f|ta": "goes"} and a.confidence == 0.9


class Rec(Provider):
    def __init__(self):
        self.prompts = []

    def ask(self, prompt, images=()):
        self.prompts.append(prompt)
        return '{"answers": {"f|ta": "coldest"}, "confidence": 0.9}'


def test_english_tasks_do_not_trigger_web_search(monkeypatch):
    import yaklass_bot.pipeline as pl

    def boom(*a, **k):
        raise AssertionError("поиск не должен вызываться для английского")
    monkeypatch.setattr(pl, "gather_context", boom)
    cfg = Config()
    cfg.searxng_url, cfg.search_mode = "http://searx.invalid", "always"
    prov = Rec()
    ans = solve(cfg, prov, EN, log=lambda m: None)
    assert ans.values == {"f|ta": "coldest"} and len(prov.prompts) == 1
