---
name: visual-product-search
description: Многоэтапный конвейер поиска товаров по фото из Memory Lane. Покрывает Vision-атрибуты, query expansion, каноникализацию, ценовые аномалии, taste re-ranking, Thompson-sampling bandit, click tracking. Используй для поиска товаров на маркетплейсах, проверки визуального соответствия, фильтрации подделок, учёта вкуса владельца.
---

# Visual Product Search — Sprint Plan

> Обновлено: 2026-05-17 (по итогам 5-дневного спринта 13–17 мая)
> Статус: pipeline + retrieval + UX полностью рабочие; добавлены price-drop alerts

## Production Note (2026-05-21)

Для фактического алгоритма, который сейчас крутится в боте, см.:

- `consumption_agent/docs/recognition_algorithms.md`

Ключевая актуализация:

- foreign retrieval теперь строится через semantic visual query, а не буквальный перевод текста;
- top-3 результата в Telegram выводятся отдельными URL-кнопками;
- поиск после `inventory -> Memory Lane` использует тот же `ml_search_v2` pipeline.
- `site:google.com` URL-кнопки больше не создаются (Telegram их не открывает нормально).
- `.env` загружается при старте бота через `dotenv.load_dotenv()`, так что Gemini и xAI ключи работают.

## Production Update (2026-05-21, late session)

### 🔑 Категорийный индекс источников (`ml_sources_index.py`)
Создан модуль `ml_sources_index.py` и таблица `ml_sources` в consumption.db:

```sql
CREATE TABLE ml_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_type TEXT DEFAULT 'link_only',
    search_url_template TEXT,
    domain TEXT,
    priority INTEGER DEFAULT 0,
    last_used_at TIMESTAMP,
    use_count INTEGER DEFAULT 1,
    UNIQUE(category, source_name)
);
```

**Логика:**
1. При поиске товара проверяется его категория (из memory_lane_items)
2. `get_sources_for_item(category)` сначала ищет источники в таблице `ml_sources` для этой категории
3. Если не найдено — отдаёт дефолтные провайдеры + геолокационные поставщики
4. После завершения поиска `record_sources_from_search()` записывает все реально использованные источники в БД
5. При следующем поиске товара из той же категории база уже знает, где искать

### 🌍 Геолокационные провайдеры (`get_geo_providers`)
Модуль определяет поставщиков на основе геолокации пользователя:
- **RU**: Ozon, Яндекс.Маркет, Wildberries, DNS, М.Видео, Lamoda, Citilink, Hoff, AliExpress
- **EU/UK/DE/FR/IT**: Amazon (US/DE/FR/IT), eBay, Idealo, Billiger, Farfetch, Luisaviaroma, Net-a-Porter, Vestiaire Collective, Grailed, StockX
- **KZ/BY**: Kaspi, Satu, Onliner
- Другие регионы: Amazon, eBay, AliExpress

Геолокация определяется по IP (через ip-api.com или захардкоженный fallback на RU для текущей конфигурации)

### ⏭️ Фильтрация site:google кнопок
В `_format_top_link_button()`: если URL содержит `google.com/search?q=site:` — кнопка не создаётся. Такие ссылки остаются только в текстовом выводе, но не как inline-кнопки.

### 📝 Прямые поставщики и дистрибьюторы
Добавлены прямые search URL для:
- Ozon: `https://www.ozon.ru/search/?text={query}`
- Яндекс.Маркет: `https://market.yandex.ru/search?text={query}`
- Wildberries: через live API (card v2)
- Megamarket: `https://megamarket.ru/catalog/?q={query}`

### 🚧 Планируется
- Ручное добавление/удаление источников для категории через Telegram-команду
- Приоритизация источников на основе прошлых переходов (CTR per source)
- Автоматическое обновление `ml_sources` при импорте чеков (определение поставщика по магазину в чеке)

---

## Статус реализации

