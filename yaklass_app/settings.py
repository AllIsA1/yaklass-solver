"""Настройки приложения. Секреты (токен агента, ключ своего API) хранятся отдельно от обычных настроек:
в системном хранилище (keyring), а если его нет — в файле с правами 600."""
from __future__ import annotations

import json
import os
import platform
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

from yaklass_bot.pace import Pace

APP = "yaklass-solver"
SERVICE = "yaklass-solver"


def default_channel() -> str:
    """Какой браузер использовать по умолчанию, чтобы ничего не скачивать: на Windows всегда есть Edge,
    иначе установленный Chrome, иначе встроенный Chromium Playwright (его можно скачать кнопкой в настройках)."""
    import shutil
    import sys
    if sys.platform == "win32":
        return "msedge"
    if sys.platform == "darwin":
        return "chrome" if Path("/Applications/Google Chrome.app").exists() else ""
    return "chrome" if any(shutil.which(n) for n in ("google-chrome", "google-chrome-stable", "chrome")) else ""


@dataclass
class AppSettings:
    server_url: str = ""                      # https://домен сервера (без пути)
    device_name: str = field(default_factory=lambda: platform.node()[:40] or "pc")
    # решатель: server — общий API сервера (с лимитами), own — ваш API, вызывается прямо с этого ПК
    solver_mode: str = "server"
    own_api: dict = field(default_factory=lambda: {
        "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini", "vision": True, "json_mode": False,
        "temperature": 0.0})
    searxng_url: str = ""
    search_mode: str = "always"               # always | model | off (только для own)
    pace: dict = field(default_factory=lambda: Pace().to_dict())
    # браузер: profile — собственный профиль приложения (один раз войти в ЯКласс), cookies — куки вашего браузера
    browser_mode: str = "profile"
    cookie_browser: str = "chrome"
    channel: str = field(default_factory=default_channel)   # "" = Chromium Playwright, chrome / msedge = установленный
    browser_path: str = ""                    # свой исполняемый файл Chrome/Chromium/Edge (необязательно)
    show_browser: bool = True
    remote_start_allowed: bool = True         # разрешить запуск работ командой из Telegram
    auto_poll: bool = False                   # сам проверять новые работы (открывает окно браузера на несколько секунд)
    poll_minutes: int = 60
    theme: str = "dark"                       # dark | light
    check_updates: bool = True                # узнавать о новых версиях (только уведомление, ничего не скачивается)
    update_repo: str = "AllIsA1/yaklass-solver"
    base_url: str = "https://www.yaklass.ru"

    def pace_obj(self) -> Pace:
        return Pace.from_dict(self.pace)

    def validate(self) -> list[str]:
        """Список проблем (пустой = всё в порядке)."""
        errs = []
        if self.solver_mode not in ("server", "own"):
            errs.append("Неизвестный режим решателя")
        if self.server_url and not self.server_url.startswith(("https://", "http://")):
            errs.append("Адрес сервера должен начинаться с https://")
        if self.solver_mode == "own" and not self.own_api.get("base_url"):
            errs.append("Укажите адрес своего API")
        if not 5 <= self.poll_minutes <= 1440:
            errs.append("Период проверки — от 5 до 1440 минут (частые автоматические заходы на сайт могут привести к блокировке IP)")
        return errs


def config_dir() -> Path:
    return Path(os.environ.get("YAKLASS_CONFIG_DIR") or user_config_dir(APP))


def data_dir() -> Path:
    return Path(os.environ.get("YAKLASS_DATA_DIR") or user_data_dir(APP))


class SecretStore:
    """keyring, если он работает; иначе файл secrets.json с правами 600."""

    def __init__(self, directory: Path, use_keyring: bool = True):
        self.file = directory / "secrets.json"
        self.use_keyring = use_keyring and self._keyring_ok()

    @staticmethod
    def _keyring_ok() -> bool:
        try:
            import keyring
            from keyring.backends import fail
            return not isinstance(keyring.get_keyring(), fail.Keyring)
        except Exception:  # noqa: BLE001
            return False

    def _read_file(self) -> dict:
        try:
            return json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def get(self, name: str) -> str:
        """Сначала связка ключей, но пустой ответ — не окончательный: значение могло быть записано в файл
        (когда связка ключей отказала при сохранении). Раньше файл не читался, и привязка «пропадала» после перезапуска."""
        if self.use_keyring:
            try:
                import keyring
                v = keyring.get_password(SERVICE, name)
                if v:
                    return v
            except Exception:  # noqa: BLE001
                self.use_keyring = False
        return self._read_file().get(name, "")

    def set(self, name: str, value: str) -> None:
        """Пишем в связку ключей и проверяем чтением (некоторые бэкенды молча теряют запись); не удалось —
        в файл с правами 600. Удалось — убираем копию из файла, чтобы не было двух разных значений."""
        in_keyring = False
        if self.use_keyring:
            try:
                import keyring
                if value:
                    keyring.set_password(SERVICE, name, value)
                    in_keyring = keyring.get_password(SERVICE, name) == value
                else:
                    try:
                        keyring.delete_password(SERVICE, name)
                    except Exception:  # noqa: BLE001
                        pass
                    in_keyring = True
            except Exception:  # noqa: BLE001
                self.use_keyring = False
        data = self._read_file()
        changed = False
        if value and not in_keyring:
            data[name] = value
            changed = True
        elif name in data:
            del data[name]
            changed = True
        if changed:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            self.file.write_text(json.dumps(data), encoding="utf-8")
            try:
                self.file.chmod(0o600)
            except OSError:
                pass


class Store:
    """Загрузка/сохранение настроек и секретов."""

    def __init__(self, directory: Path | None = None, use_keyring: bool = True):
        self.dir = directory or config_dir()
        self.path = self.dir / "settings.json"
        self.secrets = SecretStore(self.dir, use_keyring)

    def load(self) -> AppSettings:
        s = AppSettings()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return s
        if not isinstance(raw, dict):
            return s
        known = {f.name for f in fields(AppSettings)}
        for k, v in raw.items():
            if k not in known:
                continue
            default = getattr(s, k)
            # значение неподходящего типа (файл правили руками / повредили) — оставляем по умолчанию
            if isinstance(v, type(default)) and not (isinstance(default, bool) is False and isinstance(v, bool)):
                setattr(s, k, v)
            elif isinstance(default, dict) and isinstance(v, dict):
                setattr(s, k, v)
        return s

    def save(self, s: AppSettings) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(s), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    # секреты
    @property
    def token(self) -> str:
        return self.secrets.get("agent-token")

    @token.setter
    def token(self, v: str) -> None:
        self.secrets.set("agent-token", v)

    @property
    def own_api_key(self) -> str:
        return self.secrets.get("own-api-key")

    @own_api_key.setter
    def own_api_key(self, v: str) -> None:
        self.secrets.set("own-api-key", v)
