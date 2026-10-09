from __future__ import annotations

import json
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

APP = "yaklass-bot"


def _t(v) -> str:
    """Значение -> литерал TOML (строки через json.dumps: это валидная basic string)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return json.dumps(str(v), ensure_ascii=False)


def default_config_path() -> Path:
    local = Path("config.toml")
    return local if local.exists() else Path(user_config_dir(APP)) / "config.toml"


@dataclass
class Config:
    base_url: str = "https://www.yaklass.ru"
    browser: str = "chrome"
    cookie_file: str = ""
    interval_min: float = 12
    jitter_min: float = 4
    api: dict = field(default_factory=lambda: {
        "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY", "temperature": 0.0,
        "json_mode": False})
    run: dict = field(default_factory=lambda: {"channel": "", "min_confidence": 0.5, "user_agent": ""})
    searxng_url: str = ""
    search_results: int = 6
    search_mode: str = "always"      # always | model | off
    search_fetch_pages: int = 3
    search_context_chars: int = 4000
    search_skip_computational: bool = True
    data_dir: Path = field(default_factory=lambda: Path(user_data_dir(APP)))

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or default_config_path()
        cfg = cls()
        if not path.exists():
            return cfg
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        cfg.base_url = raw.get("yaklass", {}).get("base_url", cfg.base_url).rstrip("/")
        b = raw.get("browser", {})
        cfg.browser = b.get("name", cfg.browser)
        cfg.cookie_file = b.get("cookie_file", "")
        w = raw.get("watch", {})
        cfg.interval_min = w.get("interval_min", cfg.interval_min)
        cfg.jitter_min = w.get("jitter_min", cfg.jitter_min)
        llm = raw.get("llm", {})
        if llm.get("provider") == "chatgpt_browser":
            print("Внимание: режим «ChatGPT через браузер» удалён — используется API. "
                  "Запустите `yaklass-bot setup`.", file=sys.stderr)
        cfg.api.update(llm.get("api", {}))
        cfg.run.update(raw.get("run", {}))
        s = raw.get("search", {})
        cfg.searxng_url = s.get("searxng_url", "").rstrip("/")
        cfg.search_results = s.get("results", cfg.search_results)
        cfg.search_mode = s.get("mode", cfg.search_mode)
        cfg.search_fetch_pages = s.get("fetch_pages", cfg.search_fetch_pages)
        cfg.search_context_chars = s.get("context_chars", cfg.search_context_chars)
        cfg.search_skip_computational = s.get("skip_computational", cfg.search_skip_computational)
        d = raw.get("storage", {}).get("dir", "")
        if d:
            cfg.data_dir = Path(d).expanduser()
        return cfg

    def save(self, path: Path) -> None:
        d = ""
        if self.data_dir != Path(user_data_dir(APP)):
            d = str(self.data_dir)
        a, r = self.api, self.run
        lines = [
            "# Создано `yaklass-bot setup`; можно править вручную, мастер можно запускать повторно.",
            "", "[yaklass]", f"base_url = {_t(self.base_url)}",
            "", "[browser]", "# chrome | chromium | firefox | edge | brave | opera | opera_gx | vivaldi | librewolf | safari | arc",
            f"name = {_t(self.browser)}", f"cookie_file = {_t(self.cookie_file)}",
            "", "[watch]", f"interval_min = {_t(self.interval_min)}", f"jitter_min = {_t(self.jitter_min)}",
            "", "[llm.api]", f"base_url = {_t(a['base_url'])}", f"model = {_t(a['model'])}",
            "# ключ: сначала переменная окружения api_key_env, затем файл api_key_file",
            f"api_key_env = {_t(a.get('api_key_env', ''))}", f"api_key_file = {_t(a.get('api_key_file', ''))}",
            f"temperature = {_t(a.get('temperature', 0.0))}", f"vision = {_t(a.get('vision', True))}",
            f"json_mode = {_t(a.get('json_mode', False))}",
            "", "[run]", '# "" = встроенный Chromium Playwright; "chrome" / "msedge" = установленный браузер',
            f"channel = {_t(r.get('channel', ''))}", f"min_confidence = {_t(r.get('min_confidence', 0.5))}",
            "# User-Agent браузера (пусто = по умолчанию)", f"user_agent = {_t(r.get('user_agent', ''))}",
            "", "[search]", "# mode: always — поиск перед каждым ответом (рекомендуется) | model — модель решает сама | off",
            f"searxng_url = {_t(self.searxng_url)}", f"mode = {_t(self.search_mode)}",
            f"results = {_t(self.search_results)}", f"fetch_pages = {_t(self.search_fetch_pages)}",
            "# сколько символов найденного отдавать модели (не больше окна контекста модели!)",
            f"context_chars = {_t(self.search_context_chars)}",
            "# пропускать поиск, если модель считает задание чисто вычислительным (уравнения, разложение и т.п.)",
            f"skip_computational = {_t(self.search_skip_computational)}",
            "", "[storage]", f"dir = {_t(d)}", "",
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
