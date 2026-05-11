# Consumption Agent — Architecture v2 с предложениями и комментариями

**Версия:** 2.0 draft · 11.05.2026  
**Основано на:** `architecture_overview_20260511.md`, v1.0  
**Цель документа:** зафиксировать более управляемую архитектуру персонального агента потребления, добавить блок **Memory Lane**, слой разрешений/подтверждений, audit trail, workflow разработки через GitHub и правила взаимодействия нескольких AI-агентов.

---

## 0. Executive summary

Проект уже вышел за рамки простого Telegram-бота для учёта расходов. Его правильнее рассматривать как **Personal Consumption Agent** — персонального агента владельца, который:

1. собирает историю покупок, заказов, чеков, подписок, поездок и услуг;
2. формирует инвентарь материальных и нематериальных активов;
3. запоминает вкусы и визуальные предпочтения владельца через **Memory Lane**;
4. прогнозирует будущие потребности;
5. предлагает действия;
6. в перспективе выполняет заказы и бронирования только после подтверждения владельца;
7. ведёт полный журнал причин, предложений, подтверждений и действий.

Ключевое архитектурное предложение: отделить **ядро агента** от каналов связи. Telegram, OpenClaw, Claude Code, Paperclip AI и GitHub должны быть не «местом, где живёт логика», а внешними контурами управления, разработки и взаимодействия.

---

## 1. Продуктовая концепция

### 1.1. Рабочее определение

**Consumption Agent** — персональная система управления потреблением, расходами, бытовыми активами, услугами и предпочтениями владельца. Агент действует на стороне владельца, сохраняет приватность, объясняет свои рекомендации и не совершает значимых действий без подтверждения.

### 1.2. Чем агент отличается от обычного трекера расходов

Обычный трекер расходов отвечает на вопрос:

> «Куда ушли деньги?»

Consumption Agent должен отвечать на более сильные вопросы:

> «Что у меня есть?»  
> «Что скоро понадобится?»  
> «Что я обычно покупаю?»  
> «Что мне нравится по стилю?»  
> «Что стоит купить, заменить, продлить, заказать или забронировать?»  
> «Почему агент предлагает именно это?»

### 1.3. Главный принцип

Агент может самостоятельно анализировать, сопоставлять, прогнозировать и готовить действия, но **не должен тратить деньги, оформлять заказ, отменять услугу, отправлять заявку или бронировать ресурс без явного подтверждения владельца**.

---

## 2. Архитектурные принципы v2

| Принцип | Смысл | Комментарий |
|---|---|---|
| **Core-first** | Логика живёт в backend-ядре, а не в Telegram-боте | Telegram может отвалиться, но агент не должен терять состояние |
| **Human-in-the-loop** | Все значимые действия проходят через подтверждение | Особенно покупки, бронирования, заявки, письма, отмены |
| **Memory as product asset** | Память — не побочный лог, а главный актив системы | Покупки показывают прошлое, Memory Lane показывает вкус и намерения |
| **Auditability** | Каждая рекомендация и каждое действие должны быть объяснимы | Важно для доверия владельца и отладки агента |
| **Dry-run by default** | До зрелости системы все внешние действия имитируются | Агент готовит заказ, но не отправляет без разрешения |
| **Least privilege** | Каждый коннектор получает минимально нужные права | Особенно почта, маркетплейсы, банки, календарь, платежи |
| **Model-agnostic orchestration** | Claude, GPT, Grok, Kimi, DeepSeek и др. — сменные исполнители | Нельзя завязывать архитектуру на одну модель |
| **Local-first privacy** | Чувствительные данные остаются локально, облако получает минимум | Нужно явно маркировать local/cloud/anonymous |
| **Incremental autonomy** | Автономность включается ступенчато по сценариям и лимитам | Сначала совет, потом черновик действия, потом подтверждённое действие |
| **Single source of truth** | GitHub + миграции БД + audit log | Уменьшает хаос от многих агентов и ручных правок |

---

