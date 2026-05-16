---
name: visual-product-search
description: Многоэтапный конвейер поиска товаров по фото из Memory Lane. Покрывает Vision-атрибуты, query expansion, каноникализацию, ценовые аномалии, taste re-ranking, Thompson-sampling bandit, click tracking. Используй для поиска товаров на маркетплейсах, проверки визуального соответствия, фильтрации подделок, учёта вкуса владельца.
---

# Visual Product Search — Sprint Plan

> Обновлено: 2026-05-16
> Тесты: 363 passed | Модули: 9 (ml_*.py) | Коммиты: 8 (Stages 1–6, 9, orchestrator)

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
| 7 | Orchestrator v2 Pipeline | `ml_search_v2.py` | 21 | ✅ Done |
| 8 | Click Tracking + Active Learning | `ml_clicks.py` | 21 | ✅ Done |
| 9 | Thompson-Sampling Bandit | `ml_bandit.py` | 19 | ✅ Done |
| 10 | Telegram Integration | `telegram_bot.py` | — | ✅ Done |
| 11 | CLIP Visual Gate | — | — | 🔲 Not started |
| 12 | Marketplace API Wiring | — | — | 🔲 Not started |
| 13 | Reverse Image Search | — | — | 🔲 Not started |
| 14 | Price Drop Alerts | — | — | 🔲 Not started |
| 15 | OOS Recovery | — | — | 🔲 Not started |

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
│ 3. Federated Search     │  Bandit-routed sources + API calls           🔲
└──────────┬──────────────┘  ml_search_v2.py (stub provider)
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

---

## Задачи кодирования (Sprint Backlog)

### 🔴 P0 — Критический путь (без этого поиск возвращает пустоту)

#### TASK-201: Wire Ozon API provider
**Файлы:** `ml_search.py`, `ml_search_v2.py`
**Описание:** Реализовать `OzonCandidatesProvider` — async функцию, которая принимает `(queries, sources, photo_path)` и возвращает `list[dict]` с полями `title, url, price, store, image_url`. Использовать существующие Ozon-хелперы из `ml_search.py` (сейчас отключены коммитом `5340b1b`). Нужны валидные cookies (файл `ozon_cookies.json` — пользователь должен экспортировать заново, текущий пуст).
**Acceptance:** `candidates_provider` возвращает ≥1 результат для запроса "Nike Air Force 1" на Ozon.

#### TASK-202: Wire Wildberries API provider
**Файлы:** `ml_search.py`, `ml_search_v2.py`
**Описание:** Аналогично TASK-201 для WB. WB API проще (публичный search endpoint). Вернуть `list[dict]` в том же формате.
**Acceptance:** WB provider возвращает ≥1 результат.

#### TASK-203: Wire Yandex Market API provider
**Файлы:** `ml_search.py`, `ml_search_v2.py`
**Описание:** Yandex Market через SerpAPI или прямой scraping. Результат — `list[dict]`.
**Acceptance:** YM provider возвращает ≥1 результат.

#### TASK-204: Composite candidates_provider
**Файлы:** `ml_search_v2.py`
**Описание:** Объединить TASK-201/202/203 в один `CompositeCandidatesProvider`, который запускает все providers параллельно через `asyncio.gather()`, объединяет результаты, нормализует формат. Заменить `_default_candidates_provider` (который сейчас возвращает `[]`).
**Acceptance:** `/ml_search <id>` в Telegram возвращает реальные товары с нескольких площадок.

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

#### TASK-303: Price Drop Alert system (Stage 12)
**Файлы:** новый `ml_price_tracker.py`
**Описание:** Таблица `price_history(fingerprint, source, price, ts)`. Cron-job (APScheduler, каждые 6h) повторяет поиск для items с `set_reminder` кликом. Если `current_price < 0.85 × last_price` — Telegram уведомление пользователю.
**Алгоритм:**
```python
def check_price_drops(conn, threshold=0.85):
    watched = get_watched_items(conn)  # items with ACTION_REMIND clicks
    for item in watched:
        attrs = load_attributes(conn, item['id'])
        queries = expand_queries(attrs)[:1]
        candidates = await fetch(queries, [item['source']])
        current = parse_price(candidates[0]['price'])
        last = get_last_price(conn, item['fingerprint'], item['source'])
        if current and last and current < threshold * last:
            send_price_drop_alert(item, last, current)
        save_price_point(conn, item['fingerprint'], item['source'], current)
```
**Acceptance:** Price drop ≥15% triggers Telegram notification.

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

## Telegram-команды (текущие)

| Команда | Описание |
|---------|----------|
| `/ml_search <id>` | Запуск visual product search v2 для item из Memory Lane |
| `/ml_stats` | CTR по источникам + bandit snapshot + последние события |
| `/ml_last` | Последние 5 ML-обработанных фото |
| `/help` | Полный список команд |

---

## Метрики для отслеживания

- **CTR per source** — `/ml_stats` показывает; bandit оптимизирует автоматически
- **Taste hit rate** — % кликов на top-3 vs bottom-3 по taste_score
- **Anomaly precision** — % flagged items, которые реально подозрительные (ручная проверка)
- **Collision recall** — % дубликатов в инвентаре, которые были обнаружены
- **Query expansion coverage** — % items, для которых ≥3 tier queries генерируются
