# Аналитический сервис логистики путешествия

FastAPI backend для поиска маршрутов, автокомплита локаций, выдачи деталей маршрута и mock checkout-link.

Сервис умеет искать маршруты в нескольких источниках, постепенно публиковать результаты для polling-фронта и кешировать завершённые поиски в Redis.

## Технологии

- Python 3.12+
- FastAPI
- SQLAlchemy 2.x с асинхронным доступом
- SQLite для локального запуска по умолчанию
- PostgreSQL и Redis в Docker Compose
- Redis как optional cache завершённых поисков
- Poetry для управления зависимостями
- Ruff, mypy, pytest для проверок

## Docker Compose

Для единообразного окружения разработки сервис можно запускать через Docker Compose вместе с PostgreSQL и Redis.

Dev-режим с hot reload:

```bash
docker compose --profile dev up --build
```

Production-like профиль без bind mount и без `--reload`:

```bash
docker compose --profile prod up --build
```

При запуске через Compose наружу публикуется только backend:

- API: `http://127.0.0.1:8000`
- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

Внутри Docker backend автоматически использует PostgreSQL и Redis через переменные окружения из `docker-compose.yml`, даже если в локальном `.env` `PDAXENIX_REDIS_URL` оставлен пустым.
Если порт `8000` уже занят локальным процессом, измените `PDAXENIX_BACKEND_PORT` в `.env`, например на `8001`.

## Сценарий работы через Docker

Рекомендуемый dev-flow через Docker Compose:

1. Создать `.env` из шаблона:

   ```bash
   cp .env.example .env
   ```

2. Поднять backend, PostgreSQL и Redis:

   ```bash
   docker compose --profile dev up -d --build
   ```

3. Заполнить PostgreSQL мок-данными внутри backend-контейнера:

   ```bash
   docker compose exec backend poetry run python -m app.scripts.seed_mock_data
   ```

   Для воспроизводимого demo-сценария можно зафиксировать дату:

   ```bash
   docker compose exec backend poetry run python -m app.scripts.seed_mock_data --base-date 2026-04-14
   ```

4. Открыть Swagger и проверить API:

   - `http://127.0.0.1:8000/docs`
   - `GET /api/locations?prefix=Мос`
   - `GET /api/locations?prefix=Санкт`
   - `POST /api/searches`

5. Посмотреть логи при необходимости:

   ```bash
   docker compose logs -f backend
   ```

6. Остановить окружение:

   ```bash
   docker compose --profile dev down
   ```

Важно: seed-команду для Docker нужно запускать именно через `docker compose exec backend ...`, иначе при запуске с хоста она возьмёт локальный `.env` и может писать в SQLite вместо docker PostgreSQL.
Команда пересоздаёт таблицы `locations`, `carriers` и `route_segments`, поэтому она подходит для dev/demo-окружения, но не для сохранения пользовательских данных.

## Сценарий для фронтенда

Этот сценарий нужен в первую очередь для ручной интеграции фронтенда с backend, а не для backend unit/integration tests.

Рекомендуемый flow:

1. Поднять окружение:

   ```bash
   cp .env.example .env
   docker compose --profile dev up -d --build
   ```

2. Заполнить PostgreSQL фиксированным набором мок-данных:

   ```bash
   docker compose exec backend poetry run python -m app.scripts.seed_mock_data --base-date 2026-04-14
   ```

3. Подключить фронтенд к backend по адресу:

   - API: `http://127.0.0.1:8000`
   - Swagger: `http://127.0.0.1:8000/docs`

4. Проверить пользовательский flow:

   - в поле "Откуда" ввести `Мос`
   - в поле "Куда" ввести `Санкт` или `Каз`
   - выбрать дату `2026-04-14`
   - нажать "Найти"
   - дождаться polling результатов
   - открыть деталку маршрута
   - нажать кнопку перехода / покупки

Для UI-проверки под seeded данными подходят сценарии:

- `Москва -> Санкт-Петербург`, дата `2026-04-14`
- `Москва -> Казань`, дата `2026-04-14`
- `Таганрог -> Москва`, дата `2026-04-14`

Что фронтенд должен увидеть:

- автокомплит для `GET /api/locations`
- после `POST /api/searches` состояние loading/skeleton и пустой список
- затем непустой ответ из `GET /api/searches/{search_id}/results`
- рабочие сортировки и фильтры через повторный запрос к `/results`
- деталку через `GET /api/routes/{route_id}`
- mock checkout-link через `POST /api/routes/{route_id}/checkout-link`

## Структура проекта

```text
app/
  adapters/      адаптеры внешних и внутренних источников маршрутов
  api/           HTTP слой, схемы запросов, сериализация
  clients/       HTTP client factories
  core/          конфиг, БД, Redis, DI container, logging
  models/        SQLAlchemy ORM модели
  repositories/  доступ к данным
  schemas/       Pydantic response/request schemas
  scripts/       seed/import/load-test scripts
  services/      use cases, search logic, store, cache
  utils/         общие утилиты
data/            подготовленные справочники локаций
data_pipelines/  notebooks/pipelines для подготовки данных
tests/           unit и integration tests
```

## Локальный запуск

1. Склонировать репозиторий:

   ```bash
   git clone https://github.com/PD-Axenix-2025-2026/Backend.git
   cd Backend
   ```

2. Установить зависимости:

   ```bash
   poetry install
   ```

3. Создать локальный файл окружения на основе шаблона:

   ```bash
   cp .env.example .env
   ```

4. При необходимости изменить значения в `.env`. Вне Docker по умолчанию используется локальный файл SQLite `pdaxenix.db`, а `PDAXENIX_REDIS_URL` можно оставить пустым: Redis для обычного локального запуска не требуется. Для Docker Compose подключения к PostgreSQL и Redis переопределяются в `docker-compose.yml`.
   Для управления уровнем логирования можно использовать `PDAXENIX_LOG_LEVEL`, например `INFO` или `DEBUG`.

5. Запустить приложение:

   ```bash
   poetry run uvicorn app.main:app --reload
   ```

Приложение автоматически читает переменные из `.env` через `pydantic-settings`.

## Заполнение БД мок-данными

Для локальной разработки и интеграции с фронтендом можно заполнить базу детерминированным набором мок-данных:

```bash
poetry run python -m app.scripts.seed_mock_data
```

Если backend запущен через Docker Compose, используйте команду внутри контейнера:

```bash
docker compose exec backend poetry run python -m app.scripts.seed_mock_data
```

Команда пересоздаёт `locations`, `carriers` и `route_segments`, а затем заново создаёт один и тот же набор данных.
Это позволяет синхронизировать docker PostgreSQL с текущей ORM-схемой даже после изменения моделей.
По умолчанию сегменты создаются на даты:

- сегодня
- завтра
- через 3 дня
- через 7 дней

Для воспроизводимого demo-сценария можно зафиксировать базовую дату:

```bash
poetry run python -m app.scripts.seed_mock_data --base-date 2026-04-14
```

После заполнения базы можно сразу проверять пользовательские сценарии:

- `GET /api/locations?prefix=Мос`
- `GET /api/locations?prefix=Санкт`
- `POST /api/searches` для маршрутов `Москва -> Санкт-Петербург`
- `POST /api/searches` для маршрутов `Москва -> Казань`
- `POST /api/searches` для маршрутов `Таганрог -> Москва`

В мок-данных есть города, аэропорты, железнодорожные вокзалы, автовокзал, несколько перевозчиков и прямые сегменты для `plane`, `train` и `bus`.

## Заполнение БД нагрузочными данными

Для нагрузочного тестирования можно отдельно сгенерировать 50 000 сегментов маршрутов, не трогая таблицы справочников:

```bash
poetry run python -m app.scripts.seed_load_test_data
```

По умолчанию скрипт:

- стартует с даты `2026-05-22`
- распределяет данные на 30 дней
- создаёт 50 000 строк в `route_segments`
- генерирует и прямые сегменты, и двухсегментные маршруты с пересадкой

Если нужно, параметры можно переопределить:

```bash
poetry run python -m app.scripts.seed_load_test_data --base-date 2026-05-22 --days 45 --target-segments 60000
```

По умолчанию скрипт заменяет только содержимое `route_segments`. Если нужен дописывающий режим, используйте `--append`.

