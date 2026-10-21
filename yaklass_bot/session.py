"""HTTP-сессия ЯКласса на куках из браузера пользователя."""
from __future__ import annotations

import http.cookiejar
import shutil
import sqlite3
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests

from .config import Config
from .useragent import build_user_agent

DEFAULT_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


class AuthError(RuntimeError):
    pass


FIREFOX_FAMILY = {"firefox": "Firefox", "librewolf": "LibreWolf"}


def snapshot_firefox_profile(cookie_file: str | Path, dest: Path) -> Path:
    """Снимок профиля Firefox для чтения кук. Firefox пишет свежие куки в журнал cookies.sqlite-wal, а простое
    копирование cookies.sqlite их теряет (браузер открыт — недавний вход «не виден»). Поэтому копируем базу вместе с
    журналом и вливаем его (checkpoint). Сессионные куки (без срока жизни) лежат только в файлах сессии:
    recovery.jsonlz4 (браузер открыт) или previous.jsonlz4 (после закрытия) — берём, что есть. -> путь к снимку базы."""
    src = Path(cookie_file)
    prof = src.parent
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / "cookies.sqlite"
    shutil.copyfile(src, out)
    for suffix in ("-wal", "-shm"):
        side = prof / (src.name + suffix)
        if side.is_file():
            try:
                shutil.copyfile(side, dest / ("cookies.sqlite" + suffix))
            except OSError:
                pass
    try:
        con = sqlite3.connect(out)
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            con.close()
    except sqlite3.DatabaseError:
        pass                                          # не получилось влить журнал — останется то, что в основном файле
    backups = dest / "sessionstore-backups"
    backups.mkdir(exist_ok=True)
    sb = prof / "sessionstore-backups"
    for name in ("recovery.jsonlz4", "previous.jsonlz4"):
        if (sb / name).is_file():
            try:
                shutil.copyfile(sb / name, backups / "recovery.jsonlz4")    # под этим именем читает browser_cookie3
                break
            except OSError:
                continue
    if (prof / "sessionstore.js").is_file():
        try:
            shutil.copyfile(prof / "sessionstore.js", dest / "sessionstore.js")
        except OSError:
            pass
    return out


def load_cookies(browser: str, domain: str, cookie_file: str = "") -> http.cookiejar.CookieJar:
    """Читает куки домена из установленного браузера (chrome/firefox/edge/...)."""
    import browser_cookie3 as bc

    name = browser.lower()
    loader = getattr(bc, name, None)
    if loader is None or name in ("load", "all_browsers"):
        raise ValueError(f"Неизвестный браузер: {browser}")
    kwargs = {"domain_name": domain}
    if cookie_file:
        kwargs["cookie_file"] = cookie_file
    tmp: Path | None = None
    try:
        if name in FIREFOX_FAMILY:
            try:
                src = cookie_file or getattr(bc, FIREFOX_FAMILY[name])().cookie_file      # профиль по умолчанию
                tmp = Path(tempfile.mkdtemp(prefix="yk-ff-"))
                kwargs["cookie_file"] = str(snapshot_firefox_profile(src, tmp))
            except OSError:
                kwargs.pop("cookie_file", None)
                if cookie_file:
                    kwargs["cookie_file"] = cookie_file          # снимок не вышел — читаем как раньше
        return loader(**kwargs)
    except Exception as e:  # браузер не найден, БД заблокирована, шифрование и т.п.
        raise AuthError(
            f"Не удалось прочитать куки из {browser}: {e}\n"
            "Закройте браузер и повторите (БД кук может быть заблокирована) "
            "или укажите cookie_file в config.toml."
        ) from e
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def make_session(cfg: Config) -> requests.Session:
    host = urlparse(cfg.base_url).hostname or "yaklass.ru"
    domain = ".".join(host.split(".")[-2:])
    s = requests.Session()
    s.cookies.update(load_cookies(cfg.browser, domain, cfg.cookie_file))
    s.headers["User-Agent"] = cfg.run.get("user_agent") or build_user_agent(cfg.browser) or DEFAULT_UA
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
