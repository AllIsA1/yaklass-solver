# Установка сервера

Требования: Linux, Python ≥ 3.11, домен, токен бота от @BotFather, API  ключ ИИ/локальная модель.

```bash
git clone https://github.com/AllIsA1/yaklass-solver /opt/yaklass-solver && cd /opt/yaklass-solver
bash deploy/install-server.sh --systemd         # окружение, зависимости, .env, база, служба systemd
nano .env                                       # DOMAIN, PORT, TELEGRAM_BOT_TOKEN, TELEGRAM_API_ID/HASH, ADMIN_IDS, LLM_*
sudo systemctl restart yaklass-solver && journalctl -u yaklass-solver -f
```

**Обновление сервера:** `git pull && bash deploy/install-server.sh --systemd` (скрипт переустанавливает пакет и перезапускает
службу).

Без `--systemd` он только готовит окружение (запуск вручную: `.venv/bin/yaklass-server`); `--print-unit` показывает
юнит systemd без установки.

**Telegram.** Бот работает на Telethon (MTProto) как бот: кроме токена от @BotFather нужны `TELEGRAM_API_ID` и
`TELEGRAM_API_HASH` — на https://my.telegram.org → *API development tools* (создайте приложение, любое название).
Если Telegram с сервера недоступен напрямую, задайте MTProto-прокси в `TELEGRAM_MTPROXY`: ссылка `tg://proxy?server=…&port=…&secret=…`,
`https://t.me/proxy?…` или `host:port:secret`; секрет — hex (можно с префиксом `dd`), секреты `ee…` (fake-TLS) Telethon не
поддерживает. Бот хранит сессию в `data/bot.session` (путь — `TELEGRAM_SESSION`), а адрес каждого пользователя — в базе:
уведомления доходят после того, как пользователь хотя бы раз написал боту (при переходе с прежней версии — отправьте
боту `/status` один раз).

**Выход в сеть**
На данный момент есть готовая конфигурация только для nginx, но вы можете использовать Caddy и прочие аналоги.

***nginx***: Вставьте `deploy/nginx.conf.example` (WebSocket-заголовки обязательны), порт должен совпадать с `PORT` в `.env`.
Сервис слушает только `127.0.0.1`: наружу порт не открывается.

**Проверка:** `curl https://<домен>/healthz` → `{"ok":true,...}`

Адрес сервера бот передаёт приложению внутри **кода подключения** (`/link`: строка `домен:код`), пользователь
его не вводит и не видит. Адрес берётся из `DOMAIN` (или `PUBLIC_URL`, если задан) в `.env`.

**Запасная модель.** Если задан `LLM_FALLBACK_MODEL` (остальные `LLM_FALLBACK_*` — в `.env.example`), то при недоступности
основной модели (сеть, 5xx, лимит, закончился баланс) запросы идут на запасную; основная после сбоя не пробуется
`LLM_FALLBACK_COOLDOWN_S` секунд (по умолчанию 60). Состояние — в `GET /healthz` (поле `fallback`) и в журнале службы.
Только для сервера: приложение со «Своим API» запасную модель не использует.

**Аудирование.** `AUDIO_MODE=transcribe` (по умолчанию): запись скачивается сервером (только с `IMAGE_HOSTS`) и
расшифровывается через `STT_BASE_URL` (пусто — `LLM_BASE_URL` и `LLM_API_KEY`), модель `STT_MODEL` (`whisper-1`).
Если основная модель не у OpenAI, у её провайдера может не быть `/audio/transcriptions`: укажите отдельный сервис,
например Groq (`STT_BASE_URL=https://api.groq.com/openai/v1`, `STT_MODEL=whisper-large-v3`, `STT_API_KEY=…`).
Если сервис расшифровки отвечает 404/405, сервер сам переходит на `native` до перезапуска (записи уходят модели).
`AUDIO_MODE=chat` — расшифровку делает модель со звуком через `chat/completions` (`STT_MODEL=gemini-…-flash`,
адрес и ключ — `STT_*` или как у `LLM_*`), текст уходит основной модели; так основной модели звук понимать не нужно.
`AUDIO_MODE=native` — запись уходит модели как `input_audio` (Gemini, `gpt-4o-audio-preview`). `off` — такие задания
клиенты пропускают.

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
