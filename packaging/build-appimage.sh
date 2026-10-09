#!/usr/bin/env bash
# Собирает AppImage из папки PyInstaller (dist-app/YaklassSolver). Запуск из корня репозитория:
#   pyinstaller packaging/yaklass-app.spec --noconfirm --distpath dist-app --workpath build-app
#   bash packaging/build-appimage.sh            -> dist-app/YaklassSolver-x86_64.AppImage
set -euo pipefail
ARCH="${ARCH:-x86_64}"
APPDIR="build-app/AppDir"
rm -rf "$APPDIR" && mkdir -p "$APPDIR/usr/bin"
cp -a dist-app/YaklassSolver/. "$APPDIR/usr/bin/"
cp yaklass_app/assets/icon.png "$APPDIR/yaklass-solver.png"
cp yaklass_app/assets/icon.png "$APPDIR/.DirIcon"      # именно её файловые менеджеры показывают для самого файла AppImage
cat > "$APPDIR/yaklass-solver.desktop" <<'D'
[Desktop Entry]
Type=Application
Name=Yaklass Solver
Comment=Помощник для проверочных работ
Exec=YaklassSolver
Icon=yaklass-solver
Categories=Education;
Terminal=false
D
cat > "$APPDIR/AppRun" <<'R'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/YaklassSolver" "$@"
R
chmod +x "$APPDIR/AppRun"
TOOL="build-app/appimagetool-$ARCH.AppImage"
if [ ! -x "$TOOL" ]; then
  curl -fsSL -o "$TOOL" "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage"
  chmod +x "$TOOL"
fi
ARCH="$ARCH" "$TOOL" --appimage-extract-and-run "$APPDIR" "dist-app/YaklassSolver-$ARCH.AppImage"
