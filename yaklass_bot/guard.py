"""Распознавание страниц, на которых работать нельзя: блокировка IP, проверка «вы не робот»."""
from __future__ import annotations

import re


class SiteBlocked(RuntimeError):
    """Сайт заблокировал доступ (IP помечен как подозрительный). Продолжать опросы нельзя: станет только хуже."""


class SiteChallenge(RuntimeError):
    """Сайт показывает проверку «вы не робот» / JS-заглушку защиты вместо страницы."""


BLOCKED_MSG = ("ЯКласс отклонил запрос приложения (страница «403: Доступ запрещён, подозрительная активность»). "
               "Это не обязательно блокировка вашего IP: сайт мог не принять именно автоматический (скрытый) браузер. "
               "Автоматические проверки приостановлены. Включите «Показывать окно браузера» в настройках и попробуйте "
               "вручную; если отказ повторяется и в видимом окне — подождите и напишите в поддержку ЯКласса "
               "(info@yaklass.ru), указав дату, IP и id со страницы ошибки.")
CHALLENGE_MSG = ("ЯКласс показал проверку «вы не робот». Откройте сайт в обычном браузере на этом компьютере, пройдите "
                 "проверку и повторите; скрытый режим браузера сайт может не принимать.")


def is_blocked(html: str, title: str = "") -> bool:
    """Страница «403 Доступ запрещён … подозрительная активность»."""
    t = (title or "").lower()
    h = html.lower()
    if "403 error" in t or "403 forbidden" in t:
        return True
    return "доступ запрещ" in h and ("подозрительн" in h or "403" in h)


def is_challenge(html: str) -> bool:
    """Заглушка защиты от ботов (JS-проверка/капча) вместо настоящей страницы."""
    h = html.lower()
    return ("servicepipe" in h or "js-challenge-loader" in h or "id_captcha_frame_div" in h) and len(html) < 20_000


def page_title(html: str) -> str:
    m = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    return m.group(1).strip() if m else ""


def check_page(html: str) -> None:
    """Бросает SiteBlocked / SiteChallenge, если на странице блокировка или проверка."""
    if is_blocked(html, page_title(html)):
        raise SiteBlocked(BLOCKED_MSG)
    if is_challenge(html):
        raise SiteChallenge(CHALLENGE_MSG)
