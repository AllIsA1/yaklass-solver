"""Токены агентов и одноразовые коды привязки."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

TOKEN_PREFIX = "yks_"
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"        # без похожих 0/O, 1/I


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """В базе хранится только хеш: утечка БД не раскрывает токены."""
    return hashlib.sha256(token.encode()).hexdigest()


def new_link_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_code(code: str) -> str:
    c = "".join(ch for ch in code.upper() if ch.isalnum())
    return f"{c[:4]}-{c[4:]}" if len(c) == 8 else ""


def new_invite() -> str:
    return secrets.token_urlsafe(9)


def safe_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


class RateLimiter:
    """Скользящее окно в памяти: не больше `limit` событий за `window_s` секунд на ключ."""

    def __init__(self, limit: int, window_s: float = 60.0, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window_s, clock
        self._hits: dict[object, deque[float]] = defaultdict(deque)

    def hit(self, key) -> float:
        """0 — можно; иначе сколько секунд подождать."""
        now = self.clock()
        q = self._hits[key]
        while q and now - q[0] >= self.window:
            q.popleft()
        if len(q) >= self.limit:
            return max(0.1, self.window - (now - q[0]))
        q.append(now)
        return 0.0