| # | Этап | Модуль | Тесты | Статус |
|---|------|--------|-------|--------|
| 1 | Attribute Extraction | `ml_attributes.py` | 23 | ✅ Done |
| 2 | Query Expansion Tree | `ml_query_expansion.py` | 18 | ✅ Done |
| 3 | Cross-marketplace Canonicalization | `ml_canonical.py` | 36 | ✅ Done |
| 4 | Price Anomaly Detection | `ml_anomaly.py` | 24 | ✅ Done |
| 5 | Inventory Collision Check | `ml_inventory.py` | 24 | ✅ Done |
| 6 | Taste Profile Re-Ranker | `ml_taste.py` | 46 | ✅ Done |
| 7 | Orchestrator v2 Pipeline | `ml_search_v2.py` | 21+ | ✅ Done |
| 8 | Click Tracking + Active Learning | `ml_clicks.py` | 21 | ✅ Done |
| 9 | Thompson-Sampling Bandit | `ml_bandit.py` | 19 | ✅ Done |
| 10 | Telegram Integration | `telegram_bot.py` | — | ✅ Done |
| 11 | CLIP Visual Gate | — | — | 🔲 Not started |
| 12 | Retrieval / Seller Links | `ml_providers.py` | 25 | ✅ Done (Day 1–3) |
| 12a | Official/Distributor Resolver | `ml_official_sites.py` | 18 | ✅ Done (Day 2) |
| 13 | Reverse Image Search | — | — | 🔲 Not started |
| 14 | Price Drop Alerts | `ml_watchlist.py` | 20 | ✅ Done (Day 5) |
| 14a | Telegram pagination | `ml_search_v2.py` | 11 | ✅ Done (Day 4) |
| 15 | OOS Recovery | — | — | 🔲 Not started |

**Итого тестов:** 513 (baseline 421 → +92 за 5-дневный спринт).

---

## Архитектура конвейера

```
[Memory Lane photo + caption]
        │
        ▼
┌─────────────────────────┐
│ 1. Attribute Extraction │  Vision API → 15-field JSON schema           ✅
└──────────┬──────────────┘  ml_attributes.py
           │
           ▼
┌─────────────────────────┐
│ 2. Query Expansion Tree │  6-tier specificity tree                     ✅
└──────────┬──────────────┘  ml_query_expansion.py
           │
           ▼
┌─────────────────────────┐
│ 3. Federated Search     │  WB API + прямые seller links + brand search 🟡
└──────────┬──────────────┘  ml_search_v2.py + ml_providers.py
           │
           ▼
┌─────────────────────────┐
│ 4. CLIP Visual Gate     │  cosine_similarity(emb_orig, emb_cand) ≥ τ  🔲
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│ 5. Canonicalization     │  4-tier fingerprinting + dedup               ✅
└──────────┬──────────────┘  ml_canonical.py
           │
           ▼
┌─────────────────────────┐
│ 6. Anomaly Filter       │  3-check cascade: intra/vision/brand        ✅
└──────────┬──────────────┘  ml_anomaly.py
           │
           ▼
┌─────────────────────────┐
│ 7. Inventory Collision  │  rapidfuzz token_set_ratio ≥ 75             ✅
└──────────┬──────────────┘  ml_inventory.py
           │
           ▼
┌─────────────────────────┐
│ 8. Taste Re-Ranker      │  time-decay profile + combined score        ✅
└──────────┬──────────────┘  ml_taste.py
           │
           ▼
┌─────────────────────────┐
│ 9. Bandit Allocator     │  Beta(α,β) Thompson sampling per source     ✅
└──────────┬──────────────┘  ml_bandit.py
           │
           ▼
┌─────────────────────────┐
│10. Output + Tracking    │  Telegram buttons + impression/click log    ✅
└─────────────────────────┘  ml_clicks.py + telegram_bot.py
```

---

## Реализованные алгоритмы (справка)

### 1. Attribute Extraction
- 15-field strict JSON schema: category, subcategory, brand, model, article, primary_color, secondary_colors, material, fit, length, season, style, gender, estimated_price_rub, confidence
- `validate_attributes()` — type-checks enums, clamps confidence [0,1], strips null-like
- Injectable `vision_caller` for testing without OpenAI API
- Cached in `attributes_json` column (idempotent ALTER TABLE migration)

### 2. Query Expansion Tree
- 6 tiers: article → brand_article → brand_model → brand_subcat → descriptive → style_broad
- `queries_for_source()` — brand sites get only precise tiers
- `_clean()` strips None/null/"—", `_join()` joins non-empty parts

### 3. Canonicalization
- `normalize()` — lowercase, strip punctuation, preserve Cyrillic й/ё (no NFKD!)
- 4-tier fingerprint: attr-strong (brand|article) → attr-loose (subcat|color) → tokens (top-4 TF tokens) → unknown (sha12)
- `canonicalize()` groups by fingerprint, merges prices across stores
- Stop-tokens: купить, оригинал, sale, новый, prepositions

### 4. Price Anomaly Detection
- 3-check cascade: (a) intra-group min < 0.40×median, (b) vision estimate outside [0.30, 1.80]×est, (c) brand history median < 0.50×avg
- `Anomaly` frozen dataclass with kind/severity/baseline/observed/reason
- Injectable `brand_history_provider`

