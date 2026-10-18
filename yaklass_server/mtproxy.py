"""MTProto-прокси для Telegram-бота (TELEGRAM_MTPROXY в .env).

Принимаются ссылки Telegram и короткая запись:
  tg://proxy?server=HOST&port=443&secret=SECRET
  https://t.me/proxy?server=HOST&port=443&secret=SECRET
  HOST:443:SECRET
Секрет — hex (32 символа), «dd» + hex (с дополнением; нужен не всем прокси) или base64url. Секреты «ee…» (fake-TLS)
Telethon не поддерживает: домен он отбрасывает, и такой прокси обычно не принимает соединение."""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

HOST_RX = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*$")


class ProxyError(ValueError):
    pass


@dataclass(frozen=True)
class MTProxy:
    host: str
    port: int
    secret: str                       # как в ссылке (hex или base64url, возможно с префиксом dd/ee)

    @property
    def padded(self) -> bool:
        """Секрет «dd…»: прокси ждёт протокол с дополнением (RandomizedIntermediate)."""
        return self.secret.lower().startswith("dd")

    @property
    def fake_tls(self) -> bool:
        return self.secret.lower().startswith("ee")

    def safe(self) -> str:
        """Для журнала: без секрета."""
        return f"{self.host}:{self.port}"


def _check_secret(secret: str) -> str:
    s = secret.strip()
    if not s:
        raise ProxyError("у MTProto-прокси не указан секрет")
    low = s.lower()
    if re.fullmatch(r"[0-9a-f]+", low):
        body = low[2:] if low[:2] in ("dd", "ee") and len(low) > 32 else low
        if len(body) < 32 or len(body) % 2:
            raise ProxyError("секрет MTProto-прокси: 32 hex-символа (допустим префикс dd)")
        return s
    try:                                       # base64url
        raw = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except ValueError:
        raise ProxyError("секрет MTProto-прокси: hex или base64url") from None
    if len(raw) < 16:
        raise ProxyError("секрет MTProto-прокси слишком короткий")
    return s


def parse_mtproxy(text: str) -> MTProxy | None:
    """'' -> None (прокси не используется). Некорректная запись — ProxyError с понятным текстом."""
    t = (text or "").strip().strip("\"'")
    if not t:
        return None
    if "://" in t:
        u = urlparse(t)
        if u.scheme == "tg" and u.netloc != "proxy" or u.scheme in ("http", "https") and not u.path.rstrip("/").endswith("/proxy"):
            raise ProxyError("TELEGRAM_MTPROXY: ссылка должна быть вида tg://proxy?server=…&port=…&secret=…")
        q = {k: v[0] for k, v in parse_qs(u.query).items() if v}
        host, port, secret = q.get("server", ""), q.get("port", ""), q.get("secret", "")
    else:
        parts = t.rsplit(":", 2)
        if len(parts) != 3:
            raise ProxyError("TELEGRAM_MTPROXY: формат host:port:secret или ссылка tg://proxy?…")
        host, port, secret = parts
    if not HOST_RX.match(host):
        raise ProxyError("TELEGRAM_MTPROXY: неверный адрес прокси")
    if not port.isdigit() or not 0 < int(port) < 65536:
        raise ProxyError("TELEGRAM_MTPROXY: неверный порт прокси")
    return MTProxy(host, int(port), _check_secret(secret))
