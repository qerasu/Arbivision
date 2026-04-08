# Arbivision

Arbivision ищет арбитражные возможности между **Polymarket** и **Predict.Fun**, сохраняет найденные пары рынков в PostgreSQL и отправляет Telegram-алерты по подходящим возможностям.

## Что умеет сервис

- синхронизирует рынки с обеих площадок
- матчингует похожие рынки и строит `outcome_mapping`
- загружает ордербуки и считает profitable directions
- дедуплицирует возможности через Redis
- создаёт и доставляет Telegram-алерты
- даёт внутренние API-ручки для health, status и админ-диагностики

## Стек

- Python 3
- FastAPI
- SQLAlchemy + asyncpg
- Alembic
- Redis
- aiogram
- PostgreSQL
- Docker Compose

## Структура проекта

```text
arbitrage_bot/
  adapters/        интеграции с Polymarket и Predict.Fun
  api/             внутренние HTTP-ручки
  core/            config, db, redis, logging, observability
  models/          SQLAlchemy ORM-модели
  services/        ingestion, matcher, orderbook, calculator, fanout
  tg_bot/          Telegram UI, обработчики и настройки пользователей
  main.py          FastAPI app c lifespan-рантаймом
  api_app.py       FastAPI app только с API, без фоновых рантаймов
  runtime.py       общий запуск worker / fanout / telegram
  worker.py        основной цикл обработки рынков
  run_worker.py    запуск только worker
  run_fanout.py    запуск только fanout
  run_telegram.py  запуск только telegram polling
start.py           локальный dev-запуск проекта
stop.py            безопасная остановка процесса и контейнеров
```

## Как работает пайплайн

1. `IngestionService` загружает рынки и делает upsert в БД.
2. `MatcherService` строит пары `MarketPair` между площадками.
3. `OrderbookService` получает книги и готовит направления `A_yes_B_no` и `A_no_B_yes`.
4. `ArbitrageCalculator` считает объём, profit и ROI.
5. `AlertManager` сохраняет `ArbOpportunity` и применяет dedupe.
6. `FanoutManager` подбирает Telegram-получателей по пользовательским фильтрам.
7. Telegram-бот отправляет алерт сразу или добирает queued/retry сообщения из БД.

## Режимы запуска

Параметр `APP_RUNTIME_MODE` определяет, какие фоновые процессы поднимаются внутри `arbitrage_bot.main:app`.

- `all` — worker + telegram
- `worker` — только worker
- `fanout` — только fanout loop
- `telegram` — только Telegram polling

`fanout` вынесен отдельно, а в `all` основная доставка алертов всё равно может происходить сразу из worker-пайплайна.

## Быстрый старт для разработки

1. Подготовьте файл окружения в `~/.config/arbivision/.env`.
2. Установите зависимости:

```bash
python3 -m pip install -r requirements.txt
```

3. Запустите проект:

```bash
python3 start.py
```

Что делает `start.py`:

- загружает `.env`
- запускает `docker compose up -d`
- ждёт готовности Postgres
- прогоняет `alembic upgrade head`
- стартует `uvicorn arbitrage_bot.main:app --reload`
- пишет PID в временный файл, чтобы `stop.py` мог остановить именно этот процесс

Остановка:

```bash
python3 stop.py
```

`stop.py` завершает только сохранённый PID, не пытаясь убивать посторонние `uvicorn`-процессы, а затем делает `docker compose stop`.

Опция `python3 stop.py --drop` удаляет контейнеры и volume базы данных. Это разрушительное действие, поэтому скрипт дополнительно спрашивает подтверждение.

## Альтернативные способы запуска

Только API без фоновых циклов:

```bash
uvicorn arbitrage_bot.api_app:app --reload
```

Только worker:

```bash
python3 -m arbitrage_bot.run_worker
```

Только fanout:

```bash
python3 -m arbitrage_bot.run_fanout
```

Только Telegram:

```bash
python3 -m arbitrage_bot.run_telegram
```

## Основные переменные окружения

### Инфраструктура

- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_DB`
- `POSTGRES_HOST`
- `POSTGRES_PORT`
- `REDIS_HOST`
- `REDIS_PORT`
- `REDIS_DB`

### Источники данных

- `POLYMARKET_ENABLED`
- `PREDICT_FUN_ENABLED`
- `PREDICT_FUN_API_KEY`

### Логика поиска и расчёта

- `MARKET_REFRESH_SECONDS`
- `MARKET_SYNC_INTERVAL_SECONDS`
- `MAX_MARKET_PAIRS_PER_LOOP`
- `EMPTY_ORDERBOOK_THRESHOLD`
- `ORDERBOOK_CACHE_TTL_SECONDS`
- `ORDERBOOK_CACHE_MAX_ITEMS`
- `ORDERBOOK_POLYMARKET_BATCH_SIZE`
- `ORDERBOOK_PREDICT_FUN_CONCURRENCY`
- `FEE_POLYMARKET_BPS`
- `FEE_PREDICT_FUN_BPS`

### Алерты и доставка

- `ALERTS_DEDUPE_TTL_SECONDS`
- `ALERTS_DELTA_PROFIT_THRESHOLD_USD`
- `ALERTS_DELTA_ROI_THRESHOLD_PERCENT`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_DEFAULT_CHAT_IDS`
- `TELEGRAM_SYSTEM_ERROR_CHAT_IDS`
- `TELEGRAM_ALERTS_POLL_SECONDS`
- `TELEGRAM_DELIVERY_RETRY_SECONDS`
- `TELEGRAM_DELIVERY_MAX_ATTEMPTS`
- `FANOUT_TARGET_CACHE_TTL_SECONDS`
- `TELEGRAM_SYSTEM_ERROR_COOLDOWN_SECONDS`
- `ANDREI_KURILOV_ID`

### API и рантайм

- `ADMIN_API_TOKEN`
- `APP_RUNTIME_MODE`
- `APP_HOST`
- `APP_PORT`

## Telegram-бот

Команда `/start` открывает главное меню. Бот поддерживает:

- паузу и возобновление алертов
- пользовательские фильтры через inline-кнопки
- ввод числовых значений следующим сообщением
- админский экран статистики для чатов, прошедших `_is_admin_chat(...)`

Кнопка `Stats` открывает сводку по пользователям, алертам и причинам дропа. Текст этого окна формируется в `arbitrage_bot/tg_bot/handlers.py` в функции `_format_admin_stats_text`.

## HTTP API

Приложение регистрирует роутер с префиксом `/api`.

Основные ручки:

- `GET /api/health`
- `GET /api/status`
- `GET /api/admin/pairs`
- `POST /api/admin/pairs/{pair_id}/approve`
- `GET /api/admin/pairs/{pair_id}/diagnose`
- `GET /api/admin/runtime-metrics`

Для админских ручек нужен заголовок `X-Admin-Token` со значением `ADMIN_API_TOKEN`.

## Тесты

Основные тесты лежат в директории `tests/`.

Запуск:

```bash
python3 -m unittest discover -s tests
```

Для точечного прогона, например Telegram-команд:

```bash
python3 -m unittest tests.test_tg_bot_commands -v
```

## Примечания

- `.env` загружается из `~/.config/arbivision/.env`
- `main.py` поднимает API и фоновые рантаймы через FastAPI lifespan
- `api_app.py` нужен, когда хочется запустить только HTTP API без worker и Telegram
- Redis используется для dedupe и служебных кешей
- проект уже содержит локализацию Telegram UI через `arbitrage_bot.tg_bot.localization`
