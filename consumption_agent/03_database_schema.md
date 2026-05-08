# Схема базы данных (PostgreSQL DDL)

## Ключевые решения

- **Privacy-by-design**: каждая запись имеет поле `data_origin` (local/cloud/anonymous) и `consent_level`.
  Уровень хранения определяется при создании записи, а не постфактум.
- **Retention policy**: каждая таблица с временными данными имеет `retention_days`.
- **MVP-first**: в ядре (Фаза 0–1) — три таблицы: items, purchases, alerts.
  Остальные добавляются по мере расширения.
- **JSONB** — для гибких атрибутов, неопределяемых на уровне схемы.
- **UUID** — первичные ключи (для возможной multi-device синхронизации).
- **soft_delete** везде — ничего не удаляется физически.

---

## CORE — MVP (Фаза 0–1: email-парсер → автоинвентарь → гарантии)

### 1. profiles — профиль владельца

```sql
CREATE TABLE profiles (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name              TEXT,
    data_origin       TEXT DEFAULT 'local' CHECK (data_origin IN ('local', 'cloud')),
    currency          TEXT DEFAULT 'RUB',
    timezone          TEXT DEFAULT 'Europe/Moscow',
    lifestyle_clusters TEXT[] DEFAULT '{}',   -- профессия, дети, климат, жильё, хобби...

    -- Согласия
    consent_email     BOOLEAN DEFAULT false,  -- даём агенту доступ к email?
    consent_cloud     BOOLEAN DEFAULT false,  -- данные могут храниться в облаке?
    consent_aggregate BOOLEAN DEFAULT false,  -- анонимная статистика?

    notification_config JSONB DEFAULT '{"quiet_hours": "23:00-08:00", "max_daily": 3}',

    settings          JSONB DEFAULT '{}',
    created_at        TIMESTAMPTZ DEFAULT now(),
    updated_at        TIMESTAMPTZ DEFAULT now(),
    deleted_at        TIMESTAMPTZ
);
```

### 2. categories — дерево категорий (иерархическое)

```sql
CREATE TABLE categories (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_id         UUID REFERENCES categories(id),
    name              TEXT NOT NULL,
    slug              TEXT NOT NULL,
    path              LTREE,
    icon              TEXT,
    attribute_schema  JSONB DEFAULT '{}',      -- шаблон атрибутов для LLM при распознавании
    temporal_patterns TEXT[] DEFAULT '{}',     -- continuous, seasonal, sparse, event_driven, lifecycle
    sort_order        INT DEFAULT 0,
    is_active         BOOLEAN DEFAULT true,
    retention_days    INT DEFAULT 1095,         -- 3 года по умолчанию
    created_at        TIMESTAMPTZ DEFAULT now(),
    deleted_at        TIMESTAMPTZ
);

CREATE INDEX idx_categories_parent ON categories(parent_id);
CREATE INDEX idx_categories_path ON categories USING GIST(path);
CREATE INDEX idx_categories_slug ON categories(slug);
```

### 3. items — всё, что есть или было (inventory), и что хочется (wishlist)

Универсальная таблица: статус `in_use` / `wishlist` / `disposed` различает тип.

