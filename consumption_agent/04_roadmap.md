Roadmap реализации — consumption_agent

*Версия от 11.05.2026 · 04_roadmap.md (обновлено CEO после ревью коммитов a2e5246 + e2922a9 + 6424748)*

Ничего не строить, пока не проверено на предыдущем шаге. Каждая фаза —
это *работающий slice*, который можно потрогать. Если на фазе гипотеза не
подтвердилась — stopship, пересмотр.

------------------------------
✅ Фаза 0 — Email-парсер → автоинвентарь (MVP) — ВЫПОЛНЕНА

*Гипотеза:* Парсинг чеков из писем маркетплейсов даёт достаточно данных для
полезного инвентаря с гарантиями.

*Выполнено:*

   - ✅ БД SQLite: items (196 active), purchases (1266), categories (34),
     alerts, cheques_log (1178), recognized_items_log
   - ✅ Telegram-бот: /list, /alerts, /check (PDF), /add, /add_photo,
     /warranties, /last_drives, /find_car, /help
   - ✅ Systemd-сервис consumption-bot.service (autorestart)
   - ✅ Обработка чеков: QR (Ozon, ФНС), OCR (Tesseract), категоризация
   - ✅ Обработка бирок одежды: бренд, артикул, размер, цена,
     валюта ЦБ РФ, поиск картинок
   - ✅ Массовый fuzzy-match (rapidfuzz, порог 70, +86 совпадений)
   - ✅ Импорт чеков Ozon из PDF-писем
   - ✅ Ежедневный cron (10:10): import + enrichment + report + warranty_check
   - ✅ IMAP Gmail + Яндекс.Почта + mail.ru (zorea2001, neutrinon)
   - ✅ FINANCIAL_SENDERS: 19 отправителей (ozon, yandex_market, ofd_yandex,
     taxcom, belkacar, yandex_taxi, afisha_yandex, rusconcert, xero_invoice,
     google_play, element14, pult, yandex_lavka, yandex_eda, samokat,
     samokat_retail, samokat_ofd, yandex_drive_gmail, gosuslugi/nalog)
   - ✅ БД в WAL-режиме (нет блокировок)
   - ✅ Модульная структура: consumption/db.py, consumption/categorize.py,
     warranty_check.py

------------------------------
✅ Фаза 1 — Гарантии и напоминания — ВЫПОЛНЕНА (10.05.2026)

*Гипотеза подтверждена:* Уведомления об истекающих гарантиях — полезный
use case.

   - ✅ Колонка warranty_until в items (автоматический расчёт из
     purchase_date + warranty_months, calc_warranty_until через
     calendar.monthrange)
   - ✅ 12 товаров с рассчитанными гарантиями (все активны)
   - ✅ warranty_check.py — модуль проверки гарантий и сроков годности
   - ✅ Генерация alerts: гарантия (30 дней), срок годности (7 дней)
   - ✅ Ежедневная проверка в cron (daily_run.sh → warranty_check.py --notify)
   - ✅ Проактивные уведомления в Telegram (run_daily_alert_job 09:00,
     UTC-aware фильтр после фикса YUR-45)
   - ✅ /warranties — отдельная команда в Telegram-боте
   - ✅ /warranties добавлен в /help и setMyCommands
   - ✅ Дедупликация алертов в БД
   - ✅ /set_warranty <id> <months> — ручная установка (с recompute существующего
     warranty_until после фикса YUR-45)
   - ⬜ low_stock — алерты для расходников (нет данных в БД,
     требует ручного заполнения remaining)

*Закрытые задачи:* YUR-40 (консолидация Phase 1), YUR-45 (баги tz + stale until),
YUR-47 (приёмка)

------------------------------
🔄 Фаза 2 — Яндекс-экосистема: сбор данных — В ПРОЦЕССЕ

2.1 Мониторинг почты HKID2021@yandex.ru

   - ⬜ Пересылка Яндекс.Почты → Gmail
   - ⬜ Автоматический мониторинг входящих
   - ⬜ Парсинг писем от всех Яндекс-сервисов

2.2 Яндекс Еда / Лавка

   - ✅ Импортированы чеки Яндекс Еды и Лавки (см. итоги 2.4)
   - ⬜ Полный парсинг истории заказов (блюда/рестораны/суммы → items)
   - ⬜ Парсинг заказов Лавки (продукты → items)

2.3 Каршеринг — ✅ ОСНОВНОЙ СЛАЙС ВЫПОЛНЕН (11.05.2026, коммит a2e5246)

   - ✅ Обработаны скриншоты и почта (4 провайдера: Яндекс Драйв, BelkaCar,
     CityDrive, ещё один)
   - ✅ 39 поездок в БД (carsharing_trips)
   - ✅ /last_drives — история поездок (бывший /find_car)
   - ✅ /find_car — сравнение тарифов с параметрами (время + км)
   - ✅ Fixed Yandex Drive cost calc (flat daily, не hourly)
   - ⬜ Парсинг маршрутов (откуда → куда) — задел на Phase 3
   - ⬜ Профиль предпочтений по типу авто

2.4 Массовый импорт — ✅ ЧАСТИЧНО ВЫПОЛНЕН (10–11.05.2026)

   - ✅ Gmail: import --all-senders --max 100 (10.05 поздно вечером,
     +186 новых purchases в openclaw production DB)
   - ✅ Фикс UnboundLocalError в cmd_import (коммит 0bc325a)
   - ✅ Фикс SyntaxError в IMAP_CONFIG dict-literal в 3 файлах после
     e2922a9 (коммит 6424748)
   - ⬜ Yandex mailbox: --mailbox yandex --all-senders --max 100
   - ⬜ mail.ru ящики (zorea, neutrinon) — разведка, потом массовый
   - ⬜ Импорт Ozon-чеков за год (cmd_parse для PDF)

