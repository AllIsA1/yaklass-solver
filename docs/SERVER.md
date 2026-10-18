# Установка сервера

Требования: Linux, Python ≥ 3.11, домен, токен бота от @BotFather, API  ключ ИИ/локальная модель.

```bash
git clone https://github.com/AllIsA1/yaklass-solver /opt/yaklass-solver && cd /opt/yaklass-solver
bash deploy/install-server.sh --systemd         # окружение, зависимости, .env, база, служба systemd
nano .env                                       # DOMAIN, PORT, TELEGRAM_BOT_TOKEN, ADMIN_IDS, LLM_*
sudo systemctl restart yaklass-solver && journalctl -u yaklass-solver -f
```

**Обновление сервера:** `git pull && bash deploy/install-server.sh --systemd` (скрипт переустанавливает пакет и перезапускает
службу).

Без `--systemd` он только готовит окружение (запуск вручную: `.venv/bin/yaklass-server`); `--print-unit` показывает
юнит systemd без установки.

**Выход в сеть**
На данный момент есть готовая конфигурация только для nginx, но вы можете использовать Caddy и прочие аналоги.

***nginx***: Вставьте `deploy/nginx.conf.example` (WebSocket-заголовки обязательны), порт должен совпадать с `PORT` в `.env`.
Сервис слушает только `127.0.0.1`: наружу порт не открывается.

**Проверка:** `curl https://<домен>/healthz` → `{"ok":true,...}`

Адрес сервера бот передаёт приложению внутри **кода подключения** (`/link`: строка `yk1.<адрес>.<код>`), пользователь
его не вводит и не видит. Адрес берётся из `DOMAIN` (или `PUBLIC_URL`, если задан) в `.env`.

## Управление: `yaklassctl`
Запускать из `/opt/yaklass-solver` (читает тот же `.env`/БД):
```bash
.venv/bin/yaklassctl stats
.venv/bin/yaklassctl users list | add <id> | ban <id> | unban <id> | quota <id> <N|default>
.venv/bin/yaklassctl agents list [<tg_id>] | revoke <agent_id> | revoke-user <tg_id>
.venv/bin/yaklassctl invite create [--uses N] [--note ...] | invite list     # REGISTRATION=invite
```
Бан сразу отзывает токены пользователя; активное WebSocket-соединение закроется при следующем запросе/переподключении
(или перезапуском службы).
