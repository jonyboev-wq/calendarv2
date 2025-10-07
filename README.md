# CalendarV2 Planner

Проект предоставляет планировщик с веб-интерфейсом и API вокруг оптимизатора из `src/scheduler`.

## Структура

- `src/api/` — FastAPI-приложение, которое управляет событиями, гибкими окнами и взаимодействием с оптимизатором.
- `frontend/` — React + Vite интерфейс с формами добавления задач, настройками гибкости и визуализацией свободных слотов.
- `frontend/tests/` — e2e тесты на Playwright для ключевых пользовательских сценариев.

## Запуск бэкенда

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn src.api.main:app --reload
```

API поднимается на `http://localhost:8000`. Основные эндпоинты:

- `GET /api/schedule` — текущее расписание, свободные окна и настройки дня.
- `POST /api/events` — добавление фиксированных и гибких событий.
- `PUT /api/events/{event_id}` — обновление событий.
- `DELETE /api/events/{event_id}` — удаление события.
- `POST /api/events/{event_id}/complete` — завершение события с пересчётом расписания.
- `PUT /api/settings` — изменение границ рабочего дня.
- `POST /api/proposals` — подбор рекомендуемых временных интервалов.

## Запуск фронтенда

```bash
cd frontend
npm install
npm run dev
```

Vite проксирует запросы `/api/*` на `http://localhost:8000`, поэтому UI сразу общается с запущенным бэкендом. Готовая сборка собирается командой `npm run build`, предпросмотр — `npm run preview`.

## Тесты

End-to-end сценарии находятся в `frontend/tests/e2e.spec.ts` и моделируют ключевой поток создания гибкого события. Запуск тестов:

```bash
cd frontend
npm install
npx playwright install --with-deps
npm test
```

Тесты используют роутинг Playwright, чтобы изолированно проверить UI без реального бэкенда.

## Дополнительно

- CORS не требуется — прокси на уровне Vite маршрутизирует запросы во время разработки.
- В рабочей среде фронтенд может обращаться к API напрямую по `/api`, если настроить reverse proxy (например, Nginx) или включить CORS в FastAPI.
