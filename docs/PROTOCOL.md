# Протокол агент ↔ сервер

База: `https://<DOMAIN>`. Токен агента: `Authorization: Bearer yks_...` (HTTP), первым сообщением (WebSocket).

## HTTP
| Запрос | Описание |
|---|---|
| `GET /healthz` | `{ok, agents_online}` |
| `POST /v1/link` `{code, device_name}` | обмен кода из бота на токен → `{token, username}`. Код одноразовый, 10 мин. 10 попыток/мин с IP |
| `GET /v1/me` | `{tg_id, username, quota_per_day, used_today, min_task_interval_s, agent_online}` |
| `POST /v1/solve` `{task}` | решение задания → `{answers, confidence, evidence, remaining_today}` |

Ошибки: `401` нет/неверный токен, `403` пользователь заблокирован, `413` запрос > `MAX_BODY_BYTES`,
`422` неверная структура, `429` rate limit (`Retry-After`) или суточная квота, `502` модель не дала ответа
(запрос **не списывается**), `500` внутренняя ошибка.

### `task`
```json
{"position": 3, "title": "…", "points": 3, "text": "Условие с метками [[имя_поля]]",
 "images": ["https://….selcdn.net/…/a.png"],
 "fields": [{"name": "e6r1|dd", "kind": "dropdown|text|radio|checkbox|dnd",
             "options": [{"value": "…", "text": "…", "image": ""}]}]}
```
Структура — результат `yaklass_bot.parser.parse_exercise`. Лимиты: текст ≤ 20 000, полей ≤ 60, вариантов ≤ 60.
Картинки сервер скачивает только по `https` с доменов `IMAGE_HOSTS` (по умолчанию `selcdn.net`, `yaklass.ru`)
и только с публичных адресов.

### `answers`
`{ "<имя поля>": "<значение>" }`: для `dropdown/radio/dnd` — `value` выбранного варианта; для `checkbox` — `value`
вариантов через запятую; для `text` — строка.

## WebSocket `/v1/agent`
1. Клиент подключается и **в течение 10 с** шлёт `{"type":"auth","token":"yks_..."}`. Иначе закрытие `4401`.
2. Сервер: `{"type":"welcome","min_task_interval_s":0}`.
3. Дальше JSON-сообщения (≤ 64 КБ).

**Сервер → агент** (только эти команды):
`{"type":"cmd","cmd":"status|list_works|pause|resume|stop"}` и
`{"type":"cmd","cmd":"start_work","work_id":"123456","mode":"auto|dry"}` (`work_id` — только цифры).

**Агент → сервер** (остальные типы игнорируются; строки обрезаются до 500 символов):
| type | поля |
|---|---|
| `hello` | `device` |
| `works` | `works: [{id, subject, title, deadline}]`, `new: bool` |
| `progress` | `work_id, done, total, note` |
| `finished` | `work_id, completed: bool, summary: {…}` |
| `error` | `message` |
| `status` | `state: idle|running|paused, work_id` |
| `ack` | `cmd, ok: bool, msg` |

Агент **обязан** выполнять `start_work` только после явной настройки пользователем «разрешить удалённый запуск»
и не принимать никаких других инструкций от сервера. Закрытие `4403` — токен отозван (`/unlink`).