2.5 Дедупликация данных

*Проблема:* Один заказ может быть загружен двумя способами:
1. Через /add_photo (скриншот)
2. Через импорт с почты

   - ⬜ Дедупликация при импорте: проверка (sum, date, source) перед INSERT
   - ⬜ Связь скриншота с чеком: linked_purchase_id в items
   - ⬜ Обобщённый механизм для всех источников
   - ⬜ UI: пометка "дубль" в /list и отчётах

2.6 Госуслуги

*Что импортировано:* 7 писем (4 zorea, 3 neutrinon)

   - ⬜ doc_type='gov' с кастомным парсингом
   - ⬜ Распознавание типа услуги (налог, штраф, выписка, запись)
   - ⬜ Алерты: сроки уплаты налогов, штрафов

2.7 Яндекс Драйв: полная история поездок

   - ⬜ Парсинг HTML-чеков Яндекс Драйв (формат отличается от Самоката)
   - ⬜ Связь поездок с маршрутами (откуда → куда)

------------------------------
🆕 Фаза 2.8 — Кредитный мониторинг (новая, коммит a2e5246)

*CMO внёс новую вертикаль в production без предварительного roadmap-апруфа.
По состоянию на 11.05 принято решение АКЦЕПТОВАТЬ как experimental slice
и формализовать через эту секцию.*

*Гипотеза:* Мониторинг почты+SMS на сообщения от банков/МФО о предстоящих
платежах позволяет проактивно предупреждать пользователя за 3+ дня.

*Что сделано:*

   - ✅ credit_monitor.py — парсинг IMAP (Gmail + Yandex) на сообщения банков/МФО
   - ✅ sms_monitor.py — чтение Microsoft Phone Link SQLite (Windows)
   - ✅ credit_alerts.py — генерация Telegram-уведомлений
   - ✅ check_credit_alerts.sh — обёртка для cron (10:00 + 18:00)
   - ✅ telegram_bot.py: credit-callbacks + alert confirmation
   - ✅ test_credit.py — базовое покрытие

*К доработке:*

   - ⬜ Контракт: где список банков/МФО? Жёсткий список или regex по почте?
   - ⬜ Документация: README-секция «как пользователю активировать»
   - ⬜ Тесты покрывают только парсинг, не дедупликацию алертов
   - ⬜ Связь с категорией расходов в items/purchases
   - ⬜ Решение: оставить как отдельный модуль или сливать с alerts engine
     из Phase 1

*Приоритет: средний, не блокирует Phase 3.*

------------------------------
⬜ Фаза 3 — Анализ предпочтений и профиль пользователя

   - ⬜ Еда: любимые блюда, рестораны, частота, бюджет
   - ⬜ Каршеринг: предпочитаемые авто, маршруты, время суток
   - ⬜ Покупки: категории, бренды, ценовой диапазон, сезонность
   - ⬜ Фото → Memory Lane: стиль, бренд, похожие товары, wishlist
   - ⬜ /link <url> → web fetch → парсинг → wishlist

------------------------------
⬜ Фаза 4 — Автономные заказы

   - ⬜ Проактивные предложения на основе истории
   - ⬜ Формирование корзины (Яндекс Еда / Лавка)
   - ⬜ Рекомендация авто (Драйв) — задел уже есть в /find_car
   - ⬜ Бюджетный контроллер: лимиты категорий

------------------------------
⬜ Фаза 5 — Needs Engine + Байесовская модель

   - ⬜ Temporal Pattern: continuous/seasonal/event_driven/lifecycle
   - ⬜ P(need | season, calendar, inventory, budget, profile)
   - ⬜ Граф зависимостей: requires/enables/consumes/replaces

------------------------------
⬜ Фазы 6–8

   - *Фаза 6:* Price tracking, поиск на Ozon / WB / Яндекс.Маркет
   - *Фаза 7:* Multi-profile, экспорт/импорт данных
   - *Фаза 8:* Сеть агентов (только при явном согласии владельца)

------------------------------
🔧 Технический долг — Приоритет 1

   - ⬜ Item-level парсинг для Ozon (_parse_ozon_items) — сейчас 12% items linked
   - ⬜ Item-level парсинг для Yandex.Market
   - ⬜ Очистка recognized_items_log (1230 мусорных записей)
   - ⬜ Удалить тестовую запись item id 843 (data_origin='telegram_tag')
   - ⬜ Сбор курсов валют на дату чека
   - ⬜ Алерты low_stock (требует заполнения remaining вручную)
   - ⬜ Pre-commit hook: `python -m py_compile` для всех изменённых .py
     (профилактика регрессии вроде e2922a9)
   - ⬜ Документировать credit_monitor / sms_monitor (Phase 2.8)

------------------------------
⚙️ Инфраструктура

   - ✅ Git синхронизация: WSL → bare-репо на Windows (post-commit hook)
   - ✅ CEO-агент Paperclip видит актуальный код
   - ✅ .gitignore: исключены бинарные артефакты, дата-директории, .venv*/
   - ✅ Gateway: allowInsecureAuth=false (нет спама 401 от Яндекс.Браузера)
   - ✅ .env: rotated Gmail app-password (CMO 10.05.2026 22:03)
   - ⬜ По желанию: GitHub как второй remote

------------------------------

consumption_agent · git: 6424748 · обновлено 11.05.2026
