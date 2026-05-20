# Recognition Algorithms

Обновлено: 2026-05-21

Этот документ фиксирует фактические алгоритмы, которые сейчас работают в `consumption_agent` для:
- распознавания чеков;
- распознавания предметов по фото;
- распознавания бирок;
- поиска по кнопке `🔍 Искать` из Memory Lane.

Ниже описан именно реальный pipeline по состоянию кода, а не исходные идеи спринта.

## 1. Распознавание товаров по чекам

### Входная точка

Telegram-фото проходит через `bot/handlers/photos.py:photo_handler`, затем через `services/photo_pipeline.py`.

Общий flow:

1. Бот скачивает фото.
2. `parse_photo_mode()` определяет режим:
   - обычное фото;
   - принудительный чек (`чек`, multi-receipt session);
   - бирка;
   - редирект в `/add_item`.
3. Для фото запускаются:
   - `decode_qr()` — попытка прочитать FNS/OFD QR;
   - `ocr_image()` — Tesseract OCR;
   - `parse_clothing_tag()` — tag probe;
   - `classify_image_type()` — OCR-based fallback типизация.
4. `resolve_image_type()` сводит всё вместе:
   - если есть сигналы реальной бирки, тип может быть переопределён в `tag`;
   - если есть признаки чека (QR FNS, receipt indicators, total), `tag` может быть переопределён обратно в `receipt`.

### OCR слой для чеков

Основной OCR делается в `services/ocr.py`.

Алгоритм:

1. Строятся несколько preprocessing-вариантов изображения:
   - исходное;
   - grayscale;
   - upscale + autocontrast + high contrast;
   - binarized + sharpen.
2. Для каждого варианта гоняется Tesseract с `psm=6` и `psm=11`.
3. Каждый результат оценивается `_score_ocr_text()`:
   - количество цифр;
   - количество латинских/кириллических слов;
   - наличие валют и маркеров (`ФН`, `ФПД`, `ИТОГО`, `SIZE`, `TAGLIA`);
   - сильные бонусы за явные признаки чека/бирки;
   - бонусы за barcode/article-like паттерны.
4. Берётся лучший OCR по score.

### Unified receipt pipeline

Дальше работает `services/receipt_pipeline.py`.

Алгоритм `process_source()`:

1. Определяет тип входа:
   - `text_file`;
   - `text`;
   - `pdf`;
   - `image`.
2. Для `image`:
   - сначала Tesseract;
   - если чек слабый (`is_weak_receipt`) — пробует EasyOCR;
   - если всё ещё слабо и `vision_fallback=True` — вызывает `vision_receipt.recognize_receipt()`.
3. Результат нормализуется в `StructuredReceipt`:
   - товары;
   - доставка;
   - дата;
   - total;
   - engine;
   - ocr_score.

### Парсинг строк чека

Если OCR-текст есть, но structured parser не сработал, pipeline использует `_parse_receipt_lines()`:

Поддерживаемые паттерны:

- `QTY x PRICE` для Ozon/похожих чеков;
- `Название ... 123.45 ₽`;
- эвристика поиска названия товара в предыдущих строках;
- отсеивание OCR-мусора:
  - строки из одних цифр/символов;
  - строки с повторяющимися символами;
  - шумные латинские последовательности;
  - служебные строки (`ИТОГ`, `НДС`, `ФН`, `Зачет`, `Курьер`, `Доставк`).

### Нормализация доставки

`normalize_receipt()`:

- отделяет `delivery_items` от `product_items`;
- суммирует `delivery_total`;
- если доставка есть только как сумма, добавляет synthetic item `Доставка`;
- достраивает total, если он не распознан явно.

### Матчинг с существующими товарами

Перед созданием новых `items` pipeline делает match:

1. Загружает все активные товары.
2. Строит normalized index через matcher.
3. Для каждой product line пытается найти существующий item:
   - `recognized_product = item.name`;
   - `confidence = high`;
   - `brand = ""`;
   - `sku = ""`.
4. Если score >= threshold, товар не создаётся заново:
   - обновляется `purchase_id`, `purchase_price`, `purchase_date`, `quantity`.

### Категоризация новых товарных строк

Для новых позиций работает inline LLM-классификация:

1. Доставка сразу идёт в `service`.
2. Товарные позиции проходят `_classify_receipt_item_category()`.
3. Используется `bot.ai_categorizer.suggest_category_options()`:
   - только существующие категории;
   - без автосоздания новых категорий;
   - до 3 вариантов;
   - confidence 0..100.
