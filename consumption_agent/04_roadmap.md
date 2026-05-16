# Roadmap реализации — consumption_agent

**Версия от 16.05.2026 · 04_roadmap.md (обновлено после seller-link search и IMAP folder scan)**
**Git baseline:** efff784

---

Ничего не строить, пока не проверено на предыдущем шаге. Каждая фаза — это *работающий slice*, который можно потрогать.

После принятия Architecture v2 дорожная карта переведена на структуру Sprint A→E.

---

## ✅ Phase A — Stabilisation (сейчас)

**Цель:** зафиксировать инфраструктуру, перенести код в управляемую структуру, восстановление после сбоев.

- [x] **GitHub repository** (remote настроен, push работает)
- [ ] `docs/` — структура каталога:
  - [ ] `architecture.md` (симлинк или копия 02_architecture_v2.md)
  - [ ] `security.md`
  - [ ] `agent_rules.md`
  - [ ] `development_workflow.md`
- [ ] `.env.example` (без секретов)
- [ ] `README.md` — общее описание проекта
- [x] **CLI** `consumption status|doctor|check-db|backup-now|restart-bot` (YUR-70, commit pending)
- [ ] **Backup script** (шифрованный `.backup` по расписанию)
- [x] **Tests baseline** — минимальный baseline давно есть, новые тесты добавляются по модулям
- [ ] **Pre-commit hook** для py_compile + secret scanning
- [ ] `docs/cli_workflow.md` — инструкция для CLI вместо Telegram

### Что уже есть

- ✅ Git в WSL, bare-репо на Windows
- ✅ GitHub remote синхронизируется с bare-репо на Windows
- ✅ .gitignore
- ✅ .env (секреты)
- ✅ systemd-юнит consumption-bot.service (autorestart)
- ✅ Ежедневный cron (10:10): import + enrichment + report
- ✅ IMAP: Gmail + Яндекс + 2×Mail.ru
- ✅ Telegram bot (@ConsumptionAgentBot)

---

## 🔄 Phase B — Memory Lane MVP + Visual Search (текущий спринт)

**Цель:** владелец отправляет фото с комментарием, бот сохраняет запись и извлекает признаки вкуса.

**Схема:**
```
Telegram photo + comment → сохранение в media/ → caption + feature extraction
→ memory_lane_items → liked/disliked/attributes → taste profile
```

### Acceptance criteria

- [x] **Автообработка фото в Telegram** — если бот получает фото с текстом «нравится»,
      «запомни», «найди похожее» и т.п., сохраняет в Memory Lane (commit YUR-64)
- [x] Сохранение оригинала в `data/media/` (sha256-deduped, MEDIA_SUBDIR)
- [x] Запись в `memory_lane_items` (liked_features, disliked_features, style_tags, topic)
- [x] Запись в `media_assets`
- [x] Команда `/ml_last` — последние впечатления (опц. фильтр по topic)
- [ ] Команда `/ml_find <query>` — текстовый поиск по памяти (следующая итерация: embedding)
- [ ] Команда `/ml_profile <topic>` — профиль вкуса по теме (следующая итерация)
- [x] **Тесты** на сохранение (9 кейсов в tests/test_memory_lane.py)
- [x] `ml_search_v2` — seller-link orchestrator с brand gating и canonicalization
- [x] Прямые seller links вместо Ozon-first retrieval
- [x] `AliExpress` / `Alibaba` — запросы переводятся с русского на английский
- [x] Official-site fallback через поисковые ссылки Google / Yandex
- [ ] Structured official/distributor resolution — не только generic search links
- [ ] CLIP visual gate для проверки визуального совпадения кандидатов
- [ ] Reverse image search provider
- [ ] Price-drop tracking для товаров из Memory Lane

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

## 🔧 Technical debt — Приоритет 1 (параллельно)

### 📸 Распознавание чеков — критично
**Проблема:** OCR через Tesseract выдаёт мусор на большинстве фото/скринов чеков. Названия товаров, суммы и состав позиций распознаются нестабильно. «Товар 2» вместо реального названия, мусорные строки в начале OCR.

