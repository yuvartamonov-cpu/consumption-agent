---
name: visual-product-search
description: Нетривиальные алгоритмы поиска товаров по фото из Memory Lane для consumption-agent. Используй когда нужно найти конкретный товар на маркетплейсах/сайтах производителей, проверить визуальное соответствие найденного, отфильтровать подделки, учесть вкус владельца, дедуплицировать одинаковые товары на разных площадках. Покрывает CLIP-верификацию, taste profile re-ranking, brand authority cascade, ценовые аномалии, bandit allocation, canonical product matching.
---

# Visual Product Search

## Зачем этот skill

`ml_search.py` (текущая реализация) делает линейный поиск: один запрос → 3 API маркетплейсов → дешёвый = победитель. Это даёт ложноположительные совпадения, не отличает оригинал от подделки и игнорирует вкус владельца, накопленный в `memory_lane_items`.

Этот skill описывает следующее поколение поиска: **многоэтапный конвейер с визуальной верификацией, re-ranking по вкусу и каноникализацией продуктов между площадками**. Реализуется поверх существующего `memory_lane_items` / `media_assets` / `vision_item.py`.

---

## Архитектура конвейера

```
[Memory Lane photo + caption]
        │
        ▼
┌─────────────────────────┐
│ 1. Attribute Extraction │  Vision API → структурированные атрибуты
└──────────┬──────────────┘  (category, brand, color, material, fit, season, article)
           │
           ▼
┌─────────────────────────┐
│ 2. Query Expansion Tree │  N запросов разной специфичности
└──────────┬──────────────┘  (artikul → brand+model → vague style)
           │
           ▼
┌─────────────────────────┐
│ 3. Federated Search     │  Brand-tier + marketplaces + reverse image
└──────────┬──────────────┘  (параллельно, с bandit-аллокацией)
           │
           ▼
┌─────────────────────────┐
│ 4. CLIP Visual Gate     │  Фильтр: candidate ↔ original photo similarity ≥ τ
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│ 5. Canonicalization     │  Группировка одного товара между площадками
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│ 6. Anomaly Filter       │  Подозрительно дёшево → флаг "fake?"
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│ 7. Inventory Collision  │  У вас уже есть похожее → предупреждение
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│ 8. Taste Re-Ranker      │  Boost по liked_features / penalty по disliked
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│ 9. Output + Tracking    │  Telegram-кнопки + click logging → bandit update
└─────────────────────────┘
```

---

## 1. Attribute Extraction (структурированный schema)

**Проблема:** свободно-текстовый `search_query` ("пальто серое") даёт хаотичные результаты.

**Решение:** Vision API возвращает строго-типизированный JSON. Промпт фиксирует схему:

```python
ATTRIBUTE_SCHEMA = {
    "category": "одежда|обувь|техника|мебель|интерьер|косметика|аксессуары|еда",
    "subcategory": "пальто|кроссовки|...",        # узкий тип
    "brand": "string|null",
    "model": "string|null",                        # для техники
    "article": "string|null",                      # артикул на бирке
    "primary_color": "string",                     # русский canonical
    "secondary_colors": ["string"],
    "material": "string|null",                     # шерсть, кожа, хлопок
    "fit": "oversize|regular|slim|null",
    "length": "mini|midi|maxi|cropped|regular|null",
    "season": "winter|spring|summer|autumn|all|null",
    "style": ["casual","sport","formal","streetwear","minimalism"],
    "gender": "male|female|unisex|null",
    "estimated_price_rub": "int|null",
    "confidence": 0.0-1.0
}
```

Эти поля **не** склеиваются в одну строку — они идут в Query Expansion как параметры.

---

## 2. Query Expansion Tree

**Идея:** для одного товара генерируем дерево из 5–7 запросов разной точности. Маркетплейсы по-разному ранжируют точные/широкие запросы → больше шансов попасть.

