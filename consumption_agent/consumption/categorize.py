"""
categorize.py — категоризация товаров по ключевым словам.
Возвращает category_id или None.
"""
import re
from typing import Optional

# (regex_pattern, category_id) — порядок важен (специфичное → общее)
RULES = [
    # Сервисные расходы
    (r'(?i)доставка|курьерская|компенсация доставки|обработка заказа|пвз\b', 'cat_subscriptions'),
    # Интим
    (r'(?i)эротическ|интим|секс[\s\-]?игр|вибратор|анальн|бдсм|фаллоимитатор|любриканс', 'cat_sexual'),
    # Книги
    (r'(?i)книг|питер пэн|гарри поттер|сартр|мураками|тошнота|бытие и ничто|вглядываясь в солнце|дар психотерапии|мамочка и смысл|роман\b|повесть', 'cat_culture_books'),
    # Спорт
    (r'(?i)коврик.*йог|блок.*йог|мяч.*пилатес|валик.*спин|диск.*баланс|тюбинг|ватрушка|кросс[оа]вк|тренаж[её]р', 'cat_sport'),
    # Авто
    (r'(?i)предохранител.*автомобил|колеса|шины|резина авто|запчаст', 'cat_auto'),
    # Дом и ремонт
    (r'(?i)стремянка|насосная станция|инструмент|шуруповерт|дрель', 'cat_home'),
    # Кухня
    (r'(?i)турка|кофевар|электротурка|сковорода|кастрюля|чайник электр', 'cat_home_kitchen'),
    # Мебель
    (r'(?i)елка искусственн|камин электрический|диван|стол письменн|стул бескаркас|мебел', 'cat_home_furn'),
    # Продукты
    (r'(?i)пакет.майка|ozon fresh|молоко|хлеб|сыр|колбас|конфет|шоколад|зефир|торт', 'cat_food'),
    # Одежда
    (r'(?i)camicie|etro|massimo dutti|dutti|футболк|платье\b|юбк|рубашк|джинс|свитер', 'cat_clo_everyday'),
    # Животные
    (r'(?i)корм.*собак|корм.*кошк|симпарик|ветеринар', 'cat_pets'),
    # Косметика
    (r'(?i)крем\b|шампун|маска для лица|тушь|помада|лосьон', 'cat_cosmetics'),
    # Подписки/IT
    (r'(?i)premium|подписк|netflix|spotify|youtube', 'cat_subscriptions'),
]


def categorize(name: str) -> Optional[str]:
    """Возвращает category_id по правилам или None."""
    if not name or len(name.strip()) < 3:
        return None
    for pat, cat_id in RULES:
        if re.search(pat, name):
            return cat_id
    return None


def categorize_batch(names: list) -> dict:
    """
    Принимает список названий, возвращает {name: category_id_or_None}.
    Используется в cmd_enrich для bulk-обновления.
    """
    return {n: categorize(n) for n in names}


# Маппинг для UI/CLI — словарь slug → cat_id
SLUG_TO_CAT = {
    'еда': 'cat_food', 'продукты': 'cat_food', 'food': 'cat_food',
    'одежда': 'cat_clo_everyday', 'обувь': 'cat_clo_shoes',
    'техника': 'cat_tech', 'электроника': 'cat_tech',
    'книги': 'cat_culture_books', 'книга': 'cat_culture_books',
    'спорт': 'cat_sport',
    'косметика': 'cat_cosmetics',
    'здоровье': 'cat_health_med', 'лекарства': 'cat_health_med',
    'витамины': 'cat_health_vit', 'бады': 'cat_health_vit',
    'дом': 'cat_home',
    'авто': 'cat_auto',
    'животные': 'cat_pets', 'питомцы': 'cat_pets',
    'мебель': 'cat_home_furn',
    'кухня': 'cat_home_kitchen',
    'аксессуары': 'cat_clo_access', 'аксесс': 'cat_clo_access',
    'хобби': 'cat_hobbies',
    'интим': 'cat_sexual',
    'подписки': 'cat_subscriptions',
}


def slug_to_cat_id(slug: str) -> Optional[str]:
    return SLUG_TO_CAT.get(slug.lower().strip())