## 3. Целевая архитектура верхнего уровня

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│                              INPUT CHANNELS                                  │
├──────────────────────────────────────────────────────────────────────────────┤
│ Telegram chat/photo/voice     Email/IMAP/PDF receipts     Screenshots        │
│ SMS / Phone Link              Marketplace exports         Manual notes       │
│ Calendar / bookings           Bank notifications          Web links          │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                         INGESTION & NORMALIZATION                            │
├──────────────────────────────────────────────────────────────────────────────┤
│ OCR · QR · PDF parsing · Email parsing · Link extraction · Speech-to-text     │
│ Deduplication · Entity extraction · Category mapping · Source confidence      │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                              MEMORY CORE                                     │
├──────────────────────────────────────────────────────────────────────────────┤
│ 1. Purchases & expenses                                                       │
│ 2. Inventory / assets                                                         │
│ 3. Services / subscriptions / bookings                                        │
│ 4. Memory Lane: visual preference memory                                      │
│ 5. Needs graph: recurring and inferred needs                                  │
│ 6. Preference constraints: likes, dislikes, brands, materials, styles         │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                          REASONING & DECISION LAYER                          │
├──────────────────────────────────────────────────────────────────────────────┤
│ Budget controller · Warranty checker · Lifecycle tracker · Needs engine       │
│ Recommendation engine · Price tracker · Similarity search · Risk scoring      │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                         GOVERNANCE & PERMISSIONS                             │
├──────────────────────────────────────────────────────────────────────────────┤
│ Policy engine · Spending limits · Confirmation flow · Allow/deny lists        │
│ Action risk levels · Approval tokens · Audit log · Rollback strategy          │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                               ACTION LAYER                                   │
├──────────────────────────────────────────────────────────────────────────────┤
│ Draft order · Compare offers · Prepare message · Book service · Create task   │
│ Send only after approval · Reserve only after approval · Cancel only after approval
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                              OUTPUT CHANNELS                                 │
├──────────────────────────────────────────────────────────────────────────────┤
│ Telegram Bot · PDF/HTML reports · Dashboard · GitHub issues · Push alerts     │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Memory Lane

### 4.1. Назначение

**Memory Lane** — это персональная визуально-смысловая память владельца. Владелец на ходу отправляет в бот фото, скриншоты, ссылки, голосовые или текстовые комментарии о понравившихся товарах, услугах, интерьерах, местах, одежде, еде, упаковках, инструментах, автомобилях, сервисах и других объектах.

Цель — не просто сохранить изображение, а извлечь из него признаки вкуса:

- что именно понравилось;
- что не понравилось;
- какие цвета, материалы, формы, бренды, стили, ценовой уровень;
- для какой будущей потребности это может пригодиться;
- какие аналоги искать в будущем.

### 4.2. Почему это стратегически важно

История покупок показывает фактическое прошлое поведение. Memory Lane показывает **вкус, намерения и потенциальное будущее потребление**.

Это делает агента не просто финансовым помощником, а персональным ассистентом, который постепенно понимает стиль владельца.

Пример:

```text
Владелец отправляет фото кресла:
«Нравится цвет, кожа и форма, но не нравятся тонкие ножки».

Агент сохраняет:
- категория: мебель / кресло
- понравилось: тёмная кожа, округлая форма, премиальный кабинетный стиль
- не понравилось: тонкие ножки
- будущая потребность: найти похожее кресло для кабинета
- стиль: modern classic, office, premium, dark leather
```

### 4.3. Поток данных Memory Lane

```text
Telegram photo / screenshot / link / voice note
  → Media storage
  → Fast captioning
  → User comment parsing
  → Category detection
  → Feature extraction
  → liked/disliked attributes
  → style tags
  → vector embedding
  → Memory Lane item
  → optional lazy enrichment
  → later recommendation / search / purchase proposal
```

### 4.4. Fast path и lazy enrichment

#### Fast path

Срабатывает сразу после отправки фото:

- сохранить оригинал;
- извлечь короткое описание;
- сохранить комментарий владельца;
- определить примерную категорию;
- задать один уточняющий вопрос, если это действительно полезно.

#### Lazy enrichment

Запускается позже по расписанию или по запросу:

- глубокий анализ изображения;
- извлечение признаков товара/услуги;
- поиск похожих объектов;
- построение embeddings;
- обновление taste profile;
- связывание с будущими потребностями.

Комментарий: это правильный подход, потому что владелец не должен ждать долгий анализ в Telegram. Главное — не потерять сигнал.

### 4.5. Предлагаемые таблицы для Memory Lane

#### `memory_lane_items`

| Поле | Тип | Смысл |
|---|---|---|
| `id` | UUID | Идентификатор записи |
| `owner_id` | TEXT | Профиль владельца |
| `created_at` | DATETIME | Дата создания |
| `source_channel` | TEXT | telegram / web / email / manual |
| `source_ref` | TEXT | message_id, url, email_id |
| `title` | TEXT | Короткое название |
| `raw_comment` | TEXT | Исходный комментарий владельца |
| `normalized_comment` | TEXT | Нормализованный смысл |
| `category_id` | UUID | Связь с деревом категорий |
| `object_type` | TEXT | product / service / place / style / idea |
| `liked_features` | JSON | Что понравилось |
| `disliked_features` | JSON | Что не понравилось |
| `style_tags` | JSON | Стили и визуальные теги |
| `materials` | JSON | Материалы |
| `colors` | JSON | Цвета |
| `brands` | JSON | Бренды, если есть |
| `price_level` | TEXT | low / mid / premium / unknown |
| `future_need_hint` | TEXT | Возможная будущая потребность |
| `confidence` | REAL | Уверенность анализа |
| `privacy_level` | TEXT | local / cloud / anonymous |
| `soft_deleted` | BOOLEAN | Мягкое удаление |