```python
def expand_queries(attrs: dict) -> list[tuple[str, str]]:
    """Возвращает [(query, specificity_tag), ...] от самого точного к самому широкому."""
    out = []
    # T1: артикул — если есть, всегда самый точный
    if attrs.get('article'):
        out.append((attrs['article'], 'article'))
        out.append((f"{attrs['brand']} {attrs['article']}", 'brand_article'))
    # T2: brand + model
    if attrs.get('brand') and attrs.get('model'):
        out.append((f"{attrs['brand']} {attrs['model']}", 'brand_model'))
    # T3: brand + subcategory + key colour
    if attrs.get('brand') and attrs.get('subcategory'):
        out.append((
            f"{attrs['brand']} {attrs['subcategory']} {attrs.get('primary_color','')}".strip(),
            'brand_subcat_color'
        ))
    # T4: subcategory + colour + material + fit
    parts = [
        attrs.get('subcategory'),
        attrs.get('primary_color'),
        attrs.get('material'),
        attrs.get('fit'),
    ]
    desc_query = ' '.join(p for p in parts if p)
    if desc_query:
        out.append((desc_query, 'descriptive'))
    # T5: style-based broad
    if attrs.get('style'):
        out.append((
            f"{attrs.get('subcategory','')} {' '.join(attrs['style'][:2])}".strip(),
            'style_broad'
        ))
    return out
```

Каждая площадка получает топ-N запросов из этого дерева. Можно поднять precision/recall trade-off через выбор N.

---

## 3. Brand Authority Cascade (federated search)

**Идея:** не все источники равны. Шкала доверия:

| Tier | Источник                                     | Cost | Trust |
|------|----------------------------------------------|------|-------|
| 1    | Официальный сайт бренда (`site:nike.com`)    | low  | 1.0   |
| 2    | Авторизованные ритейлеры (Lamoda, Brandshop) | low  | 0.9   |
| 3    | Крупные маркетплейсы (Ozon, Wildberries, ЯМ) | low  | 0.7   |
| 4    | Категорийные дистрибьюторы (DNS, Goldapple)  | low  | 0.85  |
| 5    | C2C (Avito, Юла)                              | med  | 0.4   |

```python
CATEGORY_TIER_MAP = {
    'одежда':    ['lamoda', 'brandshop', 'wildberries', 'ozon', 'yandex_market'],
    'обувь':     ['lamoda', 'brandshop', 'sneakerhead', 'wildberries', 'ozon'],
    'техника':   ['dns', 'citilink', 'mvideo', 'ozon', 'yandex_market'],
    'мебель':    ['hoff', 'mrdoors', 'ikea', 'ozon', 'wildberries'],
    'косметика': ['goldapple', 'iledebeaute', 'wildberries', 'ozon'],
}

def route_search(attrs: dict) -> list[str]:
    cat = (attrs.get('category') or '').lower()
    sources = list(CATEGORY_TIER_MAP.get(cat, []))
    # Plus brand site if recognised
    if attrs.get('brand'):
        sources.insert(0, f"brand:{attrs['brand']}")
    return sources
```

Поиск идёт **сверху вниз**: если бренд-сайт дал чёткий хит → можно срезать остальные. Если нет → раскрываем шире.

---

## 4. CLIP Visual Verification Gate

**Проблема:** маркетплейс на запрос "Nike Air Force 1 White" возвращает 100 левых кроссовок. Текстовый match ≠ визуальный match.

**Решение:** для каждого кандидата качаем preview-image, считаем CLIP embedding, сравниваем с embedding оригинального фото. Порог `τ = 0.78` (косинусное сходство) — экспериментально для одежды/обуви; для техники можно занизить до 0.7.