```sql
CREATE TABLE items (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id        UUID NOT NULL REFERENCES profiles(id),
    category_id       UUID NOT NULL REFERENCES categories(id),

    -- Идентификация
    name              TEXT NOT NULL,
    brand             TEXT,
    model             TEXT,
    sku               TEXT,                   -- артикул / штрихкод
    description       TEXT,
    attributes        JSONB DEFAULT '{}',     -- гибкие атрибуты категории

    -- Статус
    status            TEXT NOT NULL DEFAULT 'in_use'
                      CHECK (status IN (
                          'wishlist',          -- хочу, не куплено
                          'in_use',            -- используется
                          'low_stock',         -- заканчивается
                          'storage',           -- на хранении
                          'expired',           -- срок годности истёк
                          'broken',            -- сломано
                          'disposed',          -- утилизировано/продано/подарено
                          'replaced'           -- заменено новым
                      )),
    quantity          INT DEFAULT 1,
    unit              TEXT,                   -- "pcs", "kg", "l", "pack"
    remaining         REAL,                   -- для расходников: остаток (0.0–1.0)

    -- Покупка
    purchase_date     DATE,
    purchase_price    DECIMAL(12,2),
    purchase_currency TEXT DEFAULT 'RUB',
    purchase_source   TEXT,                   -- "ozon", "wb", "ym", "store"
    purchase_url      TEXT,
    purchase_id       UUID REFERENCES purchases(id),

    -- Гарантия и сроки
    warranty_months   INT,
    warranty_until    DATE GENERATED ALWAYS AS (
                          CASE WHEN purchase_date IS NOT NULL AND warranty_months IS NOT NULL
                               THEN (purchase_date + (warranty_months || ' months')::INTERVAL)::DATE
                               ELSE NULL
                          END
                      ) STORED,
    expiry_date       DATE,                   -- срок годности
    lifespan_months   INT,                    -- ожидаемый срок службы

    -- Wishlist-specific
    priority          TEXT CHECK (priority IN ('critical', 'must', 'planned', 'backlog', 'wish')),
    target_price      DECIMAL(12,2),
    current_price     DECIMAL(12,2),
    price_tracking    BOOLEAN DEFAULT false,
    price_source_url  TEXT,
    discovery_source  TEXT CHECK (discovery_source IN (
                          'email_parsed', 'receipt_scan', 'manual',
                          'photo_impression', 'voice_memo', 'link_shared',
                          'screenshot', 'dependency', 'agent_proactive'
                      )),

    -- Приватность
    data_origin       TEXT DEFAULT 'local' CHECK (data_origin IN ('local', 'cloud')),
    consent_level     TEXT DEFAULT 'full' CHECK (consent_level IN ('full', 'anonymized', 'none')),

    -- Связи
    replaces_id       UUID REFERENCES items(id),
    photos            TEXT[] DEFAULT '{}',
    tags              TEXT[] DEFAULT '{}',
    notes             TEXT,

    -- Полнотекстовый поиск
    search_vector     TSVECTOR GENERATED ALWAYS AS (
                          to_tsvector('russian',
                              coalesce(name,'') || ' ' ||
                              coalesce(brand,'') || ' ' ||
                              coalesce(model,'') || ' ' ||
                              coalesce(description,'')
                          )
                      ) STORED,

    created_at        TIMESTAMPTZ DEFAULT now(),
    updated_at        TIMESTAMPTZ DEFAULT now(),
    deleted_at        TIMESTAMPTZ
);

CREATE INDEX idx_items_profile ON items(profile_id);
CREATE INDEX idx_items_category ON items(category_id);
CREATE INDEX idx_items_status ON items(status);
CREATE INDEX idx_items_search ON items USING GIN(search_vector);
CREATE INDEX idx_items_warranty ON items(warranty_until) WHERE warranty_until IS NOT NULL;
CREATE INDEX idx_items_expiry ON items(expiry_date) WHERE expiry_date IS NOT NULL;
CREATE INDEX idx_items_price_tracking ON items(profile_id) WHERE price_tracking = true;
CREATE INDEX idx_items_discovery ON items(discovery_source);
CREATE INDEX idx_items_data_origin ON items(data_origin);
```

### 4. purchases — журнал покупок

