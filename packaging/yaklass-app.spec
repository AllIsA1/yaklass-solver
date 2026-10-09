# -*- mode: python ; coding: utf-8 -*-
# Сборка: pyinstaller packaging/yaklass-app.spec --noconfirm
#   YK_ONEFILE=1 — один файл (Windows .exe); иначе папка (Linux: оборачивается в AppImage).
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

ONEFILE = os.environ.get("YK_ONEFILE") == "1"
datas, binaries, hiddenimports = [], [], []
for pkg in ("playwright",):                       # драйвер (node + cli.js) нужен для запуска браузера
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h
hiddenimports += collect_submodules("keyring.backends") + ["websocket", "bs4", "lxml", "lxml.etree", "browser_cookie3"]
datas += [("../yaklass_app/assets", "yaklass_app/assets"), ("../yaklass_bot/config.example.toml", "yaklass_bot")]

a = Analysis(["entry.py"], pathex=[".."], binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             excludes=["tkinter", "pytest", "fastapi", "uvicorn", "aiogram"], noarchive=False)
pyz = PYZ(a.pure)
icon = "../yaklass_app/assets/icon.ico" if sys.platform == "win32" else None

if ONEFILE:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="YaklassSolver", console=False, icon=icon,
              upx=False, runtime_tmpdir=None)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="YaklassSolver", console=False, icon=icon, upx=False)
    coll = COLLECT(exe, a.binaries, a.datas, name="YaklassSolver", upx=False)