### 5. Inventory Collision Check
- `build_query_text()` from attrs → rapidfuzz `token_set_ratio` + BRAND_BOOST(10) + MODEL_BOOST(10)
- `find_inventory_collisions()` with configurable threshold (default 75)
- Fallback token-intersection scorer when rapidfuzz unavailable
- Defensive schema handling (works with/without deleted_at, brand, name columns)

### 6. Taste Profile Re-Ranker
- `build_taste_profile()` — time-decay `exp(-age/180)`, extracts tokens from style_tags/topic/brand/name/description/attributes_json
- `taste_score()` → [-1,1] via `tanh((pos-neg)/5.0)`
- Combined score: 0.50×taste + 0.20×trust + 0.30×price_advantage
- `SOURCE_TRUST` map with fuzzy matching via `_core()` collapse
- Price advantage: linear inversion cheapest=1.0, expensive=0.0

### 7. Thompson-Sampling Bandit
- `Beta(α,β)` posterior per (category, source), start uniform Beta(1,1)
- Positive click → α += 1; impression without follow-up → β += 1
- Temporal decay: `new = 1 + (old-1) × 0.5` for rows > 90 days stale
- Sampling via Gamma trick: `x ~ Gamma(α,1), y ~ Gamma(β,1), p = x/(x+y)`

### 8. Click Tracking + Active Learning
- Tables: `ml_impressions`, `ml_clicks` with action vocabulary (open/remind/dismiss/like/dislike)
- `ctr_per_source()` for debugging
- `positive_fingerprints()` / `dismissed_fingerprints()` feed taste refinement
- `bandit_outcomes_since()` feeds bandit updates

### 9. Current Retrieval Strategy (после Day 1–3 спринта)
- `route_sources()` приоритет у `Lamoda`, `Brandshop`, `DNS`, `Citilink`, `Hoff`, `IKEA`, `Goldapple`, `Иль де Ботэ`, `Wildberries`, `Яндекс.Маркет`, а также `AliExpress`/`Alibaba` как дополнительных площадок.
- `composite_provider()` в `ml_providers.py` делает упор на **прямые ссылки продавцов**:
  - `Wildberries` — единственный live API provider;
  - 13 ритейлеров (Lamoda, Brandshop, Sneakerhead, DNS, Citilink, М.Видео, Hoff, Mr.Doors, IKEA, Goldapple, Иль де Ботэ, AliExpress, Alibaba) — link-only search URLs;
  - для `brand:<brand>` теперь подключается `ml_official_sites` resolver (Day 2).
- **`ml_official_sites.py`** (Day 2): справочник 25+ брендов (Nike, Adidas, Puma, NB, Apple, Samsung, Xiaomi, Sony, Dyson, IKEA, MAC, Estee Lauder и др.).
  - `resolve_brand_links()` возвращает ссылки в порядке: official > distributor > authorized > brand_page > search_fallback.
  - `lookup_brand()` поддерживает алиасы и partial match по словам (3+ символа).
  - Для неизвестных брендов — fallback на Google/Yandex search.
- **Tier-based sorting** в `ml_search_v2.py` (Day 2): результаты сортируются по tier перед canonicalization.
- **Strict brand gating** в `ml_search_v2.py`: если бренд распознан, в provider уходит только брендовый query.
- **Translation layer** для AliExpress / Alibaba (Day 3):
  - словарь `QUERY_TRANSLATIONS` (200+ слов): одежда, обувь, аксессуары, техника, мебель, косметика, цвета, материалы, fit, сезон, пол, стиль;
  - стемминг `_stem_lookup()` отрезает окончания русских прилагательных (замшевые → suede, демисезонное → all-season);
  - служебные слова (купить, цена, недорого) удаляются из foreign queries.
- **Геолокация источников** (Day 3): `GEO_FOREIGN_SOURCES` по регионам — китайские маркетплейсы доступны в RU/KZ/BY, исключаются в неизвестных регионах. `set_client_geo()` для runtime-config, `_filter_sources_by_geo()` в `route_sources()`.

### 10. Telegram UX — Pagination (Day 4)
- `format_search_pages()` разбивает результат на страницы (по 5 элементов / 4096 символов).
- Сквозная нумерация товаров между страницами.
- Кнопка «📄 Продолжить вывод (N ещё)» — `ml_page_callback()` отправляет следующую страницу.
- `format_search_result_telegram()` сохранён для backward compatibility — возвращает первую страницу.