```sql
CREATE TABLE purchases (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id        UUID NOT NULL REFERENCES profiles(id),

    -- Покупка в целом
    purchase_date     DATE NOT NULL DEFAULT CURRENT_DATE,
    total_amount      DECIMAL(12,2) NOT NULL,
    currency          TEXT DEFAULT 'RUB',
    payment_method    TEXT,
    source            TEXT,                   -- "ozon", "wb", "ym", "store"
    store_name        TEXT,
    order_number      TEXT,                   -- номер заказа на маркетплейсе

    -- Чек
    receipt_image     TEXT,                   -- путь к фото/скану чека
    receipt_ocr       TEXT,                   -- распознанный текст чека
    email_message_id  TEXT,                   -- id письма, из которого распарсено

    -- Приватность
    data_origin       TEXT DEFAULT 'local' CHECK (data_origin IN ('local', 'cloud')),
    retention_days    INT DEFAULT 1095,       -- хранить 3 года
    auto_delete_at    DATE GENERATED ALWAYS AS (purchase_date + retention_days) STORED,

    notes             TEXT,
    created_at        TIMESTAMPTZ DEFAULT now(),
    deleted_at        TIMESTAMPTZ
);

CREATE INDEX idx_purchases_profile ON purchases(profile_id);
CREATE INDEX idx_purchases_date ON purchases(purchase_date);
CREATE INDEX idx_purchases_source ON purchases(source);
CREATE INDEX idx_purchases_email ON purchases(email_message_id);
```

### 5. alerts — уведомления (гарантии, сроки, скидки)

```sql
CREATE TABLE alerts (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id        UUID NOT NULL REFERENCES profiles(id),
    item_id           UUID REFERENCES items(id),
    purchase_id       UUID REFERENCES purchases(id),

    alert_type        TEXT NOT NULL CHECK (alert_type IN (
                          'warranty_expiring',   -- гарантия истекает через N дней
                          'warranty_expired',    -- гарантия истекла
                          'expiry_approaching',  -- срок годности подходит к концу
                          'expired',             -- срок истёк
                          'low_stock',           -- расходник заканчивается
                          'price_drop',          -- цена на товар из вишлиста упала
                          'seasonal_reminder',   -- сезонная рекомендация
                          'dependency_alert',    -- связанная потребность
                          'budget_warning'       -- превышение бюджета
                      )),

    title             TEXT NOT NULL,
    message           TEXT,
    scheduled_at      TIMESTAMPTZ,             -- когда отправить
    sent_at           TIMESTAMPTZ,             -- когда отправлено
    status            TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'dismissed', 'actioned')),

    -- Действие
    action_type       TEXT,                    -- "view_item", "buy_now", "compare_prices"
    action_data       JSONB,                   -- {item_id, url, target_price, ...}

    -- Приватность
    data_origin       TEXT DEFAULT 'local' CHECK (data_origin IN ('local', 'cloud')),

    created_at        TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_alerts_profile ON alerts(profile_id);
CREATE INDEX idx_alerts_scheduled ON alerts(scheduled_at) WHERE status = 'pending';
CREATE INDEX idx_alerts_type ON alerts(alert_type);
CREATE INDEX idx_alerts_item ON alerts(item_id);
```

---

## EXTENDED — добавляется в Фазе 2+

### 6. impressions — лента впечатлений (Memory Lane)

```sql
CREATE TABLE impressions (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id        UUID NOT NULL REFERENCES profiles(id),

    type              TEXT NOT NULL CHECK (type IN (
                          'photo_vitrine', 'photo_product', 'photo_shop',
                          'screenshot', 'voice_memo', 'text_note', 'link', 'advertisement'
                      )),

    -- Raw-данные (fast path)
    raw_description   TEXT,                   -- quick LLM summary
    embedding         VECTOR(384),            -- для fuzzy matching с wishlist
    media_paths       TEXT[],
    source_url        TEXT,
    location          JSONB,                  -- {lat, lng, place_name}
    mood              TEXT,                   -- "liked", "interested", "curious", "need"

    -- Статус обработки
    recognition_status TEXT DEFAULT 'raw' CHECK (recognition_status IN ('raw', 'processing', 'enriched', 'matched', 'ignored')),

    -- Enriched (lazy, по триггеру)
    matched_item_id   UUID REFERENCES items(id),
    recognition_data  JSONB,                  -- результат глубокого распознавания

    tags              TEXT[] DEFAULT '{}',
    timestamp_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Приватность
    data_origin       TEXT DEFAULT 'local' CHECK (data_origin IN ('local', 'cloud')),
    retention_days    INT DEFAULT 180,         -- впечатления живут полгода
    auto_delete_at    DATE GENERATED ALWAYS AS ((timestamp_seen + retention_days * INTERVAL '1 day')::DATE) STORED,

    created_at        TIMESTAMPTZ DEFAULT now(),
    deleted_at        TIMESTAMPTZ
);

CREATE INDEX idx_impressions_profile ON impressions(profile_id);
CREATE INDEX idx_impressions_status ON impressions(recognition_status);
CREATE INDEX idx_impressions_date ON impressions(timestamp_seen);
```