## Нагрузочный тест API

Чтобы проверить сайт под нагрузкой, сначала заполните БД нагрузочными данными, а затем запустите HTTP load test:

```bash
poetry run python -m app.scripts.run_load_test --base-url http://127.0.0.1:8000 --users 20 --iterations-per-user 10
```

Сценарий имитирует пользовательский flow:

- автокомплит локаций
- создание поиска
- опрос результатов поиска
- открытие деталки маршрута
- опционально создание checkout-link

Полезно для первого прогона увеличить число сценариев постепенно:

```bash
poetry run python -m app.scripts.run_load_test --users 5 --iterations-per-user 5
poetry run python -m app.scripts.run_load_test --users 20 --iterations-per-user 20
```

Что смотреть в результате:

- среднее время ответа по каждой ручке
- p95 latency
- число ошибок
- количество успешных сценариев

Если нужен настоящий стресс-тест, увеличивайте `--users` и `--iterations-per-user` до момента, когда упрётесь в CPU, БД или рост ошибок.

## Настройка основного сценария работы. Работа с внешними API (РЖД и Яндекс.Расписания)

Чтобы сервис мог запрашивать и обрабатывать маршруты из внешних API требуется:

1. Записать в поле `PDAXENIX_YANDEX_RASP_API_KEY` в .env файле api ключ Яндекс.Расписаний.
Также можно задать флаги `PDAXENIX_USE_RZD_API` и `PDAXENIX_USE_YANDEX_API` для включения/отключения внешних запросов по отдельности.
По умолчанию оба флага взведены.

2. Запустить скрипт заполнения таблицы locations информацией о локациях, взятой из внешних API:

```bash
poetry run python -m app.scripts.import_rzd_and_yandex_locations --rzd-file data\rzd_locations.json \
      --yandex-file data\yandex_rasp_locations.json \
      --matches-file data\rzd_yandex_location_matches.json \
      --import-db
```

При необходимости также можно использовать скрипты import_rzd_locations.py и import_yandex_locations.py
для заполнения базы данных локациями только из одного источника.

## Проверка качества кода

Для локальных проверок используются `Ruff` и `mypy`.

- Форматирование: `poetry run ruff format app tests`
- Проверка форматирования: `poetry run ruff format --check app tests`
- Линтинг: `poetry run ruff check app tests`
- Проверка типов: `poetry run mypy app tests`

## Логирование

Приложение пишет plain-text логи через стандартный `logging` и автоматически добавляет в них:

- `request_id`
- `search_id`
- `route_id`

Для HTTP-запросов backend принимает входящий заголовок `X-Request-ID` или генерирует его сам и возвращает обратно в ответе.
Уровень логирования настраивается через `PDAXENIX_LOG_LEVEL`.

## CI

В GitHub Actions настроен workflow `CI`. Он запускается на `push` и `pull_request` в ветку `main` и автоматически выполняет:

- `poetry run ruff format --check app tests`
- `poetry run ruff check app tests`
- `poetry run mypy app tests`
- `poetry run pytest -q`
- `docker compose --profile dev config`
- `docker compose --profile dev build backend`

Для Docker job в CI используется `.env.example`: перед проверкой compose workflow создаёт временный `.env` на его основе.

## Доступные endpoint-ы

- `GET /api/health`
- `GET /api/ready`
- `GET /api/locations`
- `POST /api/searches`
- `GET /api/searches/{search_id}/results`
- `GET /api/routes/{route_id}`
- `POST /api/routes/{route_id}/checkout-link`

## Swagger / OpenAPI

После запуска приложения документация API доступна по адресам:

- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`
- OpenAPI schema: `http://127.0.0.1:8000/openapi.json`

## Текущее состояние

Backend поддерживает:

- autocomplete локаций;
- создание search job;
- progressive polling результатов;
- дедупликацию маршрутов между источниками;
- Redis cache завершённых поисков;
- route detail endpoint;
- mock checkout-link.

Ограничения:

- live search state хранится in-memory и не переживает рестарт backend-процесса;
- Redis cache хранит только завершённые результаты, а не активные partial searches;
- Docker Compose Redis настроен без persistence.