### 11. Price-Drop Watchlist (Day 5)
- **`ml_watchlist.py`** + таблицы `ml_watchlist` и `ml_price_history`.
- Кнопка «🔔 Следить за ценой (топ-3)» в результатах /ml_search → добавляет до 3 товаров с ценой в watchlist.
- Команды: `/ml_watch` (список с историей цен и ±%), `/ml_unwatch <id>` (убрать).
- **Cron-задача** в 10:00 ежедневно (`run_price_drop_check`):
  1. Достаёт все active watches;
  2. Для WB-URL запрашивает текущую цену через card v2 API;
  3. Если падение ≥ threshold_pct (дефолт 10%) — Telegram-уведомление с кнопкой «❌ Больше не следить».
- Status lifecycle: `active → notified → active` (через add) или `dismissed`.
- Дедупликация по `(item_id, product_url, profile_id)` + автоматический reactivate из `dismissed`.
- `format_drop_notification()` с XSS-защитой (html.escape).

---

## Задачи кодирования (Sprint Backlog)

### ✅ Завершено в спринте 13–17 мая 2026

#### TASK-201: Improve direct seller retrieval — ✅ Done (Day 2, commit `f86175c`)
Tier-based sorting (`_sort_by_tier` в `ml_search_v2.py`) + 13 retail sources в `RETAILER_SEARCH_URLS`.

#### TASK-202: Enrich foreign marketplace translation — ✅ Done (Day 3, commit `edf5119`)
`QUERY_TRANSLATIONS` расширен с 70 до 200+ слов, стемминг русских прилагательных, удаление служебных слов, fallback при отсутствии в словаре.

#### TASK-203: Structured official-site search — ✅ Done (Day 2, commit `f86175c`)
Создан `ml_official_sites.py` со справочником 25+ брендов и `resolve_brand_links()`. Tier ordering: official > distributor > authorized > brand_page > search_fallback.

#### TASK-204: Optional marketplace enrichment — ✅ Done (constant)
Wildberries — единственный живой API. Ozon отключён, YM — link-only.

#### TASK-303: Price Drop Alert system — ✅ Done (Day 5, commit `ae3c0c2`)
`ml_watchlist.py` + cron 10:00 + Telegram-уведомления. WB-fetcher через card v2 API.

#### TASK-PAGINATION: Telegram pagination — ✅ Done (Day 4, commit `a2bfa84`)
`format_search_pages()` + кнопка «Продолжить вывод».

### 🔴 P0 — Critical path (открытое)

### 🟡 P1 — Улучшение качества

#### TASK-301: CLIP Visual Gate (Stage 5)
**Файлы:** новый `ml_clip.py`
**Описание:** Загрузить CLIP (ViT-B/32 или русскоязычный ruCLIP) для вычисления cosine similarity между эмбеддингом оригинального фото и фото кандидата. Порог τ = 0.75 (настраиваемый). Кандидаты ниже порога отбрасываются. Добавить `visual_score` в combined ranking (weight 0.00 → 0.15, за счёт taste 0.50 → 0.35).
**Алгоритм:**
```python
from PIL import Image
import clip, torch

model, preprocess = clip.load("ViT-B/32")

def visual_similarity(img_path_a, img_path_b):
    a = preprocess(Image.open(img_path_a)).unsqueeze(0)
    b = preprocess(Image.open(img_path_b)).unsqueeze(0)
    with torch.no_grad():
        ea = model.encode_image(a)
        eb = model.encode_image(b)
    return torch.cosine_similarity(ea, eb).item()
```
**Acceptance:** 15+ тестов, visual_score интегрирован в `rank_candidates()`.

#### TASK-302: Reverse Image Search aggregator
**Файлы:** новый `ml_reverse_image.py`
**Описание:** Использовать Google Lens / Yandex Images / TinEye API для поиска товара по фото. Парсить результаты, извлекать (title, url, price, store), нормализовать и подавать в canonicalize(). Добавить как дополнительный source в `route_sources()`.
**Acceptance:** Reverse image provider возвращает ≥1 результат для фото товара.

#### TASK-303: Price Drop Alert system — см. раздел «11. Price-Drop Watchlist» выше — ✅ Done (Day 5)

#### TASK-304: OOS (Out-of-Stock) Recovery
**Файлы:** `ml_search_v2.py`
**Описание:** Когда основной запрос возвращает 0 кандидатов, автоматически:
1. Расширить query до следующего tier в expansion tree
2. Добавить "аналог" / "замена" в запрос
3. Ослабить brand constraint
4. Показать результаты с пометкой "💡 Похожие альтернативы"
**Acceptance:** Запрос по несуществующему артикулу возвращает ≥1 альтернативу.