```python
# Один раз: построить индекс эмбеддингов для memory_lane_items.media_asset
def embed_image(path: str) -> np.ndarray:
    """OpenCLIP ViT-B/32 → 512-dim normalised vector."""
    img = clip_preprocess(Image.open(path)).unsqueeze(0)
    with torch.no_grad():
        emb = clip_model.encode_image(img)
        emb /= emb.norm(dim=-1, keepdim=True)
    return emb.cpu().numpy()[0]

CLIP_THRESHOLD = {
    'одежда': 0.78, 'обувь': 0.80,
    'техника': 0.72, 'мебель': 0.75,
    'default': 0.75,
}

async def visual_gate(orig_emb: np.ndarray, candidates: list[dict], category: str) -> list[dict]:
    """Возвращает только кандидатов с similarity ≥ threshold(category)."""
    τ = CLIP_THRESHOLD.get(category, CLIP_THRESHOLD['default'])
    passed = []
    for cand in candidates:
        img_path = await download_cached(cand['image_url'])
        cand_emb = embed_image(img_path)
        sim = float(orig_emb @ cand_emb)
        cand['_visual_sim'] = sim
        if sim >= τ:
            passed.append(cand)
    passed.sort(key=lambda c: c['_visual_sim'], reverse=True)
    return passed
```

**Хранение:** добавить колонку `media_assets.clip_embedding BLOB` (512×4 = 2KB на фото). При появлении в Memory Lane — сразу считаем embedding и сохраняем.

**Альтернатива без GPU:** перцептивный хэш (`imagehash.phash`, 8×8 → 64 бит). Хуже семантически, но дёшево и работает для near-duplicate.

---

## 5. Cross-Marketplace Canonicalization

**Проблема:** один и тот же товар (например, чайник Bosch TWK6A011) лежит на Ozon, WB, ЯМ под разными названиями. Пользователю показывают 3 раза.

**Решение:** строим canonical fingerprint и группируем:

```python
def canonical_fingerprint(cand: dict, attrs: dict) -> str:
    """Стабильный хэш для одного и того же товара на разных площадках."""
    key_parts = [
        normalize(attrs.get('brand', '')),
        normalize(cand.get('model') or attrs.get('model', '')),
        normalize(cand.get('article') or attrs.get('article', '')),
    ]
    # Если артикул/модель отсутствуют — fall back к хэшу CLIP-эмбеддинга (round до 8 бит)
    if not any(key_parts):
        emb_quant = (cand['_clip_emb'] * 16).astype(np.int8).tobytes()
        return f"clip:{hashlib.sha1(emb_quant).hexdigest()[:12]}"
    return 'attr:' + '|'.join(p for p in key_parts if p)

def canonicalize(candidates: list[dict], attrs: dict) -> list[dict]:
    groups = {}
    for c in candidates:
        fp = canonical_fingerprint(c, attrs)
        groups.setdefault(fp, []).append(c)
    out = []
    for fp, items in groups.items():
        items.sort(key=lambda x: parse_price(x['price']))
        canonical = {
            **items[0],  # самый дешёвый — primary
            'fingerprint': fp,
            'price_min': parse_price(items[0]['price']),
            'price_max': parse_price(items[-1]['price']),
            'price_median': median(parse_price(i['price']) for i in items),
            'sources_count': len(items),
            'sources': [i['store'] for i in items],
            'all_listings': items,
        }
        out.append(canonical)
    return out
```

В выдаче: «Bosch TWK6A011 — от 2890 ₽ (Ozon) до 3490 ₽ (ЯМ), 3 площадки».

---

## 6. Price Anomaly Detector (counterfeit / overprice)

**Идея:** для бренда X собираем рыночную медиану. Аномально низкая цена → красный флаг. Аномально высокая → переплата.

```python
def detect_price_anomaly(canonical: dict, attrs: dict) -> str | None:
    median_p = canonical['price_median']
    min_p = canonical['price_min']
    est = attrs.get('estimated_price_rub')

    # 1. Внутригрупповая аномалия
    if median_p > 0 and min_p < 0.4 * median_p:
        return 'suspicious_cheap'  # вероятная подделка / устаревший листинг
    # 2. Сравнение с оценкой Vision
    if est and median_p > 1.8 * est:
        return 'overprice'
    if est and median_p < 0.3 * est:
        return 'suspicious_cheap'
    # 3. Сравнение с историей покупок того же бренда
    avg_brand = avg_paid_for_brand(attrs.get('brand'))
    if avg_brand and median_p < 0.5 * avg_brand:
        return 'suspicious_cheap'
    return None
```

