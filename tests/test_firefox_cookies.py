"""Импорт кук из Firefox: свежие куки в журнале WAL, сессионные куки из файлов сессии, профиль не изменяется."""
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path

import lz4.block
import pytest

from yaklass_bot.session import AuthError, load_cookies, snapshot_firefox_profile

SCHEMA = ("CREATE TABLE moz_cookies (id INTEGER PRIMARY KEY, originAttributes TEXT NOT NULL DEFAULT '', name TEXT, "
          "value TEXT, host TEXT, path TEXT, expiry INTEGER, lastAccessed INTEGER, creationTime INTEGER, "
          "isSecure INTEGER, isHttpOnly INTEGER, inBrowserElement INTEGER DEFAULT 0, sameSite INTEGER DEFAULT 0, "
          "schemeMap INTEGER DEFAULT 0)")


def make_profile(tmp_path, wal_cookie=True, recovery=True, previous=False):
    """Профиль «работающего» Firefox: одна кука в основном файле, одна — только в cookies.sqlite-wal."""
    prof = tmp_path / "profile"
    (prof / "sessionstore-backups").mkdir(parents=True)
    db = sqlite3.connect(prof / "cookies.sqlite")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA wal_autocheckpoint=0")
    db.execute(SCHEMA)
    exp = int(time.time()) + 9999
    db.execute("INSERT INTO moz_cookies(name,value,host,path,expiry,isSecure,isHttpOnly) "
               "VALUES('old','1','.yaklass.ru','/',?,1,1)", (exp,))
    db.commit()
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    if wal_cookie:
        db.execute("INSERT INTO moz_cookies(name,value,host,path,expiry,isSecure,isHttpOnly) "
                   "VALUES('fresh_in_wal','2','.yaklass.ru','/',?,1,1)", (exp,))
        db.execute("INSERT INTO moz_cookies(name,value,host,path,expiry,isSecure,isHttpOnly) "
                   "VALUES('other','3','.example.com','/',?,1,1)", (exp,))
        db.commit()
    raw = json.dumps({"cookies": [{"host": "www.yaklass.ru", "path": "/", "name": "SESSION", "value": "s"}]}).encode()
    blob = b"mozLz40\0" + lz4.block.compress(raw)
    if recovery:
        (prof / "sessionstore-backups" / "recovery.jsonlz4").write_bytes(blob)
    if previous:
        (prof / "sessionstore-backups" / "previous.jsonlz4").write_bytes(blob)
    return prof, db


def names(jar):
    return {c.name for c in jar}


def test_fresh_cookies_from_wal_and_session_cookies_are_loaded(tmp_path):
    prof, db = make_profile(tmp_path)
    assert (prof / "cookies.sqlite-wal").stat().st_size > 0                      # свежая кука действительно только в WAL
    jar = load_cookies("firefox", "yaklass.ru", cookie_file=str(prof / "cookies.sqlite"))
    assert names(jar) == {"old", "fresh_in_wal", "SESSION"}                      # «other» (чужой домен) отфильтрована
    db.close()


def test_session_cookies_come_from_previous_session_when_firefox_is_closed(tmp_path):
    prof, db = make_profile(tmp_path, wal_cookie=False, recovery=False, previous=True)
    assert names(load_cookies("firefox", "yaklass.ru", cookie_file=str(prof / "cookies.sqlite"))) == {"old", "SESSION"}
    db.close()


def test_profile_is_not_modified_and_temp_files_are_removed(tmp_path):
    prof, db = make_profile(tmp_path)
    before = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in prof.rglob("*") if p.is_file()}
    tmpdir = Path(tempfile.gettempdir())
    leftovers = set(tmpdir.glob("yk-ff-*"))
    load_cookies("firefox", "yaklass.ru", cookie_file=str(prof / "cookies.sqlite"))
    after = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in prof.rglob("*") if p.is_file()}
    assert before == after                                                       # журнал WAL у пользователя не тронут
    assert set(tmpdir.glob("yk-ff-*")) == leftovers                              # снимок удалён
    db.close()


def test_snapshot_contains_everything_needed(tmp_path):
    prof, db = make_profile(tmp_path)
    out = snapshot_firefox_profile(prof / "cookies.sqlite", tmp_path / "snap")
    con = sqlite3.connect(out)
    assert {r[0] for r in con.execute("select name from moz_cookies")} == {"old", "fresh_in_wal", "other"}
    con.close()
    assert (tmp_path / "snap" / "sessionstore-backups" / "recovery.jsonlz4").is_file()
    db.close()


def test_missing_profile_is_a_clear_error(tmp_path):
    with pytest.raises(AuthError, match="firefox"):
        load_cookies("firefox", "yaklass.ru", cookie_file=str(tmp_path / "nope" / "cookies.sqlite"))


@pytest.mark.skipif(os.name == "nt", reason="профиль по умолчанию ищется по HOME (Linux)")
def test_default_profile_is_found_via_profiles_ini(tmp_path, monkeypatch):
    home = tmp_path / "home"
    ff = home / ".mozilla" / "firefox"
    (ff / "abc.default-release").mkdir(parents=True)
    (ff / "profiles.ini").write_text("[Install4F96D1932A9F858E]\nDefault=abc.default-release\nLocked=1\n\n"
                                     "[Profile0]\nName=default-release\nIsRelative=1\nPath=abc.default-release\n"
                                     "Default=1\n\n[General]\nVersion=2\n")
    prof, db = make_profile(tmp_path)
    for f in prof.rglob("*"):
        if f.is_file():
            dst = ff / "abc.default-release" / f.relative_to(prof)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(f.read_bytes())
    monkeypatch.setenv("HOME", str(home))
    assert names(load_cookies("firefox", "yaklass.ru")) == {"old", "fresh_in_wal", "SESSION"}
    db.close()


def test_not_logged_in_message_for_firefox_mentions_keeping_it_open(tmp_path, monkeypatch):
    from yaklass_app import backend as be
    from yaklass_app.settings import AppSettings, Store
    store = Store(tmp_path, use_keyring=False)
    store.save(AppSettings(browser_mode="cookies", cookie_browser="firefox"))
    monkeypatch.setattr(be, "load_cookies", lambda *a, **k: [])
    b = be.PlaywrightBackend.__new__(be.PlaywrightBackend)
    b.store, b.dir = store, tmp_path
    with pytest.raises(be.NotLoggedIn, match="Не закрывайте Firefox"):
        b._open(headless=True)


def test_cookie_selftest_passes_in_source_tree(tmp_path):
    from yaklass_app.__main__ import cookie_selftest
    assert cookie_selftest(tmp_path) == ""
