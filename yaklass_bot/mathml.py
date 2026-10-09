"""Линеаризация MathML в читаемую строку для LLM, напр. (AD)/(BD) = (3)/(2)."""
from __future__ import annotations

from bs4 import NavigableString, Tag


def _kids(el: Tag) -> list[Tag]:
    return [c for c in el.children if isinstance(c, Tag)]


def to_text(el: Tag | NavigableString) -> str:
    if isinstance(el, NavigableString):
        return str(el).strip()
    name = el.name.split(":")[-1]
    if "ykl-input" in (el.get("class") or []):
        return f"[[{el.get('data-name', '')}]]"
    k = _kids(el)
    if name == "mfrac" and len(k) == 2:
        return f"({to_text(k[0])})/({to_text(k[1])})"
    if name == "msub" and len(k) == 2:
        return f"{to_text(k[0])}_{{{to_text(k[1])}}}"
    if name == "msup" and len(k) == 2:
        return f"{to_text(k[0])}^{{{to_text(k[1])}}}"
    if name == "msubsup" and len(k) == 3:
        return f"{to_text(k[0])}_{{{to_text(k[1])}}}^{{{to_text(k[2])}}}"
    if name == "msqrt":
        return f"sqrt({''.join(to_text(c) for c in k)})"
    if name == "mroot" and len(k) == 2:
        return f"root({to_text(k[1])}, {to_text(k[0])})"
    if name == "mfenced":
        o, c = el.get("open", "("), el.get("close", ")")
        return o + ",".join(to_text(x) for x in k) + c
    if name == "mo":
        return f" {el.get_text(strip=True)} "
    return "".join(to_text(c) for c in el.children)
