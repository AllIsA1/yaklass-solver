"""Код подключения: бот выдаёт ОДНУ строку «домен:код» — адрес сервера и одноразовый код вместе, например
``solver.example.com:ABCD-EFGH``. Приложение разбирает её само; адрес сохраняется в настройках, но в интерфейсе
не показывается и вводить его отдельно не нужно.

Принимаются также: ``https://solver.example.com:ABCD-EFGH``, прежний формат ``yk1.<адрес в base64>.<код>`` и просто
код ``ABCD-EFGH`` (тогда используется уже сохранённый адрес). Без шифрования (http) допустим только локальный адрес."""
from __future__ import annotations

import base64
import re
from urllib.parse import urlparse

PREFIX = "yk1"                                     # прежний формат (до «домен:код»)
_CODE = r"[A-Za-z0-9]{4}-?[A-Za-z0-9]{4}"
_CODE_RX = re.compile(rf"^{_CODE}$")
_DOMAIN_CODE_RX = re.compile(rf"^(?:(?P<scheme>https?)://)?(?P<host>[^\s/:@?#]+(?::\d{{1,5}})?):(?P<code>{_CODE})$")
_HOST_RX = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*$")
_LOCAL = ("localhost", "127.0.0.1", "::1")


class ConnectionCodeError(ValueError):
    pass


def _unb64(s: str) -> str:
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise ConnectionCodeError("код подключения повреждён — скопируйте его из бота целиком") from None


def _server_url(host: str, scheme: str | None = None) -> str:
    """host[:port] -> https://host[:port]; http — только для локального адреса."""
    name = host.rsplit(":", 1)[0] if re.search(r":\d+$", host) else host
    if not _HOST_RX.match(name.lower()):
        raise ConnectionCodeError("в коде подключения неверный адрес сервера")
    local = name.lower() in _LOCAL
    if scheme == "http" and not local:
        raise ConnectionCodeError("сервер должен работать по https")
    return f"{'http' if local else 'https'}://{host.lower()}"


def normalize_server_url(url: str) -> str:
    """Адрес сервера -> https://host[:port] без пути. Без шифрования (http) допускается только локальный адрес."""
    u = urlparse(url.strip())
    host = (u.hostname or "").lower()
    if u.scheme not in ("https", "http") or not host:
        raise ConnectionCodeError("в коде подключения неверный адрес сервера")
    if u.scheme == "http" and host not in _LOCAL:
        raise ConnectionCodeError("сервер должен работать по https")
    return f"{u.scheme}://{u.netloc.lower()}"


def make_connection_code(server_url: str, code: str) -> str:
    """-> «домен:код» (для локального адреса с портом — «127.0.0.1:8080:код»)."""
    u = normalize_server_url(server_url)
    return f"{urlparse(u).netloc}:{code}"


def parse_connection_code(text: str) -> tuple[str | None, str]:
    """-> (адрес сервера или None, код привязки в виде XXXX-XXXX). Для строки без адреса адрес — None."""
    t = "".join((text or "").strip().strip("`'\"«»").split())          # пробелы/переносы при копировании
    if t.lower().startswith(PREFIX + "."):                              # прежний формат
        parts = t.split(".", 2)
        if len(parts) != 3:
            raise ConnectionCodeError("код подключения неполный — скопируйте его из бота целиком")
        url, code = normalize_server_url(_unb64(parts[1])), parts[2]
    elif (m := _DOMAIN_CODE_RX.match(t)):
        url, code = _server_url(m.group("host"), (m.group("scheme") or "").lower() or None), m.group("code")
    else:
        url, code = None, t
    if not _CODE_RX.match(code):
        raise ConnectionCodeError("неверный код подключения — отправьте боту /link и скопируйте новый")
    c = code.replace("-", "").upper()
    return url, f"{c[:4]}-{c[4:]}"


def hide_host(text: str, server_url: str) -> str:
    """Убирает адрес сервера из текста ошибки: пользователь его видеть не должен."""
    if not server_url:
        return text
    host = urlparse(server_url).hostname or ""
    out = text
    for needle in filter(None, (server_url.rstrip("/"), urlparse(server_url).netloc, host)):
        out = out.replace(needle, "сервер")
    return out