4. Если top-1 confidence >= 60:
   - товар сохраняется сразу в эту категорию.
5. Если confidence < 60:
   - товар сохраняется в `other`;
   - в `PipelineApplyResult.category_reviews` кладётся review-task с 3 вариантами.

### Подтверждение категории пользователем

После ответа по чеку Telegram-слой запускает review-очередь:

1. Бот показывает один спорный товар за раз.
2. Пользователь видит 3 кнопки категорий + кнопку `Не подходит`.
3. При выборе:
   - `items.category_id` обновляется сразу.
4. При отказе:
   - бот просит ещё варианты;
   - rejected ids исключаются из следующего LLM-запроса.
5. После 3 отказов:
   - товар остаётся в `Прочее`.

Итоговый flow по чеку:

`photo -> QR/Tesseract -> EasyOCR? -> Vision fallback? -> parse -> normalize -> match existing item -> classify new item -> review if low-confidence -> persist`.

## 2. Распознавание предметов по фото

### Назначение

Этот pipeline используется для:

- `/add_item` по фото;
- обычных фото товаров в Telegram;
- Memory Lane enrichment;
- inventory item recognition.

### Быстрая классификация фото

`vision_item.classify_photo()`:

1. Вызывает Vision через `services/vision_router.py`.
2. Router всегда пытается провайдеры в порядке:
   - OpenAI;
   - Gemini;
   - xAI.
3. Если OpenAI снова становится доступен, он автоматически возвращается на первое место.
4. Классификатор отвечает одним словом:
   - `receipt`;
   - `tag`;
   - `clothing`;
   - `food`;
   - `interior`;
   - `tech`;
   - `item`;
   - `other`.
5. Вызов обёрнут в жёсткий timeout 30 секунд через отдельный `multiprocessing.Process`.

### Полное распознавание предмета

`vision_item.recognize_item()`:

1. Отправляет фото в Vision router с `ITEM_PROMPT`.
2. Ждёт JSON со схемой:
   - `type`;
   - `name`;
   - `brand`;
   - `category`;
   - `color`;
   - `material`;
   - `style_tags`;
   - `description`;
   - `estimated_price_rub`.
3. При timeout возвращает structured error:
   - `{"error": "timeout", ...}`.
4. При invalid JSON — structured parse error.

### Telegram-flow для обычного фото предмета

Если итоговый `image_type` — не чек и не бирка:

1. `photo_handler` уходит в item branch.
2. Запускается `_process_item()`.
3. Если распознавание успешно:
   - формируется `item_data`;
   - если фото есть, данные обогащаются vision metadata;
   - затем запускается category suggestion flow через `items_add._ask_category_suggestion()`.
4. Пользователь подтверждает категорию inline-кнопкой.

Это значит, что item-photo pipeline уже изначально ориентирован на:

- vision-first recognition;
- LLM category suggestion;
- optional human confirmation.

## 3. Распознавание бирок

### Детекция бирки

Бирка определяется комбинацией:

- Vision/ocr classifier (`tag`);
- `resolve_image_type()` heuristics;
- `parse_clothing_tag()` probe.

Сигналы настоящей бирки:

- бренд из белого списка `TAG_BRANDS`;
- артикул/модель;
- barcode 8..14 digits;
- текстовые tag markers:
  - `SIZE`;
  - `TAGLIA`;
  - `ARTICLE`;
  - `CAMICIA`;
  - `MULTICOL`;
  - care/label-like зоны.

### Алгоритм `parse_clothing_tag()`

1. Берётся основной OCR-текст.
2. Если есть `image_path`, делаются дополнительные crop OCR по нескольким зонам:
   - почти вся наклейка;
   - верхняя зона;
   - зона артикула/цены.
3. Изображение отдельно сканируется на barcode.
4. Отдельный crop-проход ищет размер в правой части бирки.
5. Затем по combined text извлекаются:
   - `brand`;
   - `article`;
   - `barcode`;
   - `size`;
   - `color`;
   - `model`;
   - `price`;
   - `currency`.

### Эвристики извлечения

Бренд:

- только из `TAG_BRANDS`;
- есть спец-кейс под OCR-ломаный `Massimo Dutti`.

Артикул:

- regex для `1234/567890`;
- regex для `1234/567/890`;
- fallback на длинные числовые последовательности;
- если нет артикула, может использоваться barcode.

Размер:

- таблицы `EUR/USA/MEX/UK`;
- regex по `SIZE|TAGLIA|РАЗМЕР`;
- fallback crop OCR по правой части бирки.