В выдаче подделка прячется за emoji: `⚠️ подозрительно дёшево (медиана 12 990, у этого 4 200) — проверьте оригинальность`.

---

## 7. Inventory Collision Check

**Идея:** перед рекомендацией смотрим, нет ли в `items` похожего товара уже у владельца.

```python
def find_inventory_collision(attrs: dict, orig_clip_emb: np.ndarray) -> list[dict]:
    """Возвращает существующие items, визуально похожие на запрос."""
    conn = get_db()
    # Сначала отсекаем по категории + бренду — дешёвый фильтр
    cands = conn.execute("""
        SELECT i.id, i.name, i.brand, i.purchase_date, mp.file_path
        FROM items i
        LEFT JOIN item_photos ip ON ip.item_id = i.id
        LEFT JOIN media_assets mp ON mp.id = ip.media_asset_id
        WHERE i.deleted_at IS NULL
          AND (i.brand = ? OR ? IS NULL)
        ORDER BY i.purchase_date DESC
        LIMIT 50
    """, (attrs.get('brand'), attrs.get('brand'))).fetchall()

    hits = []
    for c in cands:
        if not c['file_path']:
            continue
        emb = get_or_compute_clip(c['file_path'])
        sim = float(orig_clip_emb @ emb)
        if sim >= 0.82:  # высокий порог — почти такой же
            hits.append({**dict(c), 'similarity': sim})
    return sorted(hits, key=lambda x: x['similarity'], reverse=True)
```

В выдаче: `🟡 У вас уже есть похожее: «Парка Uniqlo» (куплено 4 мес назад). Точно нужна вторая?`.

---

## 8. Taste Profile Re-Ranker

**Идея:** Memory Lane — это history of likes/dislikes. Используем для финального ранжирования.

```python
def build_taste_profile(conn, decay_days: int = 180) -> dict:
    """Агрегирует liked_features со временным затуханием."""
    rows = conn.execute("""
        SELECT liked_features, disliked_features, style_tags, topic,
               julianday('now') - julianday(created_at) AS age_days
        FROM memory_lane_items
        WHERE deleted_at IS NULL
    """).fetchall()
    liked_weights = defaultdict(float)
    disliked_weights = defaultdict(float)
    style_weights = defaultdict(float)
    for r in rows:
        w = math.exp(-r['age_days'] / decay_days)
        for f in json.loads(r['liked_features'] or '[]'):
            liked_weights[normalize(f)] += w
        for f in json.loads(r['disliked_features'] or '[]'):
            disliked_weights[normalize(f)] += w
        for t in json.loads(r['style_tags'] or '[]'):
            style_weights[normalize(t)] += w
    return {
        'liked': dict(liked_weights),
        'disliked': dict(disliked_weights),
        'styles': dict(style_weights),
    }

def taste_score(candidate_text: str, profile: dict) -> float:
    """От -1 (всё что не нравится) до +1 (всё что нравится)."""
    text = normalize(candidate_text)
    pos = sum(w for kw, w in profile['liked'].items() if kw in text)
    neg = sum(w for kw, w in profile['disliked'].items() if kw in text)
    sty = sum(w for kw, w in profile['styles'].items() if kw in text)
    total = pos + sty - neg
    return math.tanh(total / 5.0)  # squash в [-1, 1]
```

Финальный скор кандидата:
```
score = 0.55 * visual_sim + 0.25 * taste + 0.10 * trust_tier + 0.10 * price_z
```
где `price_z` — z-score цены относительно группы (дешевле = выше). Веса подбираются по click-through.

---

## 9. Bandit Marketplace Allocator