#### `media_assets`

| Поле | Тип | Смысл |
|---|---|---|
| `id` | UUID | Идентификатор медиа |
| `memory_lane_item_id` | UUID | Связь с Memory Lane |
| `file_path` | TEXT | Локальный путь |
| `mime_type` | TEXT | image/jpeg, image/png и т.д. |
| `sha256` | TEXT | Хэш для дедупликации |
| `width` | INTEGER | Ширина |
| `height` | INTEGER | Высота |
| `exif_json` | JSON | EXIF без лишних чувствительных данных |
| `created_at` | DATETIME | Дата сохранения |

#### `preference_signals`

| Поле | Тип | Смысл |
|---|---|---|
| `id` | UUID | Идентификатор сигнала |
| `source_type` | TEXT | purchase / memory_lane / explicit_rule / rejection |
| `source_id` | UUID | ID источника |
| `polarity` | TEXT | like / dislike / neutral / avoid |
| `feature_type` | TEXT | color / material / shape / brand / service_quality |
| `feature_value` | TEXT | dark leather, matte black, no chrome |
| `strength` | REAL | Сила сигнала |
| `decay_policy` | TEXT | none / slow / seasonal |
| `created_at` | DATETIME | Дата |

#### `taste_profiles`

| Поле | Тип | Смысл |
|---|---|---|
| `id` | UUID | Идентификатор профиля |
| `profile_name` | TEXT | Например: office, clothes, food, travel |
| `category_scope` | JSON | Категории, к которым относится профиль |
| `positive_patterns` | JSON | Что нравится |
| `negative_patterns` | JSON | Что избегать |
| `examples` | JSON | Ссылки на характерные Memory Lane items |
| `updated_at` | DATETIME | Дата обновления |

### 4.6. Пример JSON-записи Memory Lane

```json
{
  "type": "memory_lane_item",
  "object_type": "product",
  "category": "furniture.armchair",
  "source": "telegram_photo",
  "raw_comment": "Нравится форма и кожа, но ножки слишком тонкие",
  "liked_features": ["dark leather", "rounded shape", "premium office look"],
  "disliked_features": ["thin metal legs"],
  "style_tags": ["modern classic", "office", "premium", "dark interior"],
  "materials": ["leather"],
  "colors": ["dark brown", "black"],
  "future_need_hint": "find similar armchair for office",
  "recommendation_use": "use as positive style reference but exclude thin legs",
  "privacy_level": "local",
  "confidence": 0.78
}
```

### 4.7. Команды Telegram для Memory Lane

| Команда | Назначение |
|---|---|
| `/ml_add` | Добавить фото/скриншот/ссылку в Memory Lane |
| `/ml_last` | Показать последние сохранённые впечатления |
| `/ml_find похожее кресло` | Найти по памяти похожие объекты |
| `/ml_profile кабинет` | Показать профиль вкуса по теме |
| `/ml_dislike` | Зафиксировать отрицательный сигнал |
| `/ml_link_need` | Связать запись с будущей потребностью |
| `/ml_export` | Экспорт Memory Lane в отчёт |

Практически лучше не заставлять владельца помнить команды. Если бот получает фото с комментарием «нравится», «запомни», «найди потом похожее», он должен сам предлагать сохранить это в Memory Lane.

---

## 5. Memory Core

### 5.1. Слои памяти

```text
Memory Core
├─ Transaction memory
│  ├─ purchases
│  ├─ cheques_log
│  ├─ recognized_items_log
│  └─ price_history
│
├─ Asset memory
│  ├─ items
│  ├─ warranties
│  ├─ lifecycle_events
│  └─ maintenance_records
│
├─ Service memory
│  ├─ subscriptions
│  ├─ bookings
│  ├─ carsharing_trips
│  └─ service_providers
│
├─ Preference memory
│  ├─ memory_lane_items
│  ├─ preference_signals
│  ├─ taste_profiles
│  └─ negative_constraints
│
├─ Needs memory
│  ├─ needs
│  ├─ needs_dependencies
│  ├─ recurring_needs
│  └─ need_forecasts
│
└─ Governance memory
   ├─ action_proposals
   ├─ approvals
   ├─ execution_log
   └─ audit_events
```

### 5.2. Предложение по нормализации

Сейчас в системе уже есть `items`, `purchases`, `categories`, `alerts`, `cheques_log`, `recognized_items_log`, `carsharing_trips`, `credit_alerts`, `impressions`, `needs_dependencies`, `price_history`, `subscriptions`.

Предлагается не ломать текущую БД, а добавить слой нормализации:

1. оставить существующие таблицы как рабочую основу;
2. добавить новые таблицы миграциями;
3. сделать `impressions` либо совместимой таблицей Memory Lane, либо мигрировать её в `memory_lane_items`;
4. зафиксировать все изменения через Alembic-like механизм или простую папку `migrations/` для SQLite.

