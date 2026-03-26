# Аналитический сервис логистики путешествия

Backend-каркас сервиса аналитики маршрутов на FastAPI.

## Технологии

- FastAPI
- SQLAlchemy 2.x с асинхронным доступом
- PostgreSQL и Redis в Docker-окружении разработки
- SQLite как fallback-вариант вне Docker
- Poetry для управления зависимостями
- Docker Compose для запуска инфраструктуры

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

## Структура проекта

```text
app/
  api/           HTTP-слой
  core/          конфигурация, логирование, БД, DI-контейнер
  models/        ORM-модели
  providers/     провайдеры источников маршрутов
  repositories/  слой доступа к данным
  services/      бизнес-логика
tests/           smoke-тесты wiring и служебных endpoint-ов
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

На этом этапе backend уже поддерживает автокомплит локаций, запуск поиска маршрутов, polling результатов, деталку маршрута и mock checkout-link.
Хранение search state в первой версии реализовано in-memory и не переживает рестарт процесса.
