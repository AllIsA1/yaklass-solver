# Установка сервера (без Docker, за вашим nginx)

Требования: Linux, Python ≥ 3.11, nginx с HTTPS на домене, токен бота от @BotFather, ключ модели.

```bash
git clone git@github.com:<вы>/yaklass-solver.git /opt/yaklass-solver && cd /opt/yaklass-solver
bash deploy/install-server.sh --systemd         # окружение, зависимости, .env, база, служба systemd
nano .env                                       # DOMAIN, PORT, TELEGRAM_BOT_TOKEN, ADMIN_IDS, LLM_*
sudo systemctl restart yaklass-solver && journalctl -u yaklass-solver -f
```
Скрипт безопасно запускать повторно: `.env` и база не перезаписываются.

**Обновление сервера:** `git pull && bash deploy/install-server.sh --systemd` (скрипт переустанавливает пакет и перезапускает
службу). Именно `restart`, а не `reload`: у службы нет перечитывания конфигурации, `systemctl reload` ничего не делает.
Пакет ставится в режиме разработки (`pip install -e`), поэтому после `git pull` служба берёт новый код из каталога.
Без `--systemd` он только готовит окружение (запуск вручную: `.venv/bin/yaklass-server`); `--print-unit` показывает
юнит systemd без установки. Служба запускается от владельца каталога; если он в `/home`, защита `ProtectHome`
отключается автоматически.

**nginx:** вставьте `deploy/nginx.conf.example` (WebSocket-заголовки обязательны), порт должен совпадать с `PORT` в `.env`.
Сервис слушает только `127.0.0.1`: наружу порт не открывается.

**Проверка:** `curl https://<домен>/healthz` → `{"ok":true,...}`; в Telegram `/start`, `/link`.

## Проверка WebSocket и частые проблемы
```bash
curl -i -N --http1.1 -H "Connection: Upgrade" -H "Upgrade: websocket" \
     -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: SGVsbG8sIHdvcmxkIQ==" https://<домен>/v1/agent
```
Должно быть `HTTP/1.1 101 Switching Protocols`. Если приходит `404`/`426` (в приложении: «Handshake status 404») —
nginx не передаёт заголовки `Upgrade`/`Connection`: добавьте `location /v1/agent` из `deploy/nginx.conf.example`
(важны `proxy_http_version 1.1` и оба `proxy_set_header`), затем `sudo nginx -t && sudo systemctl reload nginx`.
`502/503/504` — служба не запущена (`systemctl status yaklass-solver`). Адрес, который бот показывает в `/link`,
берётся из `DOMAIN` (или `PUBLIC_URL`, если задан) в `.env`.

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

## Эксплуатация и безопасность
- Резервная копия: `sqlite3 data/server.db ".backup backup.db"` (или копия файла при остановленной службе).
- `.env` содержит секреты (токен бота, ключ модели): права `600`, в git не попадает.
- Расходы на модель ограничивают `DAILY_QUOTA`, `RATE_PER_MIN`, `MAX_CONCURRENT_SOLVES`.
- Тексты заданий не логируются (`LOG_TASKS=false`). В БД нет ни кук, ни паролей ЯКласса.
- Данные пользователей (Telegram id, username) — персональные данные; храните минимум и сообщите об этом пользователям.
