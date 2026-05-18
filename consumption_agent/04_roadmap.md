# Roadmap реализации — consumption_agent

**Версия от 11.05.2026 · 04_roadmap.md (обновлено после принятия Architecture v2)**
**Git:** 70839df (02_architecture_v2.md)

---

Ничего не строить, пока не проверено на предыдущем шаге. Каждая фаза — это *работающий slice*, который можно потрогать.

После принятия Architecture v2 дорожная карта переведена на структуру Sprint A→E.

---

## ✅ Phase A — Stabilisation (сейчас)

**Цель:** зафиксировать инфраструктуру, перенести код в управляемую структуру, восстановление после сбоев.

- [ ] **GitHub repository** (создать remote, пушить)
- [ ] `docs/` — структура каталога:
  - [ ] `architecture.md` (симлинк или копия 02_architecture_v2.md)
  - [ ] `security.md`
  - [ ] `agent_rules.md`
  - [ ] `development_workflow.md`
- [ ] `.env.example` (без секретов)
- [ ] `README.md` — общее описание проекта
- [ ] **CLI** `consumption status|doctor|check-db|backup-now|restart-bot`
- [ ] **Backup script** (шифрованный `.backup` по расписанию)
- [ ] **Tests baseline** — хотя бы минимальные тесты на существующую логику
- [ ] **Pre-commit hook** для py_compile + secret scanning
- [ ] `docs/cli_workflow.md` — инструкция для CLI вместо Telegram

### Что уже есть

- ✅ Git в WSL, bare-репо на Windows
- ✅ .gitignore
- ✅ .env (секреты)
- ✅ systemd-юнит consumption-bot.service (autorestart)
- ✅ Ежедневный cron (10:10): import + enrichment + report
- ✅ IMAP: Gmail + Яндекс + 2×Mail.ru
- ✅ Telegram bot (@ConsumptionAgentBot)

---

## 🔄 Phase B — Memory Lane MVP (текущий спринт)

**Цель:** владелец отправляет фото с комментарием, бот сохраняет запись и извлекает признаки вкуса.

**Схема:**
```
Telegram photo + comment → сохранение в media/ → caption + feature extraction
→ memory_lane_items → liked/disliked/attributes → taste profile
```

### Acceptance criteria

- [ ] **Автообработка фото в Telegram** — если бот получает фото с текстом «нравится»,
      «запомни», «найди похожее» и т.п., предлагает сохранить в Memory Lane
- [ ] Сохранение оригинала в `data/media/`
- [ ] Запись в `memory_lane_items` (liked_features, disliked_features, style_tags)
- [ ] Запись в `media_assets`
- [ ] Команда `/ml_last` — последние впечатления
- [ ] Команда `/ml_find <query>` — текстовый поиск по памяти
- [ ] Команда `/ml_profile <topic>` — профиль вкуса по теме
- [ ] **Тесты** на сохранение, извлечение, поиск

### Детали

- **Fast path:** caption + comment parsing → сохранение сразу
- **Lazy enrichment:** глубокий анализ (embedding, поиск аналогов) — по расписанию
- **Privacy:** по умолчанию local, cloud — только после явного согласия

---

## ⬜ Phase C — Governance MVP

**Цель:** любые будущие действия сначала превращаются в `action_proposal`. Агент не совершает значимых действий без подтверждения.

- [ ] Таблица `action_proposals` (proposal_type, risk_level, status, evidence)
- [ ] Таблица `approvals` (proposal_id, approval_channel, confirmation_hash)
- [ ] Таблица `audit_events` (event_type, actor_type, input/output_hash)
- [ ] **Policy engine** — risk levels (low/medium/high/critical)
- [ ] **Spending limits** — лимиты по сумме
- [ ] **Dry-run executor** — подготовка действия без выполнения
- [ ] **Telegram-кнопки:** Approve / Reject / Explain / Show alternatives

---

## ⬜ Phase D — Needs + Recommendation MVP

**Цель:** агент предлагает одну практическую рекомендацию на основе покупок и Memory Lane.

- [ ] Выбрать 1–2 категории для прогноза (кофе, корм, мебель для кабинета)
- [ ] **Recurring need detection** — анализ регулярности покупок
- [ ] **Explanation template** — каждая рекомендация должна отвечать:
  - что, почему сейчас, на каких данных, альтернативы, риски, безопасность
- [ ] **Recommendation scoring** — confidence + evidence + constraints
- [ ] **Proposal generation** — агент готовит `action_proposal` из рекомендации
- [ ] НЕ выполнять внешнее действие без подтверждения

---

## ⬜ Phase E — Controlled external actions

**Цель:** только после стабильного Governance и успешного MVP рекомендаций.

- [ ] **Только draft orders** — подготовка заказа, но не отправка
- [ ] **No payment automation** — оплата только руками владельца
- [ ] **Explicit owner confirmation** — каждое действие через approvals
- [ ] **Rollback/undo** — отмена подготовленного действия
- [ ] **Мониторинг ошибок** — логи всех API-вызовов

---

## ⬜ Phase F — Monetization & Growth

**Статус:** Planned  
**Горизонт:** Q3 2026 → Q3 2027