**Идея:** не все API одинаково полезны. Для категории «обувь» Brandshop даёт лучшие хиты, для «техника» — DNS. Тратить API-бюджет на провальные источники глупо.

**Реализация:** Thompson sampling с Beta-распределением успехов/неуспехов на пару (category, source).

```python
# Схема
CREATE TABLE bandit_stats (
    category TEXT, source TEXT,
    alpha REAL DEFAULT 1.0,   -- successes + 1
    beta REAL DEFAULT 1.0,    -- failures + 1
    PRIMARY KEY (category, source)
);

def sample_sources(category: str, k: int = 3) -> list[str]:
    rows = conn.execute(
        "SELECT source, alpha, beta FROM bandit_stats WHERE category = ?",
        (category,)
    ).fetchall()
    if not rows:
        return CATEGORY_TIER_MAP.get(category, [])[:k]
    sampled = [(r['source'], np.random.beta(r['alpha'], r['beta'])) for r in rows]
    sampled.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in sampled[:k]]

def record_outcome(category: str, source: str, success: bool):
    conn.execute("""
        INSERT INTO bandit_stats (category, source, alpha, beta) VALUES (?, ?, 1, 1)
        ON CONFLICT(category, source) DO UPDATE SET
            alpha = alpha + ?, beta = beta + ?
    """, (category, source, 1.0 if success else 0.0, 0.0 if success else 1.0))
```

**Что считать «успехом»:** Memory Lane запоминает, по какой ссылке владелец кликнул в `/ml_last`. Клик = success для источника этой ссылки.

---

## 10. Reverse Image Search Aggregator

**Идея:** один источник reverse-image (например, Яндекс.Картинки) даёт слабые хиты. Триангуляция между Yandex / Google Lens / Bing Visual / TinEye даёт consensus.

```python
async def reverse_image_consensus(photo_path: str) -> list[dict]:
    tasks = [
        search_yandex_images(photo_path),
        search_google_lens(photo_path),   # через SerpAPI
        search_bing_visual(photo_path),
        search_tineye(photo_path),         # точные дубли (выясняет source)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    # Сливаем по URL → подсчёт сколько источников вернули
    counter = defaultdict(lambda: {'count': 0, 'data': None, 'sources': []})
    for src_name, res in zip(['yandex','google','bing','tineye'], results):
        if isinstance(res, Exception) or not res:
            continue
        for hit in res:
            key = normalize_url(hit['url'])
            counter[key]['count'] += 1
            counter[key]['data'] = hit
            counter[key]['sources'].append(src_name)
    # Сортируем по числу источников (consensus strength)
    out = sorted(counter.values(), key=lambda v: v['count'], reverse=True)
    return [{**v['data'], 'consensus': v['count'], 'sources': v['sources']} for v in out]
```

Совпадение в 3+ источниках = очень высокая уверенность что это тот же товар.

---

## 11. Out-of-Stock Recovery

**Идея:** если все хиты "нет в наличии", не оставлять пользователя пустым.

```python
async def oos_recovery(attrs: dict, profile: dict) -> dict:
    return {
        'reason': 'out_of_stock',
        'restock_estimate': estimate_restock(attrs),   # из истории продаж
        'alternatives_same_brand': await search_brand_catalog(attrs['brand'], attrs['subcategory']),
        'alternatives_same_style': await search_by_taste(profile, attrs['subcategory'], top_n=3),
        'price_alert_offer': True,  # предложить подписаться на возврат в наличие
    }

def estimate_restock(attrs):
    """Грубая оценка: для сезонных товаров — следующий сезон, иначе 14-21 день."""
    season = attrs.get('season')
    if season in ('winter','summer','spring','autumn'):
        return next_season_start(season)
    return (datetime.now() + timedelta(days=14)).date().isoformat()
```

---

## 12. Price Tracking & Drop Alerts

**Идея:** для каждого item в Memory Lane с реакцией `liked` периодически проверяем цену. Падение > 20% от baseline → push в Telegram.