---

## 6. Needs Engine

### 6.1. Назначение

**Needs Engine** прогнозирует будущие потребности владельца на основе:

- регулярных покупок;
- сроков годности;
- гарантий;
- сезонности;
- подписок;
- событий календаря;
- Memory Lane;
- прошлых отказов от рекомендаций;
- бюджета;
- актуальных цен.

### 6.2. Типы потребностей

| Тип | Пример |
|---|---|
| Расходуемые товары | кофе, вода, корм, батарейки, картриджи, бытовая химия |
| Сроки и гарантии | закончится гарантия, истекает страховка, подходит срок ТО |
| Сезонные | зимняя обувь, школьные товары, подарки, отпуск |
| Сервисные | записаться к врачу, химчистка, ремонт, доставка |
| Стилевые | подобрать похожую мебель, одежду, аксессуар по Memory Lane |
| Финансовые | кредитный платёж, подписка, лимит расходов |

### 6.3. Уровни уверенности

| Уровень | Поведение агента |
|---|---|
| Low confidence | Ничего не предлагает, только копит данные |
| Medium confidence | Мягкая рекомендация |
| High confidence | Конкретное предложение с аргументацией |
| Very high confidence | Готовит draft-action, но ждёт подтверждения |

### 6.4. Таблица `need_forecasts`

| Поле | Тип | Смысл |
|---|---|---|
| `id` | UUID | Идентификатор прогноза |
| `need_type` | TEXT | consumable / warranty / service / style / financial |
| `category_id` | UUID | Категория |
| `predicted_date` | DATE | Когда понадобится |
| `confidence` | REAL | Уверенность |
| `evidence_json` | JSON | На чём основан прогноз |
| `recommended_action` | TEXT | Что сделать |
| `risk_level` | TEXT | low / medium / high |
| `status` | TEXT | new / proposed / accepted / dismissed / expired |
| `created_at` | DATETIME | Дата |

---

## 7. Recommendation Engine

### 7.1. Источники рекомендаций

Recommendation Engine должен учитывать не один фактор, а несколько:

1. история покупок;
2. цена и динамика цен;
3. Memory Lane;
4. taste profile;
5. negative constraints;
6. бюджет;
7. качество поставщика или сервиса;
8. сроки доставки;
9. наличие гарантии;
10. прошлые отказы владельца.

### 7.2. Формат объяснимой рекомендации

Каждая рекомендация должна отвечать на вопросы:

```text
Что предлагается?
Почему сейчас?
На каких данных основано?
Какие есть альтернативы?
Какие риски?
Что будет, если ничего не делать?
Какое действие может подготовить агент?
Что нужно подтвердить владельцу?
```

### 7.3. Пример рекомендации

```text
Рекомендация: заказать 2 упаковки кофе Lavazza Qualità Oro, 1 кг.

Почему:
- вы покупаете кофе примерно раз в 19–24 дня;
- последняя покупка была 21 день назад;
- текущая цена ниже вашей средней цены на 12%;
- бренд уже покупался 5 раз и не был отклонён;
- бюджет категории «продукты / кофе» не превышен.

Действие:
Я могу подготовить заказ, но не оформлять его до подтверждения.

Подтвердить: [Да, подготовить] [Нет] [Показать альтернативы]
```

---

## 8. Governance & Permissions

### 8.1. Почему этот слой критичен

Пока агент только показывает списки и отчёты, риски ограничены. Как только он получает возможность отправлять заявки, бронировать услуги или готовить покупки, нужен отдельный слой управления полномочиями.

Нельзя размазывать правила безопасности по разным скриптам. Они должны быть централизованы.

### 8.2. Уровни автономности

| Уровень | Название | Что разрешено |
|---|---|---|
| 0 | Observe | Только сбор и анализ данных |
| 1 | Recommend | Рекомендации и напоминания |
| 2 | Draft | Подготовка черновиков заказов, писем, бронирований |
| 3 | Confirmed execute | Выполнение после явного подтверждения |
| 4 | Limited autonomy | Автоматическое выполнение только низкорисковых действий в лимитах |
| 5 | Full autonomy | Не использовать в текущей версии |

Рекомендуемый режим на ближайший этап: **уровень 2–3**.

### 8.3. Action risk levels

| Риск | Примеры | Требование |
|---|---|---|
| Low | создать напоминание, обновить локальную запись | можно автоматически |
| Medium | подготовить заказ, подготовить письмо, подобрать варианты | подтверждение перед отправкой |
| High | оплатить, оформить заказ, забронировать, отменить услугу | явное подтверждение + audit |
| Critical | крупная покупка, финансовое действие, юридически значимое письмо | двойное подтверждение |

### 8.4. Таблица `action_proposals`

