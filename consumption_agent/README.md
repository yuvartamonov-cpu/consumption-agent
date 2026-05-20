# Consumption Agent

Персональный агент управления потреблением, расходами, активами и предпочтениями владельца.

> Purchase Memory + Inventory + Memory Lane + Needs Engine + Recommendation Engine + Permissioned Action Layer

## Возможности

**Сбор данных:**
- 📧 Импорт чеков из email (Gmail, Яндекс, Mail.ru)
- 🧾 OCR + QR-распознавание чеков по фото (Telegram /add_tag)
- 📸 Парсинг скриншотов заказов (каршеринг, маркетплейсы)
- 💬 Ручной ввод через Telegram
- 💳 SMS-мониторинг (Microsoft Phone Link)

**Инвентарь:**
- 📋 Что есть (498 активных товаров)
- ✅ Гарантии и сроки годности (авторасчёт + уведомления)
- 📊 Категории (34 шт., иерархическое дерево)

**Memory Lane (в разработке):**
- 🖼️ Визуальная память вкусов и предпочтений
- 🏷️ Извлечение liked/disliked признаков
- 🔍 Поиск похожих объектов

**Каршеринг:**
- 🚗 39 поездок по 4 провайдерам
- 📋 /last_drives — история
- 📐 /find_car — сравнение тарифов

**Кредитный мониторинг:**
- 💳 Банки (Сбер, Совком, ВТБ, Тинькофф, Альфа)
- 🏢 МФО (автоопределение)
- ⏰ Уведомления за 3+ дня до платежа

## Быстрый старт

```bash
# Зависимости
pip install -e .

# Настройка
cp .env.example .env
# отредактировать .env

# Telegram бот
systemctl --user start consumption-bot.service

# Импорт чеков
python consumption/__init__.py import --max 20

# CLI (в разработке)
consumption status
consumption check-db
```

## Архитектура

Система состоит из 6 слоёв:

1. **Input Channels** — Telegram, IMAP, SMS, скриншоты
2. **Ingestion & Normalization** — OCR, QR, парсинг, дедупликация
3. **Memory Core** — покупки, инвентарь, впечатления (Memory Lane)
4. **Reasoning & Decision** — гарантии, бюджет, потребности, рекомендации
5. **Governance & Permissions** — approvals, лимиты, audit
6. **Action Layer** — черновики заказов (dry-run до Phase E)

Подробнее: [docs/architecture.md](02_architecture_v2.md)

## Статус

| Спринт | Статус |
|--------|--------|
| Phase A — Stabilisation | 🔄 в процессе |
| Phase B — Memory Lane | ⬜ запланировано |
| Phase C — Governance | ⬜ запланировано |
| Phase D — Recommendations | ⬜ запланировано |
| Phase E — Controlled actions | ⬜ будущее |

Подробнее: [04_roadmap.md](04_roadmap.md)

## Принципы

- **Human-in-the-loop** — никаких трат без подтверждения
- **Privacy-by-design** — данные остаются локально
- **Explainability** — каждая рекомендация обоснована
- **Telegram — интерфейс, не ядро** — CLI как fallback

## Технологии

- **Ядро:** Python 3.11+
- **БД:** SQLite (WAL)
- **Бот:** python-telegram-bot
- **OCR:** Tesseract + pyzbar (QR)
- **Fuzzy match:** rapidfuzz
- **Отчёты:** fpdf2 (PDF)
- **Инфра:** systemd, OpenClaw

## Безопасность

- Telegram whitelist (один chat_id)
- App-пароли для IMAP, не основные
- `.env` в gitignore
- Privacy router для LLM (local/cloud/anonymous)
- Governance слой — action_proposals до выполнения

Подробнее: [docs/security.md](docs/security.md)

---

*consumption_agent · git: 6d39542 · architecture v2 (11.05.2026)*