```python
CREATE TABLE ml_price_history (
    item_id INTEGER REFERENCES memory_lane_items(id),
    fingerprint TEXT,            -- canonical product
    source TEXT,
    price_rub INTEGER,
    in_stock INTEGER,
    checked_at TEXT
);

# Cron: каждые 24 часа
async def daily_price_sweep():
    items = conn.execute("""
        SELECT id FROM memory_lane_items
        WHERE liked_features != '[]' AND deleted_at IS NULL
        ORDER BY id DESC LIMIT 50
    """).fetchall()
    for it in items:
        canonical = await search_item_canonical(it['id'])
        if not canonical:
            continue
        baseline = get_baseline_price(it['id'])
        if baseline and canonical['price_min'] <= 0.8 * baseline:
            await send_drop_alert(it, canonical, baseline)
        record_price(it['id'], canonical)
```

Уведомление: `📉 «Плащ Massimo Dutti» из вашего Memory Lane — сейчас 8 990 ₽ (-31%), исходно 12 990 ₽. Lamoda → ссылка`.

---

## 13. Active Learning Loop

Каждое нажатие inline-кнопки в `/ml_last` → событие в `ml_clicks`:

```python
CREATE TABLE ml_clicks (
    item_id INTEGER,
    fingerprint TEXT,
    source TEXT,
    action TEXT,     -- 'open_listing' | 'set_reminder' | 'dismiss'
    rank_position INTEGER,
    ts TEXT
);
```

Используется для:
- обновления bandit (см. §9),
- веса в taste re-ranker (см. §8),
- A/B-теста порога CLIP (если open_listing редко после high-sim — порог можно опустить).

---

## Минимальная схема БД (новые таблицы / колонки)

```sql
-- Эмбеддинги фото (для CLIP gate и inventory collision)
ALTER TABLE media_assets ADD COLUMN clip_embedding BLOB;
ALTER TABLE media_assets ADD COLUMN phash TEXT;     -- быстрый fallback

-- Структурированные атрибуты после Vision (вместо плоского search_query)
ALTER TABLE memory_lane_items ADD COLUMN attributes_json TEXT;

-- Bandit stats
CREATE TABLE IF NOT EXISTS bandit_stats (
    category TEXT, source TEXT,
    alpha REAL DEFAULT 1.0, beta REAL DEFAULT 1.0,
    updated_at TEXT,
    PRIMARY KEY (category, source)
);

-- Price tracking
CREATE TABLE IF NOT EXISTS ml_price_history (
    item_id INTEGER, fingerprint TEXT, source TEXT,
    price_rub INTEGER, in_stock INTEGER, checked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_pph_item ON ml_price_history(item_id);

-- Click tracking
CREATE TABLE IF NOT EXISTS ml_clicks (
    item_id INTEGER, fingerprint TEXT, source TEXT,
    action TEXT, rank_position INTEGER, ts TEXT DEFAULT (datetime('now'))
);
```

---

## Этапы внедрения

| Этап | Минимально полезный кусок                                       | Сложность |
|------|------------------------------------------------------------------|-----------|
| 1    | Attribute schema + Query expansion (§1, §2)                      | low       |
| 2    | Canonicalization без CLIP, только по brand+article (§5)          | low       |
| 3    | Price anomaly + Inventory collision на текстовых атрибутах (§6,§7)| medium    |
| 4    | Taste re-ranker без CLIP (только по тексту) (§8)                 | medium    |
| 5    | CLIP gate (требует torch / openclip / GPU optional) (§4)         | high      |
| 6    | Bandit allocator (§9)                                            | medium    |
| 7    | Reverse image aggregator (§10)                                   | high (нужен SerpAPI/деньги) |
| 8    | Price drop sweep + alerts (§12)                                  | medium    |
| 9    | Click logging + active learning (§13)                            | low       |

