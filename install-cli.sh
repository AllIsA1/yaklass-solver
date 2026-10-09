#!/usr/bin/env sh
# Установка yaklass-bot на Linux / macOS.
#   sh install-cli.sh                      # из папки проекта (рядом со скриптом)
#   sh install-cli.sh yaklass_bot-0.1.0-py3-none-any.whl   # из готового wheel
set -e
SRC="${1:-$(cd "$(dirname "$0")" && pwd)}"
TARGET="$SRC[browser]"      # extra «browser» = Playwright (режим run)

if command -v uv >/dev/null 2>&1; then
  uv tool install --force "$TARGET"
  PYBIN="$(uv tool dir)/yaklass-bot/bin/python"
elif command -v pipx >/dev/null 2>&1; then
  pipx install --force "$TARGET"
  PYBIN="$(pipx environment --value PIPX_LOCAL_VENVS)/yaklass-bot/bin/python"
else
  echo "Нужен uv или pipx. Установите один из них:" >&2
  echo "  uv:   curl -LsSf https://astral.sh/uv/install-cli.sh | sh" >&2
  echo "  pipx: https://pipx.pypa.io/stable/installation/" >&2
  exit 1
fi

# Браузер для режима run. Не нужен, если в config.toml задан [run] channel = "chrome" / "msedge".
"$PYBIN" -m playwright install chromium || echo "Chromium не установлен: укажите [run] channel в config.toml"

echo
echo "Установлено. Если команда не найдена: uv tool update-shell  (или добавьте ~/.local/bin в PATH)"

# Стартовая настройка (браузер, API/аккаунт, vision...). Пропускается без терминала; позже: yaklass-bot setup
if [ -t 0 ] && [ -t 1 ]; then
  "$(dirname "$PYBIN")/yaklass-bot" setup || echo "Настройка пропущена. Запустить позже: yaklass-bot setup"
else
  echo "Настройка: yaklass-bot setup"
fi