| Поле | Тип | Смысл |
|---|---|---|
| `id` | UUID | Идентификатор предложения |
| `proposal_type` | TEXT | order / booking / message / reminder / cancellation |
| `title` | TEXT | Короткое название |
| `reason` | TEXT | Почему агент предлагает действие |
| `evidence_json` | JSON | Доказательства и источники |
| `estimated_cost` | DECIMAL | Оценочная стоимость |
| `risk_level` | TEXT | low / medium / high / critical |
| `status` | TEXT | draft / pending_approval / approved / rejected / executed / expired |
| `created_by` | TEXT | model/user/system |
| `created_at` | DATETIME | Дата |
| `expires_at` | DATETIME | До какого момента актуально |

### 8.5. Таблица `approvals`

| Поле | Тип | Смысл |
|---|---|---|
| `id` | UUID | Идентификатор подтверждения |
| `proposal_id` | UUID | Связь с действием |
| `approval_channel` | TEXT | telegram / dashboard / cli |
| `approval_text` | TEXT | Текст подтверждения владельца |
| `confirmed_by` | TEXT | Кто подтвердил |
| `confirmed_at` | DATETIME | Когда подтверждено |
| `confirmation_hash` | TEXT | Хэш для неизменяемости |

### 8.6. Spending limits

Пример политики:

```yaml
spending_policy:
  default_currency: RUB
  rules:
    - max_amount: 0
      allowed_actions: ["observe", "recommend", "draft"]
      confirmation: false
    - max_amount: 1000
      allowed_actions: ["prepare_order"]
      confirmation: true
    - max_amount: 10000
      allowed_actions: ["prepare_order", "reserve_service"]
      confirmation: true
    - max_amount: 10000+
      allowed_actions: ["prepare_order"]
      confirmation: "double"
      require_reason: true
```

---

## 9. Audit trail

### 9.1. Назначение

Audit trail должен фиксировать не только факт действия, но и логику агента:

- что агент увидел;
- что извлёк;
- какую модель использовал;
- какой prompt или policy применил;
- какую рекомендацию сформировал;
- что подтвердил владелец;
- что было исполнено;
- какой результат получен.

### 9.2. Таблица `audit_events`

| Поле | Тип | Смысл |
|---|---|---|
| `id` | UUID | Идентификатор события |
| `event_type` | TEXT | ingest / classify / recommend / approve / execute / error |
| `actor_type` | TEXT | user / system / model / connector |
| `actor_name` | TEXT | telegram_bot / claude / gpt / codex / parser |
| `object_type` | TEXT | purchase / item / proposal / memory_lane_item |
| `object_id` | UUID | ID объекта |
| `summary` | TEXT | Краткое описание |
| `input_hash` | TEXT | Хэш входа |
| `output_hash` | TEXT | Хэш выхода |
| `metadata_json` | JSON | Дополнительные данные |
| `created_at` | DATETIME | Дата |

### 9.3. Комментарий по audit

Это не бюрократия. Для персонального агента audit log — основа доверия. Через месяц владелец должен иметь возможность спросить:

> «Почему ты предложил купить именно это?»

И агент должен показать цепочку данных, а не отвечать общими словами.

---

## 10. Техническая архитектура репозитория

### 10.1. Предлагаемая структура

```text
consumption-agent/
├─ README.md
├─ pyproject.toml
├─ .env.example
├─ .gitignore
├─ docs/
│  ├─ architecture.md
│  ├─ security.md
│  ├─ data_model.md
│  ├─ agent_rules.md
│  ├─ memory_lane.md
│  ├─ development_workflow.md
│  └─ roadmap.md
│
├─ consumption/
│  ├─ __init__.py
│  ├─ config.py
│  ├─ db.py
│  ├─ models/
│  │  ├─ items.py
│  │  ├─ purchases.py
│  │  ├─ memory_lane.py
│  │  ├─ needs.py
│  │  ├─ actions.py
│  │  └─ audit.py
│  │
│  ├─ ingestion/
│  │  ├─ email_importer.py
│  │  ├─ pdf_receipts.py
│  │  ├─ screenshots.py
│  │  ├─ telegram_media.py
│  │  └─ sms_importer.py
│  │
│  ├─ memory/
│  │  ├─ memory_lane_service.py
│  │  ├─ preference_extractor.py
│  │  ├─ vector_index.py
│  │  └─ taste_profile.py
│  │
│  ├─ logic/
│  │  ├─ warranty_checker.py
│  │  ├─ budget_controller.py
│  │  ├─ lifecycle_tracker.py
│  │  ├─ needs_engine.py
│  │  ├─ recommendation_engine.py
│  │  └─ price_tracker.py
│  │
│  ├─ governance/
│  │  ├─ policy_engine.py
│  │  ├─ approvals.py
│  │  ├─ spending_limits.py
│  │  └─ audit_log.py
│  │
│  ├─ actions/
│  │  ├─ order_drafts.py
│  │  ├─ booking_drafts.py
│  │  ├─ message_drafts.py
│  │  └─ dry_run_executor.py
│  │
│  ├─ channels/
│  │  ├─ telegram_bot.py
│  │  ├─ cli.py
│  │  └─ reports.py
│  │
│  └─ llm/
│     ├─ router.py
│     ├─ prompts.py
│     ├─ schemas.py
│     └─ model_registry.py
│
├─ migrations/
│  ├─ 0001_initial.sql
│  ├─ 0002_memory_lane.sql
│  ├─ 0003_governance.sql
│  └─ 0004_audit.sql
│
├─ scripts/
│  ├─ daily_run.sh
│  ├─ run_bot.sh
│  ├─ backup_db.sh
│  └─ rotate_logs.sh
│
├─ tests/
│  ├─ test_memory_lane.py
│  ├─ test_deduplication.py
│  ├─ test_policy_engine.py
│  ├─ test_needs_engine.py
│  └─ test_receipt_parsing.py
│
└─ data/
   ├─ raw/             # ignored by git
   ├─ media/           # ignored by git
   ├─ exports/         # ignored by git
   └─ consumption.db   # ignored by git
```

