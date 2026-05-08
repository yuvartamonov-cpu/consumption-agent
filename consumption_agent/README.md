# Consumption Agent — v2 (пересмотр после ревью)

## Изменения относительно v1

| Пункт | Было | Стало |
|-------|------|-------|
| **Формула веса** | Мультипликативная: Priority × Frequency × Urgency | Байесовская: P(need \| season, calendar, inventory, budget, profile) |
| **Ось Frequency** | Линейная шкала (ежедневно → редко) | Temporal Pattern: continuous / seasonal / sparse / event_driven / lifecycle / subscription |
| **Маштаб** | 8 осей, 10 таблиц, всё сразу | MVP: items + purchases + alerts + categories. Остальное добавляется по необходимости |
| **Roadmap** | 6 параллельных фаз | Итеративная: email → гарантии → ручной ввод → engine → цены → sync → network |
| **Privacy** | Упомянуто в README | Заложено в DDL: data_origin, consent_level, retention_days, auto_delete_at |
| **Memory Lane** | Одностадийная обработка | Двухстадийная: fast path (категория + embedding) + lazy enrichment по триггеру |
| **MCP/A2A** | Не упомянуто | Заложено в архитектуре (gateway adapter), не реализуется в MVP |
| **Гендер** | мужской/женский профиль | Кластеры образа жизни (профессия, дети, климат, жильё, хобби, ценности) |

## Документация

| Файл | О чём |
|------|-------|
| `01_architecture.md` | Архитектура: слои, принципы, MCP-ready, двухстадийная обработка |
| `02_needs_matrix.md` | Матрица потребностей: байесовская модель, 8 осей, temporal patterns, кластеры образа жизни |
| `03_database_schema.md` | PostgreSQL DDL: core (5 таблиц для MVP) + extended (5 на будущее), privacy-by-design |
| `04_roadmap.md` | Итеративный roadmap: фаза 0 → 6, checkpoint после каждой |
| `seed_categories.sql` | Начальная загрузка категорий |

## MVP за 2 недели

Самая узкая версия, которая будет полезна:

1. Email-парсер (Ozon, WB, Яндекс.Маркет) → распознавание покупок
2. Автоинвентарь: items + purchases
3. Уведомления по гарантиям и срокам годности
4. Telegram-бот: список вещей, алерты, `/list`, `/alerts`

Всё. Никакой матрицы, никакого Memory Lane, никаких 6 фаз. Если этот slice окажется полезным — наращиваем. Если нет — пересматриваем.
