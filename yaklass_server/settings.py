"""Настройки сервера из переменных окружения (см. deploy/.env.example)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path


def load_dotenv(path: str | os.PathLike | None = None) -> Path | None:
    """Читает KEY=VALUE из .env (путь: аргумент, ENV_FILE или ./.env). Уже заданные переменные окружения
    НЕ перезаписываются. Возвращает путь прочитанного файла."""
    p = Path(path or os.environ.get("ENV_FILE") or ".env")
    if not p.is_file():
        return None
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), clean_value(val))
    return p


_INLINE_COMMENT = re.compile(r"\s+#.*$")


def clean_value(v: str) -> str:
    """Значение переменной без пробелов, кавычек и комментария в конце строки.
    Нужно потому, что systemd (EnvironmentFile) НЕ вырезает «КЛЮЧ=300   # комментарий» — значение приходит целиком."""
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return _INLINE_COMMENT.sub("", v).strip()


@dataclass
class Settings:
    # сеть
    domain: str = "localhost"
    public_url_override: str = ""        # PUBLIC_URL: полный адрес для пользователей (по умолчанию https://DOMAIN)
    host: str = "127.0.0.1"             # за nginx: наружу порт не открываем
    port: int = 8080
    trusted_proxy: str = "127.0.0.1"     # чьим X-Forwarded-For верим (адрес nginx)
    # Telegram
    bot_token: str = ""
    admin_ids: set[int] = field(default_factory=set)
    registration: str = "open"           # open | invite | closed
    # хранилище
    db_path: str = "data/server.db"
    # лимиты
    daily_quota: int = 300               # запросов на решение в сутки на пользователя
    rate_per_min: int = 20
    max_concurrent_solves: int = 4
    max_body_bytes: int = 300_000
    link_code_ttl_s: int = 600
    max_agents_per_user: int = 3
    min_task_interval_s: int = 0         # рекомендация клиенту (пауза между заданиями), сообщается в /v1/me
    # модель
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""
    llm_vision: bool = True
    llm_json_mode: bool = False
    # поиск
    searxng_url: str = ""
    search_mode: str = "always"          # always | model | off
    # картинки: сервер скачивает только с этих доменов (защита от SSRF)
    image_hosts: tuple[str, ...] = ("selcdn.net", "yaklass.ru")
    log_tasks: bool = False              # писать ли тексты заданий в лог (по умолчанию нет)

    @classmethod
    def from_env(cls, env_file: str | None = None) -> "Settings":
        load_dotenv(env_file)
        env = {k: clean_value(v) for k, v in os.environ.items()}

        def g(name: str, default: str = "") -> str:
            return env.get(name) or default

        def i(name: str, default: int) -> int:
            v = env.get(name, "")
            try:
                return int(v) if v else default
            except ValueError:
                raise ValueError(f"{name}: ожидалось целое число, получено {v!r}") from None

        def b(name: str, default: bool) -> bool:
            v = env.get(name, "").lower()
            return default if not v else v in ("1", "true", "yes", "on")

        s = cls(
            domain=g("DOMAIN", "localhost"),
            public_url_override=g("PUBLIC_URL"),
            host=g("HOST", "127.0.0.1"),
            trusted_proxy=g("TRUSTED_PROXY", "127.0.0.1"),
            port=i("PORT", 8080),
            bot_token=g("TELEGRAM_BOT_TOKEN"),
            admin_ids={int(x) for x in g("ADMIN_IDS").replace(" ", "").split(",") if x},
            registration=g("REGISTRATION", "open").lower(),
            db_path=g("DB_PATH", "data/server.db"),
            daily_quota=i("DAILY_QUOTA", 300),
            rate_per_min=i("RATE_PER_MIN", 20),
            max_concurrent_solves=i("MAX_CONCURRENT_SOLVES", 4),
            min_task_interval_s=i("MIN_TASK_INTERVAL_S", 0),
            max_agents_per_user=i("MAX_AGENTS_PER_USER", 3),
            llm_base_url=g("LLM_BASE_URL", "https://api.openai.com/v1"),
            llm_model=g("LLM_MODEL", "gpt-4o-mini"),
            llm_api_key=g("LLM_API_KEY"),
            llm_vision=b("LLM_VISION", True),
            llm_json_mode=b("LLM_JSON_MODE", False),
            searxng_url=g("SEARXNG_URL").rstrip("/"),
            search_mode=g("SEARCH_MODE", "always").lower(),
            image_hosts=tuple(h.strip().lower() for h in g("IMAGE_HOSTS", "selcdn.net,yaklass.ru").split(",") if h.strip()),
            log_tasks=b("LOG_TASKS", False),
        )
        s.validate()
        return s

    @property
    def public_url(self) -> str:
        """Адрес сервера, который пользователь вводит в приложении."""
        if self.public_url_override:
            return self.public_url_override.rstrip("/")
        if self.domain in ("localhost", "127.0.0.1", "::1"):
            return f"http://{self.domain}:{self.port}"
        return f"https://{self.domain}"

    def validate(self) -> None:
        if self.public_url_override and not self.public_url_override.startswith(("http://", "https://")):
            raise ValueError("PUBLIC_URL должен начинаться с http:// или https://")
        if self.registration not in ("open", "invite", "closed"):
            raise ValueError("REGISTRATION должен быть open, invite или closed")
        if self.search_mode not in ("always", "model", "off"):
            raise ValueError("SEARCH_MODE должен быть always, model или off")
