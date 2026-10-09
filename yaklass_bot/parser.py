"""Парсеры страниц Якласса (HTML рендерится на сервере, форма — обычный POST)."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from .mathml import to_text as math_to_text
from .models import Field, NavItem, Option, Overview, OverviewRow, Task, Work, WorkInfo

BLOCK_TAGS = {"div", "p", "br", "li", "tr", "h1", "h2", "h3", "h4"}


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _num(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"\d+(?:[.,]\d+)?", text)
    return float(m.group().replace(",", ".")) if m else None


def _int(text: str | None) -> int | None:
    n = _num(text)
    return int(n) if n is not None else None


# ---------- список работ ----------

def parse_works_list(html: str) -> list[Work]:
    """Секция «Новые работы» на /TestWork."""
    soup = _soup(html)
    works: list[Work] = []
    for section in soup.select("section.wg-testworks"):
        header = section.select_one("h3")
        if not header or "Новые работы" not in header.get_text():
            continue
        for tr in section.select("tbody tr"):
            a = tr.select_one("td.testwork a[href]")
            if not a:
                continue
            href = a["href"]
            m = re.search(r"/TestWorkRun/Preview/(\d+)", href)
            if not m:
                continue
            subj = tr.select_one("td.subject")
            subject = subj.get_text(" ", strip=True).replace("Предмет", "").strip() if subj else ""
            t = tr.select_one("td.date time")
            works.append(Work(
                work_id=m.group(1),
                subject=subject,
                title=a.get_text(strip=True),
                deadline_utc_ms=int(t["data-utc-date"]) if t and t.get("data-utc-date") else None,
                preview_url=href,
            ))
    return works


def _minutes(raw: str | None) -> int | None:
    """«0:45», «1:30», «45 мин» -> минуты; всё остальное («без ограничений») -> None."""
    if not raw:
        return None
    m = re.fullmatch(r"\s*(\d+):(\d{1,2})(?::\d{1,2})?\s*", raw)
    if m:
        total = int(m.group(1)) * 60 + int(m.group(2))
        return total or None
    m = re.search(r"(\d+)\s*мин", raw)
    return int(m.group(1)) if m else None


def parse_preview(html: str) -> WorkInfo:
    """/TestWorkRun/Preview/<id> — лимит времени, число заданий, попытки, кнопка запуска."""
    soup = _soup(html)
    info: dict[str, str] = {}
    for p in soup.select("#test-preview .info p"):
        val = p.select_one(".value")
        if val:
            label = p.get_text(" ", strip=True).replace(val.get_text(" ", strip=True), "").strip(" :")
            info[label] = val.get_text(strip=True)

    def find(*keys: str) -> str | None:
        return next((v for k, v in info.items() if any(x in k.lower() for x in keys)), None)

    # кнопка бывает с id (#start-action-btn, «Начать») и без него («Продолжить прохождение тестирования»)
    btn = soup.select_one("#start-action-btn") or soup.select_one('form[action*="/TestWorkRun/Start/"] button')
    form = btn.find_parent("form") if btn else None
    title = soup.select_one("#itemtitle")
    return WorkInfo(
        title=title.get_text(strip=True) if title else "",
        time_limit_min=_minutes(find("время выполнения", "время на")),
        tasks_count=_int(find("количество заданий")),
        max_points=_int(find("максимальное количество баллов")),
        attempts_left=_int(find("попыт")),
        can_start=btn is not None and btn.get("disabled") is None,
        start_label=btn.get_text(strip=True) if btn else "",
        is_continue=bool(btn and "продолж" in btn.get_text().lower()),
        start_action=form["action"] if form is not None and form.get("action") else "",
        raw=info,
    )


# ---------- задание ----------

def _abs(src: str) -> str:
    return "https:" + src if src.startswith("//") else src


def _render(node, out: list[str], images: list[str]) -> None:
    for ch in node.children:
        if isinstance(ch, Comment):
            continue
        if isinstance(ch, NavigableString):
            out.append(str(ch).replace("\xa0", " "))
            continue
        if not isinstance(ch, Tag):
            continue
        name = ch.name.split(":")[-1]
        classes = ch.get("class") or []
        if name == "math":
            out.append(" " + re.sub(r"\s+", " ", math_to_text(ch)).strip() + " ")
        elif name == "select":
            out.append(f" [[{ch.get('name')}]] ")
        elif name == "input" and ch.get("type") in (None, "text", "number"):
            out.append(f" [[{ch.get('name')}]] ")
        elif name == "textarea":
            out.append(f" [[{ch.get('name')}]] ")
        elif name == "ul" and "gxs-answer-select" in classes:
            out.append(f" [[{_group_name(ch)}]] ")          # варианты перечисляем отдельно
        elif "gxs-dnd-field" in classes:
            inp = ch.select_one("input[name]")
            out.append(f" [[{inp['name']}]] " if inp else " ")
        elif "answer-box" in classes:
            continue                                         # варианты dnd — в Field.options
        elif name == "img":
            images.append(_abs(ch.get("src", "")))
            out.append(f" [IMG:{ch.get('alt', '')}] ")
        elif name in ("meta", "script", "style"):
            continue
        elif name in ("td", "th"):
            out.append(f" {_inline(ch, images)} |")
        else:
            if name in BLOCK_TAGS:
                out.append("\n")
            _render(ch, out, images)
            if name in BLOCK_TAGS:
                out.append("\n")


def _inline(node: Tag, images: list[str] | None = None) -> str:
    out: list[str] = []
    _render(node, out, images if images is not None else [])
    return re.sub(r"\s+", " ", "".join(out).replace("\xa0", " ")).strip()


def _group_name(ul: Tag) -> str:
    inp = ul.select_one("input[name]")
    name = inp["name"] if inp else "group"
    return name if inp and inp.get("type") == "radio" else name.split("|")[0] + "|*"


def _label_option(inp: Tag, value: str, root: Tag) -> Option:
    lab = root.find("label", attrs={"for": inp.get("id")}) if inp.get("id") else None
    holder = (lab.select_one(".select-text") or lab) if lab else None
    img = holder.find("img") if holder else None
    return Option(value=value, text=_inline(holder) if holder else "",
                  image=_abs(img["src"]) if img is not None and img.get("src") else "")


def _collect_fields(root: Tag) -> list[Field]:
    fields: dict[str, Field] = {}

    def maxpts(el: Tag) -> float | None:
        for c in el.get("class") or []:
            if c.startswith("maxpoints"):
                return _num(c[len("maxpoints"):])
        return None

    # радио / чекбоксы: группа = <ul class="gxs-answer-select">
    for ul in root.select("ul.gxs-answer-select"):
        inputs = ul.select("input[type=radio], input[type=checkbox]")
        if not inputs:
            continue
        kind = inputs[0]["type"]
        f = Field(name=_group_name(ul), kind=kind, max_points=maxpts(ul))
        for inp in inputs:
            val = inp.get("value") if kind == "radio" else inp.get("name")
            f.options.append(_label_option(inp, val or "", ul))
        fields[f.name] = f

    def in_group(el: Tag) -> bool:
        return el.find_parent("ul", class_="gxs-answer-select") is not None

    for el in root.select("select[name]"):
        fields[el["name"]] = Field(
            name=el["name"], kind="dropdown", max_points=maxpts(el),
            options=[Option(o.get("value", ""), o.get_text(strip=True).replace("\xa0", " "))
                     for o in el.select("option") if o.get("value")],
        )
    for el in root.select("input[name], textarea[name]"):
        t = el.get("type", "text") if el.name == "input" else "textarea"
        if t not in ("text", "number", "textarea") or in_group(el):
            continue
        if el.find_parent(class_="gxs-dnd-field"):
            continue
        fields[el["name"]] = Field(name=el["name"], kind="text", max_points=maxpts(el),
                                   size=_int(el.get("size")))
    # поля формул: <mi class="ykl-input" data-name=...> (input создаётся JS-ом на клиенте)
    for el in root.select(".ykl-input[data-name]"):
        n = el["data-name"]
        fields.setdefault(n, Field(
            name=n, kind="text", size=_int(el.get("data-size")),
            max_points=maxpts(el.find_parent(class_="gxst-formula-box-wrap") or el),
        ))
    # перетаскивание (порядок, соотнесение, выбор картинки): поле + общий ящик вариантов
    for box_field in root.select(".gxs-dnd-field"):
        inp = box_field.select_one("input[name]")
        if inp is None:
            continue
        box = root.find(id=box_field.get("data-boxid"))
        options: list[Option] = []
        for opt in (box.select(".gxs-dnd-option[data-id]") if box else []):
            img = opt.find("img")
            options.append(Option(
                value=opt["data-id"], text=_inline(opt),
                image=_abs(img["src"]) if img is not None and img.get("src") else ""))
        fields[inp["name"]] = Field(name=inp["name"], kind="dnd", options=options,
                                    max_points=maxpts(box_field))
    return list(fields.values())


def parse_nav(html: str) -> list[NavItem]:
    """Список заданий работы со страницы задания (ссылок на текущее нет)."""
    items = []
    for li in _soup(html).select(".test-ex-nav-v2__item"):
        link = li.select_one(".test-ex-nav-v2__link")
        pos = _int(link.get_text()) if link else None
        if pos is None:
            continue
        items.append(NavItem(position=pos, title=li.get("title", ""),
                             href=link.get("href", "") if link.name == "a" else "",
                             is_current="is-current" in (li.get("class") or []),
                             is_answered="is-answered" in (li.get("class") or [])))
    return items


def parse_overview(html: str) -> Overview:
    """«Список заданий»: статусы по каждому заданию и форма завершения работы."""
    soup = _soup(html)
    rows = []
    for row in soup.select(".test-exercise-list-v2__row"):
        name = row.select_one(".test-exercise-list-v2__name")
        status = row.select_one(".test-exercise-list-v2__status")
        title = name.get_text(" ", strip=True) if name else ""
        st = status.get_text(" ", strip=True) if status else ""
        m = re.match(r"\s*(\d+)\.", title)
        rows.append(OverviewRow(position=int(m.group(1)) if m else len(rows) + 1, title=title, status=st,
                                answered="ответ получен" in st.lower()))
    form = soup.select_one('form[action*="/TestWorkRun/CompleteTest"]')
    return Overview(rows=rows, complete_action=form["action"] if form is not None else "")


def page_kind(html: str) -> str:
    """task | theory | other — по заголовку блока («Задание» / «Теория»)."""
    soup = _soup(html)
    head = soup.select_one(".exercise-content-header .pull-left")
    if soup.select_one("#taskhtml") is None or head is None:
        return "other"
    return "theory" if "Теория" in head.get_text() else "task"


def parse_exercise(html: str) -> Task:
    soup = _soup(html)
    form = soup.select_one("form.taskForm")
    wrapper = soup.select_one("#taskhtml")
    if wrapper is None:
        raise ValueError("не найден #taskhtml — это не страница задания")

    out: list[str] = []
    images: list[str] = []
    _render(wrapper, out, images)
    text = re.sub(r"[ \t]+", " ", "".join(out))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    # LaTeX-вставки \(AB\) оставляем как есть: LLM их понимает

    pts = soup.select_one(".obj-points")
    cur = soup.select_one(".test-ex-nav-v2__item.is-current")
    timer = soup.select_one(".tst-time")
    position = 0
    if form and form.get("action"):
        q = parse_qs(urlparse(form["action"]).query)
        position = int(q.get("exercisePosition", ["0"])[0])
    if cur is not None:
        title = cur.get("title", "")
    else:  # тренировочные задания и теория: «6.» + название
        pos_el, name_el = soup.select_one("#itempos"), soup.select_one("#itemtitle")
        position = position or (_int(pos_el.get_text()) or 0 if pos_el else 0)
        title = name_el.get_text(strip=True) if name_el else ""
    nxt = soup.select_one(".nav-cell-next a[href]")

    return Task(
        position=position,
        title=title,
        points=_num(pts.get_text()) if pts else None,
        text=text,
        fields=_collect_fields(wrapper),
        images=images,
        has_audio=bool(wrapper.select("audio, [class*=audio]")),
        form_action=form["action"] if form else "",
        time_left_s=_int(timer.get("data-left-time")) if timer else None,
        is_theory=page_kind(html) == "theory",
        next_url=nxt["href"] if nxt else "",
    )