### F.0 — Multi-user фундамент (блокер для всех каналов)

**Acceptance criteria:**
- [ ] Все личные данные (email, DB_PATH) вынесены в config/env
- [ ] Таблица `users` добавлена в БД (user_id, telegram_id, subscription_tier, consent_flags, created_at)
- [ ] user_id добавлен как foreign key во все основные таблицы
- [ ] Auth через Telegram (telegram_id как primary auth)
- [ ] Hosted деплой на Railway или VPS (не WSL на личной машине)

### F.1 — Канал 5: Premium B2C (Q3-Q4 2026)

**Acceptance criteria:**
- [ ] Freemium лимит: 50 товаров на бесплатном тарифе
- [ ] /subscribe команда в боте с описанием Premium
- [ ] Price-drop alerts только для Premium
- [ ] Расширенная аналитика только для Premium
- [ ] Платёжная интеграция (ЮKassa или Telegram Stars)

**Метрика:** 50 платных пользователей через 3 мес после запуска

### F.2 — Growth Loop: реферальная программа (Q4 2026)

**Acceptance criteria:**
- [ ] /invite команда генерирует уникальную реферальную ссылку
- [ ] При активации по ссылке: оба получают +30 дней Premium
- [ ] Retention hooks: weekly digest, price-drop alerts, milestones
- [ ] Savings counter в еженедельном отчёте

**Метрика:** Referral rate > 15% активных пользователей

### F.3 — Канал 1: Data → Скидки (Q1 2027)

**Acceptance criteria:**
- [ ] /privacy команда с настройками согласия
- [ ] Consent flow перед включением "Режима скидок"
- [ ] Anonymization layer (убрать PII)
- [ ] API endpoint для брендов-партнёров
- [ ] Минимум 1 бренд-партнёр в пилоте

### F.4 — Канал 2: Lifecycle CPA (Q1-Q2 2027)

**Acceptance criteria:**
- [ ] Справочник сроков службы по 20+ категориям
- [ ] Lifecycle-триггеры в боте с опциональными предложениями
- [ ] Partner tracking (UTM)
- [ ] CTR lifecycle-триггера > 10%

### F.5 — Канал 6: P2P между агентами (Q3 2027)

**Acceptance criteria:**
- [ ] /sell команда: отметить товар как "готов продать"
- [ ] Анонимный матчинг между агентами сети
- [ ] Комиссия агента: 2-5% от сделки
- [ ] N > 1000 активных пользователей в сети

**См. детали:** [05_monetization.md](05_monetization.md)

---

## 🔧 Technical debt — Приоритет 1 (параллельно)

- [ ] Item-level парсинг для Ozon (_parse_ozon_items) — сейчас 12% items linked
- [ ] Item-level парсинг для Yandex.Market
- [ ] Очистка recognized_items_log (1230 мусорных записей)
- [ ] Удалить тестовую запись item id 843 (data_origin='telegram_tag')
- [ ] Сбор курсов валют на дату чека
- [ ] Алерты low_stock (требует заполнения remaining вручную)
- [ ] Документировать credit_monitor / sms_monitor

---

## ✅ Что уже сделано (до принятия v2)

### Фаза 0 — Email-парсер → автоинвентарь ✅
- БД SQLite (WAL): items (498 active), purchases (1266), categories (34), alerts, cheques_log (1178), recognized_items_log (2428)
- Telegram-бот: все команды
- OCR (Tesseract), QR (pyzbar), fuzzy match (rapidfuzz 70%)
- Импорт Ozon-чеков из PDF
- IMAP: Gmail + Яндекс + 2×Mail.ru
- FINANCIAL_SENDERS: 19 отправителей

### Фаза 1 — Гарантии и напоминания ✅ (10.05.2026)
- warranty_until в items (авторасчёт)
- warranty_check.py (30/7 дней)
- /warranties, /set_warranty
- Ежедневные уведомления (09:00)

### Фаза 2 — Яндекс-экосистема 🔄
- ✅ Каршеринг: 39 поездок, /last_drives, /find_car
- ✅ Массовый импорт (Gmail: +186 purchases 10.05)
- ⬜ Яндекс.Почта — полный мониторинг
- ⬜ Яндекс Еда / Лавка — парсинг блюд
- ⬜ Дедупликация (сумма + дата + источник)
- ⬜ Госуслуги (7 писем, нужен парсинг)

### Кредитный мониторинг ✅ (experimental, 11.05.2026)
- credit_monitor.py, sms_monitor.py, credit_alerts.py
- Gmail + Яндекс + 2×Mail.ru + SMS (Phone Link)
- Cron: 10:00 + 18:00

---

## ⚙️ Инфраструктура

- ✅ Git: WSL → bare-репо на Windows (post-commit hook)
- ✅ CEO-агент Paperclip AI видит актуальный код
- ✅ .gitignore: бинарные артефакты, .venv, data/
- ✅ Gateway: allowInsecureAuth=false
- ✅ .env: ротирован Gmail app-password (10.05.2026)
- ⬜ GitHub как второй remote

---

consumption_agent · git: 70839df · обновлено 11.05.2026 · architecture v2
