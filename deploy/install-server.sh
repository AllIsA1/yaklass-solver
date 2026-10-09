#!/usr/bin/env bash
# Установка серверной части yaklass-solver (без Docker). Запускать из клонированного репозитория:
#   bash deploy/install-server.sh              # окружение + зависимости + .env + база
#   bash deploy/install-server.sh --systemd    # + служба systemd (нужен sudo)
#   bash deploy/install-server.sh --print-unit # только показать юнит systemd
# Скрипт можно запускать повторно: .env и база не перезаписываются.
set -euo pipefail

WITH_SYSTEMD=0; PRINT_UNIT=0
for a in "$@"; do
  case "$a" in
    --systemd) WITH_SYSTEMD=1 ;;
    --print-unit) PRINT_UNIT=1 ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "неизвестный параметр: $a" >&2; exit 2 ;;
  esac
done

DIR="$(cd "$(dirname "$0")/.." && pwd)"
OWNER="$(stat -c %U "$DIR")"

render_unit() {
  local protect_home=true
  case "$DIR" in /home/*) protect_home=false ;; esac     # иначе служба не увидит каталог в /home
  sed -e "s#^User=.*#User=$OWNER#" \
      -e "s#/opt/yaklass-solver#$DIR#g" \
      -e "s#^ProtectHome=.*#ProtectHome=$protect_home#" \
      "$DIR/deploy/yaklass-solver.service"
}

if [ "$PRINT_UNIT" = 1 ]; then render_unit; exit 0; fi

PY="$(command -v python3 || true)"
[ -n "$PY" ] || { echo "Нужен python3 (>= 3.11)" >&2; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || { echo "Нужен Python >= 3.11, найден $("$PY" -V)" >&2; exit 1; }

echo "==> Окружение и зависимости ($DIR/.venv)"
[ -d "$DIR/.venv" ] || "$PY" -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install --quiet --upgrade pip
# editable: код берётся прямо из репозитория, после `git pull` достаточно перезапустить службу
"$DIR/.venv/bin/pip" install --quiet -e "$DIR[server]"

echo "==> Настройки"
if [ ! -f "$DIR/.env" ]; then
  cp "$DIR/.env.example" "$DIR/.env"
  chmod 600 "$DIR/.env"
  NEW_ENV=1
  echo "    создан $DIR/.env — заполните его (токен бота, ключ модели, домен, порт)"
else
  NEW_ENV=0
  echo "    $DIR/.env уже есть — не трогаю"
fi
mkdir -p "$DIR/data"

echo "==> Проверка"
(cd "$DIR" && .venv/bin/yaklassctl stats >/dev/null) && echo "    база данных создана: $DIR/data"

env_value() { grep -E "^$1=" "$DIR/.env" | tail -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*//' | tr -d '"'"'"' '; }
READY=1
[ -n "$(env_value TELEGRAM_BOT_TOKEN)" ] || READY=0
[ -n "$(env_value LLM_API_KEY)" ] || READY=0

if [ "$WITH_SYSTEMD" = 1 ]; then
  echo "==> Служба systemd (потребуется sudo)"
  render_unit | sudo tee /etc/systemd/system/yaklass-solver.service >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl enable yaklass-solver >/dev/null
  if [ "$READY" = 1 ]; then
    sudo systemctl restart yaklass-solver
    echo "    служба запущена: journalctl -u yaklass-solver -f"
  else
    echo "    служба включена, но НЕ запущена: заполните TELEGRAM_BOT_TOKEN и LLM_API_KEY в $DIR/.env,"
    echo "    затем: sudo systemctl start yaklass-solver"
  fi
fi

cat <<EOF

Готово. Дальше:
  1. Заполните $DIR/.env (если ещё не сделали).
  2. nginx: вставьте deploy/nginx.conf.example (порт = PORT из .env), перезагрузите nginx.
  3. Запуск: $( [ "$WITH_SYSTEMD" = 1 ] && echo "уже настроен (sudo systemctl start yaklass-solver)" || echo "bash deploy/install-server.sh --systemd   (или вручную: .venv/bin/yaklass-server)" )
  4. Проверка: curl https://<домен>/healthz ; в Telegram: /start, /link
  Управление: cd $DIR && .venv/bin/yaklassctl --help
EOF
