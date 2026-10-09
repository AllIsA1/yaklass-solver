import re
from pathlib import Path

from yaklass_bot.parser import parse_exercise, parse_preview, parse_works_list

S = Path(__file__).parent.parent / "samples"


def read(prefix: str) -> str:
    """Файл по началу имени; если есть точное имя (с .html) — берём его (чтобы «X» не цеплял «X (2)»)."""
    exact = S / (prefix + ".html")
    if exact.exists():
        return exact.read_text(encoding="utf-8")
    return next(S.glob(prefix + "*.html")).read_text(encoding="utf-8")


def test_works_list():
    works = parse_works_list(read("Проверочные работы"))
    assert len(works) == 1
    w = works[0]
    assert w.work_id == re.search(r"/TestWorkRun/Preview/(\d+)", read("Проверочные")).group(1) and w.subject == "Геометрия"


def test_preview():
    i = parse_preview(read("Определение"))
    assert (i.time_limit_min, i.tasks_count, i.max_points, i.attempts_left) == (45, 9, 27, 1)


def test_dropdowns():
    t = parse_exercise(read("1. Взаимное"))
    assert t.position == 1 and t.points == 2
    assert len(t.fields) == 5 and all(f.kind == "dropdown" for f in t.fields)
    assert "[[e6r1|dd]]" in t.text and "AA_{1}" in t.text


def test_formula_boxes():
    t = parse_exercise(read("3. Параллельность"))
    assert [f.name for f in t.fields] == ["e9r4|tb", "e17r5|tb"]
    assert "[[e9r4|tb]])/([[e17r5|tb]]" in t.text.replace(" ", "")


# ---------- тренировочные задания и теория (окружающий мир 4 кл., информатика 10 кл.) ----------

def by_name(prefix: str):
    return parse_exercise(read(prefix))


def test_theory_pages_detected():
    from yaklass_bot.parser import page_kind
    for p in ("Архитектура", "Основные устройства"):
        assert page_kind(read(p)) == "theory"
        t = by_name(p)
        assert t.is_theory and not t.supported and not t.fields and t.next_url


def test_non_task_pages_are_other():
    from yaklass_bot.parser import page_kind
    assert page_kind(read("Проверочные")) == "other"
    assert page_kind(read("Определение")) == "other"
    assert page_kind(read("1. Взаимное")) == "task"


def test_radio_options_have_text():
    f = by_name("Наша страна и её").fields[0]
    assert f.kind == "radio" and [o.text for o in f.options][-1] == "Российская Федерация"


def test_checkbox_group_is_one_field():
    t = by_name("Внутренние")
    assert len(t.fields) == 1 and t.fields[0].kind == "checkbox" and len(t.fields[0].options) == 6
    assert t.fields[0].options[0].value.startswith("e13r1|")   # value = name инпута


def test_text_fields():
    for p in ("Впиши пропущенное", "Впиши пропущенную"):
        t = by_name(p)
        assert [f.kind for f in t.fields] == ["text"] and t.fields[0].name == "e13r1|ta"


def test_ordering_dnd():
    t = by_name("Наша страна и права")
    assert [f.kind for f in t.fields] == ["dnd"] * 3 and len(t.fields[0].options) == 3
    assert "1. [[e23r1|dnd]]" in t.text.replace("  ", " ")


def test_matching_table_text_is_readable():
    t = by_name("Соотнеси")
    assert len(t.fields) == 3
    assert "Области | [[e6r1|dnd]] |" in t.text and "Края | [[e18r2|dnd]] |" in t.text


def test_image_choice_has_image_options_and_no_stray_images():
    t = by_name("Карта")
    f = t.fields[0]
    assert f.kind == "dnd" and all(o.image.startswith("https://") for o in f.options)
    assert t.images == [] and len(t.image_urls) == 3


def test_training_task_title_and_position():
    t = by_name("Впиши пропущенное")
    assert t.position == 6 and t.title == "Впиши пропущенное слово" and t.points == 3


# ---------- страница старта: условия работы бывают разными ----------

def _preview_variant(drop=None, attempts=None):
    h = read("Определение")
    if drop:
        import re
        h = re.sub(r"<p>%s.*?</p>" % drop, "", h, flags=re.S)
    if attempts is not None:
        h = h.replace("У тебя осталось попыток: <span class=\"value\">1</span>",
                      f"У тебя осталось попыток: <span class=\"value\">{attempts}</span>")
    return h