Цвет:

- словарь `TAG_COLOR_WORDS`.

Цена:

- `€ 123.45`;
- `123.45 EUR`;
- fallback на просто decimal price;
- берётся максимальная разумная цена, так как на плохом OCR первая цифра часто теряется.

### Что сохраняется

Результат идёт в `tag` branch Telegram-flow и затем используется для:

- brand/article/barcode extraction;
- категории по тегу;
- сохранения item с metadata.

## 4. Поиск по кнопке `🔍 Искать` из Memory Lane

### Входная точка

Кнопка `🔍 Искать` вызывает `ml_search_callback`, который передаёт управление в `cmd_ml_search`, а тот запускает `ml_search_v2.search_ml_item_v2()`.

### Алгоритм поиска

1. Загружается запись `memory_lane_items` и путь к фото.
2. Берутся атрибуты:
   - из cached `attributes_json`, если есть;
   - иначе fresh extraction через `ml_attributes.extract_attributes_async()`.
3. Если Vision не дал нужное поле, используются soft priors:
   - `brand` из item row;
   - `category` из topic;
   - `subcategory` из name.

### Vision attribute extraction

На выходе получается schema:

- `category`;
- `subcategory`;
- `brand`;
- `model`;
- `article`;
- `primary_color`;
- `secondary_colors`;
- `material`;
- `fit`;
- `length`;
- `season`;
- `style`;
- `gender`;
- `estimated_price_rub`;
- `confidence`.

### Query building

Дальше строится expansion tree:

1. `article`
2. `brand_article`
3. `brand_model`
4. `brand_subcat`
5. `descriptive`
6. `style_broad`

Дополнительно добавляются:

- `item_name` query, если имя записи отличается;
- `memory_lane_context` и `description_context` из `name/description/style_tags/caption`.

### Semantic translation for foreign sources

Для иностранных площадок запрос не переводится буквально.

Схема:

1. `ml_translate.build_visual_search_query()` собирает компактный visual query из:
   - subcategory/category;
   - brand/model/article;
   - color/material;
   - fit/length/gender/season;
   - style tags.
2. Этот visual query идёт в LLM translation.
3. Prompt прямо говорит переводчику:
   - опираться на атрибуты фото;
   - не переводить шумную подпись дословно;
   - собрать короткий marketplace query.

Это важный слой: поиск ориентирован не только на текст записи, но и на визуально распознанный тип товара.

### Routing sources

`route_sources()` выбирает источники:

- по category map;
- с геофильтром;
- с local aggregators для региона;
- с bandit reorder;
- с pinned `brand:<brand>` на позиции 0.

Если доступен `ml_source_matcher`, он используется первым.

### Retrieval

`ml_providers.composite_provider()` делает federated retrieval:

- brand official/distributor/authorized links;
- retailer search links;
- Wildberries live API;
- Yandex Market link-only;
- foreign marketplaces с semantic translation.

Для top search result в Telegram сейчас показываются:

- отдельные URL-кнопки для топ-3 ссылок;
- пагинация;
- watchlist button.

### Post-processing

После retrieval pipeline делает:

1. strict exact brand filtering;
2. tier sorting (`official > distributor > authorized > brand_page > search_fallback`);
3. canonicalization;
4. anomaly detection;
5. inventory collision detection;
6. taste reranking;
7. impression logging.

### Telegram output

В выдаче:

- первая страница результатов;
- top-3 URL buttons;
- `📄 Продолжить вывод`, если есть ещё страницы;
- `🔔 Следить за ценой (топ-3)`.

## 5. Что пока не реализовано

Не реализовано или не включено в production:

- CLIP visual gate;
- reverse image search aggregator;
- полноценный Ozon live retrieval в v2 search;
- автоматическое автосоздание новых категорий из чеков;
- review UI для массового пакетного редактирования спорных чековых категорий вне Telegram.

## 6. Where To Read In Code

- Чеки: `services/ocr.py`, `services/receipt_pipeline.py`, `services/photo_pipeline.py`, `vision_receipt.py`
- Фото-предметы: `vision_item.py`, `bot/handlers/photos.py`, `bot/handlers/items_add.py`
- Бирки: `services/ocr.py::parse_clothing_tag`, `services/photo_pipeline.py`
- Memory Lane search: `ml_attributes.py`, `ml_query_expansion.py`, `ml_translate.py`, `ml_providers.py`, `ml_search_v2.py`, `bot/handlers/memory_lane.py`, `bot/callbacks.py`