### 🟢 P2 — Оптимизация и polish

#### TASK-401: Bandit warm-start from historical data
**Файлы:** `ml_bandit.py`
**Описание:** При первом запуске, если есть данные в `purchase_items` / `memory_lane_items`, извлечь исторические предпочтения по источникам (из `source` полей, URL-паттернов) и использовать как prior для bandit вместо uniform Beta(1,1).
**Acceptance:** Bandit snapshot после warm-start показывает ненулевые alpha для источников с историей покупок.

#### TASK-402: Morphological query normalization
**Файлы:** `ml_query_expansion.py`, `ml_canonical.py`
**Описание:** Подключить pymorphy3 для лемматизации русских слов. Сейчас "серый" ≠ "серая" ≠ "серое" при matching. Лемматизация в `normalize()` и `_tokens()` решит это.
**Acceptance:** `normalize("серая куртка") == normalize("серый куртка")` (после лемматизации).

#### TASK-403: Embedding-based taste profile (upgrade from token-based)
**Файлы:** `ml_taste.py`
**Описание:** Заменить bag-of-tokens taste scoring на sentence-embedding similarity. Использовать `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` для русского текста. `taste_score = cosine_sim(embed(candidate_text), embed(profile_centroid))`.
**Acceptance:** Taste score корректно оценивает семантически похожие но лексически разные описания.

#### TASK-404: A/B test framework for ranking weights
**Файлы:** новый `ml_ab_test.py`
**Описание:** Параметризовать веса combined score (taste/trust/price/visual). Создать таблицу `ab_experiments(id, name, weights_json, created_at)`. Для каждого поиска случайно выбирать набор весов, логировать в ml_impressions. По CTR определить лучший набор.
**Acceptance:** Два эксперимента с разными весами, CTR сравнение через `/ml_stats`.

#### TASK-405: Telegram inline mode
**Файлы:** `telegram_bot.py`
**Описание:** Поддержка inline queries: пользователь набирает `@bot_name пальто серое` в любом чате, бот возвращает карусель товаров. Использует `InlineQueryHandler` + `InlineQueryResultArticle`.
**Acceptance:** Inline query возвращает ≥1 результат с фото и ценой.

---

## Зависимости и requirements

```
# Уже установлено
python-telegram-bot==22.7
rapidfuzz==3.14.5
APScheduler==3.11.2

# Нужно для P1 задач
# clip-by-openai или ruclip        — TASK-301
# sentence-transformers             — TASK-403
# pymorphy3                         — TASK-402
# serpapi (google-search-results)   — TASK-302
```

---

## Telegram-команды (после спринта 13–17 мая 2026)

| Команда | Описание |
|---------|----------|
| `/ml_search <id>` | Запуск visual product search v2 для item из Memory Lane. Поддерживает пагинацию (кнопка «Продолжить вывод») и watchlist (кнопка «Следить за ценой») |
| `/ml_stats` | CTR по источникам + bandit snapshot + последние события |
| `/ml_last` | Последние 5 ML-обработанных фото |
| `/ml_watch` | Активные price-drop watches с историей цен (Day 5) |
| `/ml_unwatch <id>` | Убрать товар из watchlist (Day 5) |
| `/help` | Полный список команд |

### Inline buttons (callback_data patterns)

| Кнопка | Pattern | Handler |
|---|---|---|
| 🔍 Искать | `ml_search:<item_id>` | `ml_search_callback` |
| 📄 Продолжить вывод | `ml_page:<item_id>:<page>` | `ml_page_callback` |
| 🔔 Следить за ценой | `ml_watch:<item_id>` | `ml_watch_callback` |
| ❌ Больше не следить | `ml_unwatch:<watch_id>` | `ml_unwatch_callback` |
| 🗑 Удалить ML | `ml_delete:<item_id>` | `ml_delete_callback` |
| 🔔 Напомнить | `ml_remind:<item_id>` | `ml_remind_callback` |

---

## Метрики для отслеживания

- **CTR per source** — `/ml_stats` показывает; bandit оптимизирует автоматически
- **Taste hit rate** — % кликов на top-3 vs bottom-3 по taste_score
- **Anomaly precision** — % flagged items, которые реально подозрительные (ручная проверка)
- **Collision recall** — % дубликатов в инвентаре, которые были обнаружены
- **Query expansion coverage** — % items, для которых ≥3 tier queries генерируются