def test_preview_start_button_parsed():
    i = parse_preview(read("Определение"))
    assert i.can_start and i.start_label == "Начать" and i.start_allowed
    assert i.start_action == re.search(r'action="(/TestWorkRun/Start/\d+)"', read("Определение")).group(1)


def test_preview_without_time_limit():
    i = parse_preview(_preview_variant(drop="Максимальное время выполнения работы"))
    assert i.time_limit_min is None and i.tasks_count == 9 and i.start_allowed


def test_preview_other_attempts_and_zero_attempts():
    assert parse_preview(_preview_variant(attempts=3)).attempts_left == 3
    i = parse_preview(_preview_variant(attempts=0))
    assert i.attempts_left == 0 and i.can_start and not i.start_allowed


def test_preview_without_attempts_line():
    i = parse_preview(_preview_variant(drop="У тебя осталось попыток"))
    assert i.attempts_left is None and i.start_allowed


def test_minutes_formats():
    from yaklass_bot.parser import _minutes
    assert (_minutes("0:45"), _minutes("1:30"), _minutes("0:00"), _minutes("без ограничений"),
            _minutes("20 мин"), _minutes(None)) == (45, 90, None, None, 20, None)


def test_continue_button_on_started_work():
    """Работа уже начата: кнопка «Продолжить…» без id, лимита времени нет, попыток 3."""
    i = parse_preview(read("Adjectives (на 06.10)"))
    assert i.can_start and i.is_continue and i.start_label.startswith("Продолжить")
    assert i.start_allowed and i.time_limit_min is None and i.attempts_left == 3
    assert i.start_action == re.search(r'action="(/TestWorkRun/Start/\d+)"', read("Adjectives (на 06.10)")).group(1)
    assert not parse_preview(read("Определение")).is_continue


def test_nav_items_and_current():
    from yaklass_bot.parser import parse_nav
    nav = parse_nav(read("3. Параллельность"))
    assert [n.position for n in nav] == list(range(1, 10))
    cur = [n for n in nav if n.is_current]
    assert len(cur) == 1 and cur[0].position == 3 and cur[0].href == ""
    assert "exercisePosition=2" in nav[1].href


def test_new_sample_types_english_and_russian():
    t = parse_exercise(read("2. Superlatives"))
    assert [f.kind for f in t.fields] == ["text"] * 3 and "the [[e9r1|ta]]" in t.text
    t = parse_exercise(read("Тип сложноподчинённого"))                 # смесь выпадающих списков и текстовых полей
    assert sorted({f.kind for f in t.fields}) == ["dropdown", "text"]
    t = parse_exercise(read("8. Participial"))                          # картинки в условии, выпадающие списки
    assert len(t.images) == 5 and all(f.kind == "dropdown" for f in t.fields)


# ---------- отвеченная работа: «Сохранить» вместо «Ответить», страница завершения ----------

def test_answered_task_has_save_button_and_nav_flags():
    from yaklass_bot.parser import parse_nav
    html = read("2. Superlatives (2)")
    assert 'id="correctAnswerBtn"' in html and 'id="submitAnswerBtn"' not in html
    t = parse_exercise(html)
    assert t.supported and t.position == 2 and len(t.fields) == 3


def test_overview_finish_page():
    from yaklass_bot.parser import parse_nav, parse_overview
    html = read("Adjectives (на 06.10) (2)")
    ov = parse_overview(html)
    assert len(ov.rows) == 9 and all(r.answered for r in ov.rows) and not ov.unanswered
    assert ov.can_complete and "/TestWorkRun/CompleteTest" in ov.complete_action
    assert ov.rows[1].title.startswith("2. Superlatives")
    assert all(n.is_answered for n in parse_nav(html))


def test_overview_without_answers_cannot_complete():
    from yaklass_bot.parser import parse_overview
    html = read("Adjectives (на 06.10) (2)").replace("Ответ получен", "Нет ответа")
    html = html.replace("/TestWorkRun/CompleteTest", "/x")             # форма завершения есть только при полных ответах
    ov = parse_overview(html)
    assert not ov.can_complete and len(ov.unanswered) == 9
