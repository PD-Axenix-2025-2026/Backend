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

5. Запустить приложение:

   ```bash
   poetry run uvicorn app.main:app --reload
   ```

Приложение автоматически читает переменные из `.env` через `pydantic-settings`.

## Доступные endpoint-ы

- `GET /api/health`
- `GET /api/ready`

## Swagger / OpenAPI

После запуска приложения документация API доступна по адресам:

- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`
- OpenAPI schema: `http://127.0.0.1:8000/openapi.json`

## Текущее состояние

На этом этапе подготовлен архитектурный каркас backend-сервиса и внутренние контракты для будущего поиска маршрутов.
Публичные endpoint-ы поиска маршрутов пока намеренно не реализованы.
