"""HTTP-сессия ЯКласса на куках из браузера пользователя."""
from __future__ import annotations

import http.cookiejar
from urllib.parse import urlparse

import requests

from .config import Config

DEFAULT_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


class AuthError(RuntimeError):
    pass


def load_cookies(browser: str, domain: str, cookie_file: str = "") -> http.cookiejar.CookieJar:
    """Читает куки домена из установленного браузера (chrome/firefox/edge/...)."""
    import browser_cookie3 as bc

    loader = getattr(bc, browser.lower(), None)
    if loader is None or browser.lower() in ("load", "all_browsers"):
        raise ValueError(f"Неизвестный браузер: {browser}")
    kwargs = {"domain_name": domain}
    if cookie_file:
        kwargs["cookie_file"] = cookie_file
    try:
        return loader(**kwargs)
    except Exception as e:  # браузер не найден, БД заблокирована, шифрование и т.п.
        raise AuthError(
            f"Не удалось прочитать куки из {browser}: {e}\n"
            "Закройте браузер и повторите (БД кук может быть заблокирована) "
            "или укажите cookie_file в config.toml."
        ) from e


def make_session(cfg: Config) -> requests.Session:
    host = urlparse(cfg.base_url).hostname or "yaklass.ru"
    domain = ".".join(host.split(".")[-2:])
    s = requests.Session()
    s.cookies.update(load_cookies(cfg.browser, domain, cfg.cookie_file))
    s.headers["User-Agent"] = cfg.run.get("user_agent") or DEFAULT_UA
    s.headers["Accept-Language"] = "ru,en;q=0.8"
    return s


def fetch(s: requests.Session, cfg: Config, path: str) -> str:
    r = s.get(cfg.base_url + path, timeout=30)
    r.raise_for_status()
    if "/Account/Login" in r.url or "/login" in r.url.lower():
        raise AuthError("Сессия ЯКласса недействительна: войдите в аккаунт в браузере и повторите.")
    return r.text


def _expires(raw) -> float:
    """Срок жизни куки -> то, что принимает Playwright: -1 (сессионная) или unix-время в секундах.
    Браузеры/библиотеки отдают разное: None, 0, отрицательные числа (сессионные куки Chrome после
    пересчёта эпох), миллисекунды, микросекунды."""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return -1
    if v != v or v <= 0:                 # NaN, 0, отрицательные
        return -1
    while v > 32_503_680_000:            # дальше 3000 года — значит не секунды
        v /= 1000
    return v if v > 0 else -1


def to_playwright_cookies(jar) -> list[dict]:
    out = []
    for c in jar:
        if not c.name or c.value is None or not c.domain:
            continue
        out.append({"name": c.name, "value": c.value, "domain": c.domain, "path": c.path or "/",
                    "secure": bool(c.secure), "expires": _expires(c.expires)})
    return out