### 10.2. Комментарий по текущему монолиту

Файл `consumption_agent_full_030526.py` можно оставить как рабочий монолит на переходном этапе, но его надо постепенно разгружать:

1. команды Telegram → `channels/telegram_bot.py`;
2. бизнес-логика → `logic/`;
3. работа с БД → `db.py` и модели;
4. парсеры → `ingestion/`;
5. Memory Lane → `memory/`;
6. approvals/audit → `governance/`.

Не надо переписывать всё сразу. Лучше выносить модули по одному, сопровождая тестами.

---

## 11. LLM orchestration

### 11.1. Роли моделей

Сейчас используются или планируются разные модели: Claude Opus/Sonnet, GPT 5.4–5.5, Grok, Kimi, Mistral Large, DeepSeek, Codex. Это можно превратить из хаоса в преимущество, если закрепить роли.

| Роль | Модель/агент | Назначение |
|---|---|---|
| Product architect | Claude Opus / GPT Thinking | Архитектура, декомпозиция, постановки задач |
| Coding agent | Claude Code Sonnet / Codex | Реализация задач |
| Code reviewer | Codex / GPT Thinking / Claude Opus | Проверка diff, тестов, безопасности |
| Runtime assistant | OpenClaw через Telegram | Оперативная ручная работа и диагностика |
| Research assistant | GPT / Perplexity-like workflow | Поиск API, документации, решений |
| QA controller | отдельный агент | Проверка регрессий и сценариев |

### 11.2. Правило для агентов

Каждый агент должен работать не «по памяти», а через задачу, diff и критерии приёмки.

Минимальный шаблон задачи:

```markdown
# Task

## Goal
Что нужно получить.

## Context
Какие файлы и модули связаны.

## Constraints
Что нельзя менять.

## Acceptance criteria
Как понять, что задача выполнена.

## Tests
Какие тесты нужно добавить или обновить.

## Security notes
Какие риски проверить.
```

### 11.3. GitHub workflow

После создания GitHub-репозитория рекомендуется перейти на такой цикл:

```text
Issue → branch → implementation → tests → pull request → AI code review → manual review → merge → tagged release
```

Минимальные ветки:

- `main` — стабильная версия;
- `dev` — интеграция;
- `feature/memory-lane`;
- `feature/governance`;
- `feature/audit-log`;
- `fix/telegram-reconnect`.

### 11.4. CI

Минимальный GitHub Actions pipeline:

```yaml
name: ci

on:
  pull_request:
  push:
    branches: [main, dev]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -e .[dev]
      - run: pytest
      - run: python -m compileall consumption
```

---

## 12. Security model

### 12.1. Что уже хорошо

- whitelist Telegram по `chat_id`;
- `.env` для секретов;
- `.gitignore` для бинарных и приватных данных;
- локальное хранение SQLite;
- autorestart через systemd;
- WAL-режим SQLite.

### 12.2. Что нужно усилить

| Зона | Риск | Рекомендация |
|---|---|---|
| Telegram token | утечка токена в чат, логи или git | ротация токена, secret scanning, never paste tokens |
| Email access | доступ к личным письмам | app passwords, отдельные ящики, минимальные права |
| DB backup | потеря базы или утечка | шифрованные backup, расписание, проверка восстановления |
| LLM prompts | утечка приватных данных в облако | privacy router: local/cloud/anonymous |
| Autonomous actions | нежелательные траты или отмены | policy engine + approvals + audit |
| Multi-agent coding | случайная порча кода | PR-only workflow, tests, protected main |

### 12.3. Privacy router

Перед отправкой данных в облачную модель нужно определять уровень чувствительности.