**Рекомендация:** этап 1–4 даёт 70% эффекта без тяжёлых зависимостей. CLIP добавляем когда видны false positives.

---

## Ключевые отличия от текущего `ml_search.py`

| Аспект            | Сейчас                       | Должно быть                              |
|-------------------|------------------------------|------------------------------------------|
| Запрос            | один свободный текст          | дерево из 5–7 структурированных          |
| Источники         | 3 маркетплейса                | tier-cascade по категории, bandit-аллокация |
| Фильтрация хитов  | нет                           | CLIP gate (порог по категории)            |
| Дедупликация      | нет                           | canonical fingerprint между площадками    |
| Подделки          | не детектится                 | price anomaly детектор                    |
| Свой инвентарь    | игнорируется                  | inventory collision warning               |
| Вкус владельца    | игнорируется                  | taste profile re-ranker (time-decay)      |
| Out-of-stock      | пустой результат              | restock estimate + альтернативы           |
| Learning          | нет                           | bandit + click tracking + price history   |

---

## Псевдокод финального API

```python
async def search_ml_item_v2(item_id: int) -> SearchResult:
    item = get_ml_item(item_id)
    attrs = await extract_attributes(item)         # §1
    orig_emb = embed_image(item['photo_path'])     # для §4 и §7

    queries = expand_queries(attrs)                # §2
    sources = sample_sources(attrs['category'])    # §9 (bandit)
    raw = await federated_search(queries, sources) # §3

    visually_verified = await visual_gate(orig_emb, raw, attrs['category'])  # §4
    canonical = canonicalize(visually_verified, attrs)                       # §5

    for c in canonical:
        c['anomaly'] = detect_price_anomaly(c, attrs)                        # §6

    collisions = find_inventory_collision(attrs, orig_emb)                   # §7
    profile = build_taste_profile(conn)                                      # §8
    ranked = rank_candidates(canonical, profile, attrs)                      # §8

    if not ranked:
        return await oos_recovery(attrs, profile)                            # §11

    return SearchResult(
        attributes=attrs,
        primary=ranked[0],
        alternatives=ranked[1:5],
        inventory_collision=collisions,
        price_anomalies=[c for c in ranked if c.get('anomaly')],
    )
```

---

## Зависимости

| Что                                    | Когда нужно         | Альтернатива             |
|-----------------------------------------|---------------------|--------------------------|
| `openclip-torch` + `torchvision`        | §4 CLIP gate        | `imagehash.phash` (хуже) |
| `numpy`                                  | §4, §7              | обязательно               |
| `serpapi` / Google Lens API             | §10                 | можно skip               |
| `scikit-learn` (для z-score)            | §8                  | ручной формулой           |
| OpenAI Vision API                        | §1                  | уже есть (vision_item.py)|

---

## Диагностика

**CLIP gate отбраковывает всех:** порог слишком высокий → опустить, или проверить, что preview-images качаются (некоторые маркетплейсы возвращают placeholder).

**Bandit застрял на одном источнике:** добавить ε=0.1 случайного исследования или сбрасывать alpha/beta раз в 30 дней.

**Canonical fingerprint склеивает разные товары:** усилить ключ (модель + цвет + размер), включить CLIP-quantized embedding в fingerprint.

**Taste profile зашумлён старыми реакциями:** уменьшить `decay_days` до 90.

---

## Связанные файлы

| Файл                                | Назначение                                  |
|-------------------------------------|---------------------------------------------|
| `ml_search.py`                       | v1 — линейный поиск (будет заменён)         |
| `ml_search_v2.py` (TODO)             | реализация этого skill                      |
| `memory_lane.py`                     | парсинг подписей, atrribute storage         |
| `vision_item.py`                     | Vision API wrapper (используется в §1)      |
| `clip_index.py` (TODO)               | embedding storage + similarity search       |
| `bandit.py` (TODO)                   | Thompson sampling allocator                 |
| `price_tracker.py` (TODO)            | §12 daily sweep job                         |
