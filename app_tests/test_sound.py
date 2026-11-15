"""Звук по завершении работы: файл на месте, проигрыватель выбирается по системе, окружение AppImage чистится."""
import wave
from pathlib import Path

from yaklass_app.sound import DONE_SOUND, player_command, system_env


def test_sound_file_is_short_wav():
    with wave.open(str(DONE_SOUND)) as w:
        assert 0.3 < w.getnframes() / w.getframerate() < 2.0


def test_player_choice():
    p = Path("/x/done.wav")
    assert player_command(p, "linux", which=lambda n: f"/usr/bin/{n}" if n == "aplay" else None) == \
        ["/usr/bin/aplay", "-q", "/x/done.wav"]
    assert player_command(p, "linux", which=lambda n: None) is None
    assert player_command(p, "darwin", which=lambda n: "/usr/bin/afplay") == ["afplay", "/x/done.wav"]
    assert player_command(p, "win32", which=lambda n: "x") is None


def test_appimage_library_path_is_restored_for_system_player():
    env = system_env({"LD_LIBRARY_PATH": "/tmp/_MEI/lib", "LD_LIBRARY_PATH_ORIG": "/usr/local/lib", "A": "1"})
    assert env["LD_LIBRARY_PATH"] == "/usr/local/lib" and "LD_LIBRARY_PATH_ORIG" not in env and env["A"] == "1"