```text
local_only:
  - банковские уведомления
  - телефоны, адреса, документы
  - полные чеки с персональными данными
  - токены и пароли

cloud_allowed_after_redaction:
  - обезличенные названия товаров
  - категории расходов
  - общие стилевые признаки

cloud_allowed:
  - публичные описания товаров
  - сравнение характеристик
  - поиск аналогов
```

### 12.4. Secret hygiene

Обязательно добавить:

```text
.env.example
pre-commit hook for secret scanning
GitHub secret scanning
manual token rotation checklist
docs/security.md
```

---

## 13. Data storage strategy

### 13.1. SQLite остаётся правильным выбором

Для single-user локального агента SQLite — рациональный выбор. PostgreSQL пока не нужен, если нет нескольких активных пользователей, тяжёлой аналитики и серверной многопользовательской нагрузки.

### 13.2. Что добавить к SQLite

| Задача | Возможное решение |
|---|---|
| Full-text search | SQLite FTS5 |
| Vector search | sqlite-vec / sqlite-vss / FAISS / Chroma |
| Миграции | SQL-файлы в `migrations/` |
| Backup | ежедневный `.backup` + шифрование |
| Integrity check | `PRAGMA integrity_check` по расписанию |
| WAL checkpoint | регулярный checkpoint |

### 13.3. Vector memory

Для Memory Lane и поиска похожих объектов нужен embedding index.

Минимальный вариант:

```text
memory_lane_items → text summary → embedding → vector_index
media_assets → image caption → embedding → vector_index
```

На первом этапе можно не индексировать сами изображения, а индексировать:

- caption;
- user comment;
- liked/disliked features;
- style tags;
- category.

Это проще, дешевле и достаточно для MVP.

---

## 14. Telegram as interface, not core

### 14.1. Проблема

Telegram удобен для управления, но это ненадёжный единственный канал:

- может отвалиться VPN;
- может устареть токен;
- может быть ошибка webhook/polling;
- могут потеряться сообщения;
- неудобно просматривать сложные отчёты.

### 14.2. Рекомендация

Telegram должен быть тонким интерфейсом:

```text
Telegram message
  → command parser
  → service call
  → response formatter
```

Вся бизнес-логика должна быть доступна независимо:

- из CLI;
- из тестов;
- из будущего dashboard;
- из cron;
- из OpenClaw.

### 14.3. Минимальный CLI

```bash
consumption check
consumption import-email
consumption ml-add --file ./photo.jpg --comment "нравится стиль"
consumption ml-search "похожее кресло для кабинета"
consumption recommend --category coffee
consumption proposals list
consumption proposals approve <id>
```

CLI сильно упростит отладку, особенно когда Telegram снова перестанет отвечать.

---

## 15. MVP v2: что делать в ближайшие дни

### 15.1. Не расширять всё сразу

Сейчас главный риск — слишком много инфраструктуры и слишком много направлений. Поэтому следующий MVP должен быть узким:

> **Memory Lane + безопасные рекомендации без реальных заказов.**

### 15.2. Sprint 1 — Memory Lane MVP

**Цель:** владелец отправляет фото с комментарием, бот сохраняет запись и извлекает признаки вкуса.

Acceptance criteria:

- [ ] команда или автообработка фото в Telegram;
- [ ] сохранение оригинала в `data/media/`;
- [ ] запись в `memory_lane_items`;
- [ ] запись в `media_assets`;
- [ ] извлечение `liked_features`, `disliked_features`, `style_tags`;
- [ ] команда `/ml_last`;
- [ ] простая команда `/ml_find` по текстовому поиску;
- [ ] тесты на сохранение и поиск.

### 15.3. Sprint 2 — Governance MVP

**Цель:** любые будущие действия сначала превращаются в `action_proposal`.

Acceptance criteria:

- [ ] таблица `action_proposals`;
- [ ] таблица `approvals`;
- [ ] policy engine с risk levels;
- [ ] dry-run executor;
- [ ] Telegram-кнопки: approve / reject / explain;
- [ ] audit event на каждое предложение.

### 15.4. Sprint 3 — Recommendation MVP

**Цель:** агент предлагает одну практическую рекомендацию на основе покупок и Memory Lane.

Acceptance criteria:

- [ ] выбрать одну категорию, например кофе, бытовая химия или мебель для кабинета;
- [ ] сформировать forecast или recommendation;
- [ ] объяснить рекомендацию;
- [ ] подготовить draft-action;
- [ ] не выполнять внешнее действие без подтверждения.

---

## 16. Комментарии по текущей архитектуре

### Комментарий 1. Проект сильнее, чем «бот расходов»

Текущая концепция правильно движется к lifecycle management: от момента внимания к объекту до покупки, использования, обслуживания, замены и утилизации. Это стоит сохранить как основную философию.

### Комментарий 2. Memory Lane нужно поднять до уровня ядра

В текущем v1 Memory Lane обозначен как задел через `impressions`. В v2 его лучше выделить как полноценный модуль. Это может стать главным отличием продукта от обычных expense tracker и inventory apps.

