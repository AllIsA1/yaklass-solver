"""Код подключения: бот выдаёт ОДНУ строку — адрес сервера и одноразовый код вместе. Приложение разбирает её
сама; адрес пользователю не показывается и вводить его не нужно.

Формат: ``yk1.<адрес в base64url>.<КОД-КОД>``, например ``yk1.aHR0cHM6Ly9zb2x2ZXIuZXhhbXBsZS5jb20.ABCD-EFGH``.
Для обратной совместимости принимается и просто код (``ABCD-EFGH``): тогда используется уже сохранённый адрес."""
from __future__ import annotations

import base64
import re
from urllib.parse import urlparse

PREFIX = "yk1"
_CODE_RX = re.compile(r"^[A-Za-z0-9]{4}-?[A-Za-z0-9]{4}$")
_LOCAL = ("localhost", "127.0.0.1", "::1")


class ConnectionCodeError(ValueError):
    pass


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii").rstrip("=")


def _unb64(s: str) -> str:
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise ConnectionCodeError("код подключения повреждён — скопируйте его из бота целиком") from None


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
    return f"{PREFIX}.{_b64(normalize_server_url(server_url))}.{code}"


def parse_connection_code(text: str) -> tuple[str | None, str]:
    """-> (адрес сервера или None, код привязки в виде XXXX-XXXX). Для строки без адреса адрес — None."""
    t = (text or "").strip().strip("`'\"«»")
    t = "".join(t.split())                                        # переносы/пробелы при копировании
    if t.lower().startswith(PREFIX + "."):
        parts = t.split(".", 2)
        if len(parts) != 3:
            raise ConnectionCodeError("код подключения неполный — скопируйте его из бота целиком")
        url, code = normalize_server_url(_unb64(parts[1])), parts[2]
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
