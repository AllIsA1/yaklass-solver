"""Короткий звук, когда бот завершил работу. Без Qt Multimedia (её нет в сборке): системные средства —
winsound на Windows, paplay / pw-play / aplay на Linux, afplay на macOS. Проиграть не вышло — тихо пропускаем."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

DONE_SOUND = Path(__file__).with_name("assets") / "done.wav"
LINUX_PLAYERS = (("paplay", []), ("pw-play", []), ("aplay", ["-q"]))


def player_command(path: Path, platform: str = sys.platform, which=shutil.which) -> list[str] | None:
    """Команда для проигрывания (Linux/macOS) или None (Windows — winsound, или нечем)."""
    if platform == "darwin":
        return ["afplay", str(path)] if which("afplay") else None
    if platform.startswith("win"):
        return None
    for name, args in LINUX_PLAYERS:
        exe = which(name)
        if exe:
            return [exe, *args, str(path)]
    return None


def system_env(env: dict | None = None) -> dict:
    """Окружение для системной программы: PyInstaller (AppImage) подставляет свои библиотеки в LD_LIBRARY_PATH,
    а исходное значение хранит в *_ORIG — с чужими библиотеками paplay/aplay могут не запуститься."""
    env = dict(os.environ if env is None else env)
    for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        orig = env.pop(var + "_ORIG", None)
        if orig is not None:
            env[var] = orig
        elif getattr(sys, "frozen", False):
            env.pop(var, None)
    return env


def _play_blocking(path: Path) -> bool:
    if not path.is_file():
        return False
    if sys.platform.startswith("win"):
        try:
            import winsound
            winsound.PlaySound(str(path), winsound.SND_FILENAME)
            return True
        except Exception:  # noqa: BLE001
            return False
    cmd = player_command(path)
    if not cmd:
        return False
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False,
                       env=system_env())
        return True
    except Exception:  # noqa: BLE001
        return False


def play_done(path: Path = DONE_SOUND) -> None:
    """В фоне, чтобы не задерживать окно."""
    threading.Thread(target=_play_blocking, args=(path,), daemon=True, name="done-sound").start()
