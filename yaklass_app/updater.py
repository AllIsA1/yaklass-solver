"""Обновление приложения из GitHub Releases: проверка, скачивание с проверкой SHA-256, замена файла, перезапуск.

Поддерживается ровно то, что собирает CI:
- Linux AppImage: новый файл атомарно подменяет старый (Linux разрешает это даже для запущенного файла);
- Windows .exe: запущенный файл нельзя перезаписать или удалить, но можно ПЕРЕИМЕНОВАТЬ — старый уходит в *.old-<время>,
  новый встаёт на его место, при следующем старте *.old-* удаляются;
- запуск из исходников/pip: самообновления нет, приложение только сообщает о новой версии.

Безопасность: скачанный файл применяется, только если его SHA-256 совпал с цифрой из ответа GitHub API (поле digest) или
из SHA256SUMS.txt релиза; несовпадение или обрыв — файл удаляется, приложение остаётся прежним. Скачивание — только с
адреса репозитория, указанного в настройках, по HTTPS. Приватный репозиторий без токена отвечает 404; для проверки можно
задать переменную окружения YAKLASS_UPDATE_TOKEN (не сохраняется и нигде не пишется).
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import requests

from . import __version__

API = "https://api.github.com"
ALLOW_INSECURE = False                    # только для тестов с локальным сервером: иначе скачиваем лишь по https
DEFAULT_REPO = "AllIsA1/yaklass-solver"
TIMEOUT = (10, 60)
CHUNK = 256 * 1024
EXE_NAME = "YaklassSolver.exe"


class UpdateError(RuntimeError):
    pass


def parse_version(v: str) -> tuple[int, ...]:
    """'v0.1.8' -> (0, 1, 8). Нечисловые хвосты (rc/beta) отбрасываются."""
    parts = re.findall(r"\d+", v.strip().lstrip("vV").split("-")[0].split("+")[0])
    return tuple(int(p) for p in parts) or (0,)


def is_newer(candidate: str, current: str = __version__) -> bool:
    return parse_version(candidate) > parse_version(current)


@dataclass
class Release:
    tag: str
    url: str                                  # страница релиза
    notes: str = ""
    assets: list[dict] = field(default_factory=list)

    @property
    def version(self) -> str:
        return self.tag.lstrip("vV")


@dataclass
class Plan:
    """Как можно обновиться на этой машине."""
    mode: str                                 # appimage | exe | none
    target: Path | None = None                # какой файл заменяем
    reason: str = ""                          # почему нельзя (для mode == none)


def detect_plan() -> Plan:
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        return Plan("exe", Path(sys.executable))
    app = os.environ.get("APPIMAGE")
    if app and Path(app).is_file():
        return Plan("appimage", Path(app))
    return Plan("none", None, "Приложение запущено не из готовой сборки (исходники или pip): обновитесь через pip install -U "
                              "или скачайте сборку со страницы релиза.")


def update_token() -> str | None:
    return os.environ.get("YAKLASS_UPDATE_TOKEN") or None


def _headers(token: str | None, accept: str = "application/vnd.github+json") -> dict:
    h = {"Accept": accept, "User-Agent": f"yaklass-solver/{__version__}"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def fetch_latest(repo: str = DEFAULT_REPO, token: str | None = None, api: str = API, session=requests) -> Release:
    """Последний опубликованный (не черновик и не пре-релиз) релиз."""
    try:
        r = session.get(f"{api}/repos/{repo}/releases/latest", headers=_headers(token), timeout=TIMEOUT)
    except requests.RequestException as e:
        raise UpdateError(f"Нет связи с GitHub: {e.__class__.__name__}") from e
    if r.status_code == 404:
        raise UpdateError("Релизы не найдены: репозиторий приватный (нужен токен) или в нём нет релизов.")
    if r.status_code in (401, 403):
        raise UpdateError("GitHub отклонил запрос (лимит запросов или неверный токен).")
    if r.status_code != 200:
        raise UpdateError(f"GitHub ответил {r.status_code}")
    d = r.json()
    return Release(tag=d.get("tag_name", ""), url=d.get("html_url", ""), notes=(d.get("body") or "").strip(),
                   assets=d.get("assets") or [])


def check(repo: str = DEFAULT_REPO, token: str | None = None, api: str = API, session=requests,
          current: str = __version__) -> tuple[Release, bool]:
    """-> (последний релиз, он новее текущей версии?)."""
    rel = fetch_latest(repo, token, api, session)
    return rel, bool(rel.tag) and is_newer(rel.tag, current)


def pick_asset(release: Release, mode: str) -> dict | None:
    for a in release.assets:
        n = a.get("name", "")
        if mode == "exe" and n == EXE_NAME:
            return a
        if mode == "appimage" and n.endswith(".AppImage") and "x86_64" in n:
            return a
    return None


def _asset_request(asset: dict, token: str | None, session):
    """Приватный репозиторий: скачивание через API-адрес ассета с токеном; публичный — прямая ссылка."""
    use_api = bool(token and asset.get("url"))
    url = asset["url"] if use_api else asset.get("browser_download_url", "")
    if not (url.startswith("https://") or (ALLOW_INSECURE and url.startswith("http://"))):
        raise UpdateError("Адрес файла обновления не https: обновление отменено.")
    headers = _headers(token, "application/octet-stream") if use_api else {"User-Agent": f"yaklass-solver/{__version__}"}
    return session.get(url, headers=headers, stream=True, timeout=TIMEOUT, allow_redirects=True)


def expected_sha256(asset: dict, release: Release, token: str | None = None, session=requests) -> str:
    """SHA-256 файла: поле digest из API, а если его нет — запись в SHA256SUMS.txt релиза. Нет ни того ни другого — отказ."""
    digest = asset.get("digest") or ""
    if digest.startswith("sha256:") and re.fullmatch(r"[0-9a-fA-F]{64}", digest[7:]):
        return digest[7:].lower()
    sums = next((a for a in release.assets if a.get("name") == "SHA256SUMS.txt"), None)
    if sums is None:
        raise UpdateError("У релиза нет контрольной суммы файла: обновление отменено.")
    r = _asset_request(sums, token, session)
    if r.status_code != 200:
        raise UpdateError("Не удалось получить SHA256SUMS.txt: обновление отменено.")
    for line in r.content.decode("utf-8", "replace").splitlines():
        m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+)$", line.strip())
        if m and m.group(2).strip() == asset["name"]:
            return m.group(1).lower()
    raise UpdateError("В SHA256SUMS.txt нет записи для этого файла: обновление отменено.")


def download(asset: dict, release: Release, dest: Path, token: str | None = None, session=requests,
             progress: Callable[[int, int], None] | None = None) -> Path:
    """Скачивает во временный файл и переименовывает в dest, только если размер и SHA-256 сошлись."""
    want = expected_sha256(asset, release, token, session)
    try:
        r = _asset_request(asset, token, session)
    except requests.RequestException as e:
        raise UpdateError(f"Нет связи при скачивании: {e.__class__.__name__}") from e
    if r.status_code != 200:
        raise UpdateError(f"Не удалось скачать обновление: код {r.status_code}")
    total = int(asset.get("size") or r.headers.get("Content-Length") or 0)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    h, done = hashlib.sha256(), 0
    try:
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(CHUNK):
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        if total and done != total:
            raise UpdateError(f"Скачано {done} из {total} байт: файл обрезан, обновление отменено.")
        if h.hexdigest() != want:
            raise UpdateError("Контрольная сумма файла не совпала: обновление отменено, файл удалён.")
        os.replace(tmp, dest)
    except requests.RequestException as e:
        raise UpdateError(f"Обрыв связи при скачивании: {e.__class__.__name__}") from e
    finally:
        tmp.unlink(missing_ok=True)
    return dest


# ---------------------------------------------------------------- применение
def apply_appimage(new_file: Path, target: Path) -> None:
    """Атомарная подмена AppImage. Рядом с целью создаётся временный файл (та же файловая система) и переименовывается."""
    staged = target.with_name(target.name + ".new")
    try:
        staged.write_bytes(new_file.read_bytes())
        staged.chmod(0o755)
        os.replace(staged, target)
    except OSError as e:
        staged.unlink(missing_ok=True)
        raise UpdateError(f"Нет прав на запись в {target.parent}: скачайте обновление вручную со страницы релиза.") from e


def apply_exe(new_file: Path, target: Path) -> Path:
    """Windows: запущенный exe можно переименовать, но не перезаписать. -> путь к *.old-* (удаляется при следующем запуске).
    При сбое старый файл возвращается на место."""
    old = target.with_name(f"{target.name}.old-{int(time.time())}")
    try:
        os.replace(target, old)
    except OSError as e:
        raise UpdateError(f"Не удалось заменить {target.name}: {e}") from e
    try:
        os.replace(new_file, target)
    except OSError as e:
        try:
            os.replace(old, target)                  # откат
        except OSError:
            raise UpdateError(f"Не удалось установить новый файл и вернуть старый: прежняя версия лежит в {old}") from e
        raise UpdateError(f"Не удалось установить новый файл (прежняя версия сохранена): {e}") from e
    return old


def cleanup_old(target: Path | None = None) -> int:
    """Удаляет *.old-* рядом с exe после обновления. -> сколько удалено."""
    target = target or Path(sys.executable)
    n = 0
    for f in target.parent.glob(target.name + ".old-*"):
        try:
            f.unlink()
            n += 1
        except OSError:
            pass
    return n


def restart(target: Path, mode: str) -> None:
    """Запускает обновлённое приложение вместо текущего (не возвращается)."""
    if mode == "appimage":
        os.execv(str(target), [str(target), *sys.argv[1:]])
    flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0       # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([str(target), *sys.argv[1:]], close_fds=True, creationflags=flags)
    os._exit(0)


def install(release: Release, plan: Plan, workdir: Path, token: str | None = None, session=requests,
            progress: Callable[[int, int], None] | None = None) -> Path:
    """Скачать, проверить и заменить файл приложения. Перезапуск — отдельным вызовом restart(), когда всё остальное закрыто."""
    if plan.mode == "none" or plan.target is None:
        raise UpdateError(plan.reason or "Самообновление недоступно для этого способа запуска.")
    asset = pick_asset(release, plan.mode)
    if asset is None:
        raise UpdateError("В релизе нет файла для вашей системы.")
    new = download(asset, release, workdir / f"update-{release.version}-{asset['name']}", token, session, progress)
    try:
        if plan.mode == "appimage":
            apply_appimage(new, plan.target)
        else:
            apply_exe(new, plan.target)
    finally:
        new.unlink(missing_ok=True)
    return plan.target