### 7. needs_dependencies — граф связей потребностей

```sql
CREATE TABLE needs_dependencies (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_item_id    UUID NOT NULL REFERENCES items(id),
    target_item_id    UUID NOT NULL REFERENCES items(id),

    relation          TEXT NOT NULL CHECK (relation IN (
                          'requires', 'enables', 'consumes', 'replaces',
                          'complements', 'upgrades', 'alternative'
                      )),
    relation_weight   REAL DEFAULT 1.0,

    auto_procure      BOOLEAN DEFAULT false,
    auto_alert        BOOLEAN DEFAULT true,

    created_at        TIMESTAMPTZ DEFAULT now(),
    deleted_at        TIMESTAMPTZ
);

CREATE INDEX idx_dep_source ON needs_dependencies(source_item_id);
CREATE INDEX idx_dep_target ON needs_dependencies(target_item_id);
```

### 8. budget_categories — бюджеты по категориям

```sql
CREATE TABLE budget_categories (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id        UUID NOT NULL REFERENCES profiles(id),
    category_id       UUID NOT NULL REFERENCES categories(id),

    period            TEXT NOT NULL DEFAULT 'monthly' CHECK (period IN ('weekly', 'monthly', 'yearly')),
    limit_amount      DECIMAL(12,2) NOT NULL,
    currency          TEXT DEFAULT 'RUB',
    soft_limit        BOOLEAN DEFAULT true,
    rollover          BOOLEAN DEFAULT false,

    period_start      DATE NOT NULL DEFAULT CURRENT_DATE,
    spent             DECIMAL(12,2) DEFAULT 0,
    remaining         DECIMAL(12,2) GENERATED ALWAYS AS (limit_amount - spent) STORED,

    created_at        TIMESTAMPTZ DEFAULT now(),
    deleted_at        TIMESTAMPTZ
);
```

### 9. price_history — история цен

```sql
CREATE TABLE price_history (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id           UUID NOT NULL REFERENCES items(id),
    price             DECIMAL(12,2) NOT NULL,
    currency          TEXT DEFAULT 'RUB',
    source            TEXT,
    url               TEXT,
    recorded_at       TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_price_history_item ON price_history(item_id);
CREATE INDEX idx_price_history_date ON price_history(recorded_at);
```

### 10. subscriptions — подписки и услуги

```sql
CREATE TABLE subscriptions (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id        UUID NOT NULL REFERENCES profiles(id),
    name              TEXT NOT NULL,
    provider          TEXT,
    price_monthly     DECIMAL(10,2),
    price_yearly      DECIMAL(10,2),
    currency          TEXT DEFAULT 'RUB',
    billing_date      INT,
    next_billing      DATE,
    status            TEXT DEFAULT 'active' CHECK (status IN ('active', 'paused', 'cancelled', 'expired')),
    auto_renew        BOOLEAN DEFAULT true,
    trial_ends        DATE,
    notes             TEXT,
    created_at        TIMESTAMPTZ DEFAULT now(),
    deleted_at        TIMESTAMPTZ
);
```