**Что нужно:**
- [ ] **CEO: спроектировать скрипт устойчивого распознавания чеков** (чеков с фото, скриншотов приложений, PDF — любых форматов)
- [ ] Сценарий: фото/скриншот → стабильное извлечение названий товаров, цен, количества, итога, доставки
- [ ] Альтернативы Tesseract: EasyOCR, PaddleOCR, LLM API (GPT-4o) для структурного парсинга
- [ ] Детекция доставки: выделение стоимости доставки + отдельные item с `is_delivery=1`
- [ ] Post-processing: fuzzy-матчинг товаров с существующим инвентарём
- [ ] Unit-тесты на реальных чеках (коллекция проблемных примеров)

- [ ] Item-level парсинг для Ozon (_parse_ozon_items) — сейчас 12% items linked
- [ ] Item-level парсинг для Yandex.Market
- [x] Очистка recognized_items_log (1230 мусорных записей удалены, YUR-73)
- [ ] Удалить тестовую запись item id 843 (data_origin='telegram_tag')
- [ ] Сбор курсов валют на дату чека
- [ ] Алерты low_stock (требует заполнения remaining вручную)
- [x] Документировать mail-сканирование команд `/dayexp`, `/monthexp`, `/debts`, `/fines`
- [x] Сканирование IMAP-папок расширено: `INBOX` + `Spam/Junk/Спам` + `Receipts/чеки`
- [ ] Интеграционные тесты на обход нескольких IMAP-папок для команд отчётов и долгов
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
- С 16.05: IMAP-обход релевантных папок, а не только `INBOX`

---

## ⚙️ Инфраструктура

- ✅ Git: WSL → bare-репо на Windows (post-commit hook)
- ✅ CEO-агент Paperclip AI видит актуальный код
- ✅ .gitignore: бинарные артефакты, .venv, data/
- ✅ Gateway: allowInsecureAuth=false
- ✅ .env: ротирован Gmail app-password (10.05.2026)
- ✅ GitHub как второй remote

---

## 📅 План кодирования — ближайшие 5 дней

### День 1 — Stabilize mail scans and observability
- Закрыть хвосты после IMAP folder scan: единые логи по выбранным папкам, счётчики folders_scanned/messages_seen/messages_deduped.
- Добавить интеграционный тестовый контур для daily_cheque_scan.py, credit_monitor.py, scripts/fines_bot.py с mock IMAP LIST/SELECT/SEARCH.
- Acceptance: можно доказать тестом, что INBOX, Spam и Receipts реально участвуют в сканировании.

### День 2 — Official seller retrieval
- Усилить ml_providers.py: бренды → official site / distributor / authorized retailer entry points.
- Вынести отдельный resolver для official/distributor domains, чтобы не плодить generic search links.
- Acceptance: /ml_search по брендовой вещи показывает 2–4 точных seller entry points без мусора чужих брендов.

### День 3 — Foreign marketplace translation quality
- Расширить словарь и нормализацию для AliExpress / Alibaba: категория, материал, цвет, gender, fit, сезон.
- Добавить тесты на fashion/home/beauty/tech-запросы, чтобы в query не оставались русские хвосты.
- Acceptance: англоязычные search URLs строятся стабильно и читаемо по ключевым категориям.

### День 4 — Receipt OCR fallback
- Подключить Vision fallback для плохого OCR чеков: Tesseract остаётся fast path, Vision — только для low-confidence / noisy cases.
- Нормализовать разбор суммы, доставки и item-lines из photo/screenshot/PDF чеков.
- Acceptance: проблемные чеки перестают сваливаться в мусорный текст или "Товар 2".

### День 5 — User-facing workflows
- Либо price-drop alerts для Memory Lane, либо reminders/replacement workflow для inventory — в зависимости от результатов дней 2–4.
- Минимум: один завершённый пользовательский loop с Telegram-кнопкой и записью состояния в БД.
- Acceptance: у пользователя появляется новый законченный сценарий, а не только внутренние модули.

consumption_agent · git baseline: efff784 · обновлено 16.05.2026 · architecture v2 + seller-link search + IMAP folder scan