### Комментарий 3. Необходимо не только «что понравилось», но и «почему»

Самая ценная единица памяти — не фото, а интерпретация:

```text
понравилось: тёмная кожа, форма, спокойный премиальный стиль
не понравилось: хром, тонкие ножки, дешёвый пластик
```

Без этого Memory Lane превратится в галерею. С этим — в двигатель персонализированных рекомендаций.

### Комментарий 4. Telegram нельзя считать фундаментом системы

Telegram должен быть интерфейсом. Ядро должно работать без него. Это особенно важно с учётом уже возникших проблем с OpenClaw/Telegram-связкой.

### Комментарий 5. Нужно срочно добавить governance перед action layer

Пока агент анализирует — всё безопасно. Как только он начинает готовить заказы, нужна централизованная система:

- proposal;
- approval;
- risk level;
- audit;
- dry-run;
- лимиты.

### Комментарий 6. Multi-agent workflow надо формализовать

Сейчас много агентов и моделей. Это полезно для обучения, но может порождать хаос. Нужны:

- GitHub issues;
- ветки;
- pull requests;
- code review;
- тесты;
- защищённый `main`;
- единый `docs/agent_rules.md`.

### Комментарий 7. Не надо сейчас переходить на тяжёлую архитектуру

PostgreSQL, Kubernetes, сложные очереди и микросервисы сейчас не нужны. Лучше сделать крепкое локальное ядро:

- Python package;
- SQLite WAL;
- миграции;
- тесты;
- CLI;
- Telegram adapter;
- отчёты.

### Комментарий 8. Нужен режим восстановления

Учитывая проблемы с Telegram/OpenClaw, стоит добавить команды:

```bash
consumption doctor
consumption status
consumption restart-bot
consumption check-telegram
consumption check-db
consumption backup-now
```

Это снизит количество ручной нервной диагностики.

### Комментарий 9. Стоит ввести «объяснимость» как обязательное требование

Каждая рекомендация должна иметь объяснение. Это особенно важно, когда агент будет предлагать траты.

### Комментарий 10. Лучший следующий шаг — не автономные покупки, а Memory Lane + proposals

Пока не нужно подключать реальную оплату или оформление заказов. Правильнее сделать:

```text
Memory Lane → recommendation → action proposal → approval → dry-run
```

Только после стабильной работы этого контура можно идти к реальным заказам.

---

## 17. Roadmap v2

### Phase A — Stabilization

- [ ] GitHub repository
- [ ] `.env.example`
- [ ] `README.md`
- [ ] `docs/security.md`
- [ ] `docs/agent_rules.md`
- [ ] tests baseline
- [ ] CLI `consumption status`
- [ ] backup script

### Phase B — Memory Lane MVP

- [ ] `memory_lane_items`
- [ ] `media_assets`
- [ ] Telegram photo capture
- [ ] feature extraction
- [ ] `/ml_last`
- [ ] `/ml_find`
- [ ] basic vector/text search

### Phase C — Governance MVP

- [ ] `action_proposals`
- [ ] `approvals`
- [ ] `audit_events`
- [ ] policy engine
- [ ] spending limits
- [ ] dry-run executor

### Phase D — Needs + Recommendation MVP

- [ ] выбрать 1–2 категории для прогноза
- [ ] recurring need detection
- [ ] explanation template
- [ ] recommendation scoring
- [ ] proposal generation

### Phase E — Controlled external actions

- [ ] только после стабильного governance
- [ ] only draft orders first
- [ ] no payment automation
- [ ] explicit owner confirmation
- [ ] rollback/undo where possible

---

## 18. Критерии зрелости перед автономными действиями

Перед тем как разрешать агенту реальные внешние действия, должны быть выполнены условия:

- [ ] есть audit log;
- [ ] есть approvals;
- [ ] есть spending limits;
- [ ] есть dry-run режим;
- [ ] есть тесты policy engine;
- [ ] есть backup базы;
- [ ] есть восстановление после сбоя;
- [ ] есть лог всех внешних API-вызовов;
- [ ] есть возможность объяснить каждую рекомендацию;
- [ ] нет секретов в репозитории;
- [ ] Telegram не является единственным способом управления.

---

## 19. Итоговая рекомендация

Архитектурно проект стоит развивать не как набор скриптов вокруг Telegram, а как локальное агентное ядро с несколькими интерфейсами.

Самая сильная продуктовая формула:

> **Consumption Agent = Purchase Memory + Inventory + Memory Lane + Needs Engine + Recommendation Engine + Permissioned Action Layer.**

Самый правильный ближайший фокус:

```text
1. GitHub + структура репозитория
2. Memory Lane MVP
3. Governance / approvals / audit
4. Один законченный сценарий рекомендации
5. Только потом реальные заказы и бронирования
```

