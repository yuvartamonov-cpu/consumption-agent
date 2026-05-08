"""Тесты для consumption.categorize."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from consumption.categorize import categorize, categorize_batch, slug_to_cat_id


def test_categorize_books():
    assert categorize("Питер Пэн | Барри Джеймс") == "cat_culture_books"
    assert categorize("Гарри Поттер и философский камень") == "cat_culture_books"
    assert categorize("Сартр Жан-Поль Тошнота") == "cat_culture_books"


def test_categorize_intimate():
    assert categorize("Эротическое белье HULIGUN") == "cat_sexual"
    assert categorize("Анальная пробка XXS") == "cat_sexual"
    assert categorize("Вибратор-кролик") == "cat_sexual"


def test_categorize_sport():
    assert categorize("Коврик для йоги") == "cat_sport"
    assert categorize("Блок для йоги 2 штуки") == "cat_sport"
    assert categorize("Ватрушка тюбинг 120 см") == "cat_sport"
    assert categorize("Диск балансировочный STARFIT") == "cat_sport"


def test_categorize_clothing():
    assert categorize("MASSIMO DUTTI") == "cat_clo_everyday"
    assert categorize("ETRO Camicie MULTICOL") == "cat_clo_everyday"
    assert categorize("Платье летнее") == "cat_clo_everyday"


def test_categorize_furniture():
    assert categorize("Камин электрический Нэнси Лайн") == "cat_home_furn"
    assert categorize("Elki Lux Елка искусственная ПВХ 180 см") == "cat_home_furn"


def test_categorize_kitchen():
    assert categorize("Турка электрическая, кофеварка") == "cat_home_kitchen"


def test_categorize_unknown():
    assert categorize("Загадочный предмет") is None
    assert categorize("xyz123") is None
    assert categorize("") is None
    assert categorize(None) is None
    assert categorize("ab") is None  # слишком короткое


def test_categorize_batch():
    names = [
        "Питер Пэн | Барри Джеймс",
        "Коврик для йоги",
        "Загадочный предмет",
    ]
    result = categorize_batch(names)
    assert result["Питер Пэн | Барри Джеймс"] == "cat_culture_books"
    assert result["Коврик для йоги"] == "cat_sport"
    assert result["Загадочный предмет"] is None


def test_slug_to_cat_id():
    assert slug_to_cat_id("еда") == "cat_food"
    assert slug_to_cat_id("ОДЕЖДА") == "cat_clo_everyday"
    assert slug_to_cat_id("книги") == "cat_culture_books"
    assert slug_to_cat_id("неведомое") is None
    assert slug_to_cat_id("  спорт  ") == "cat_sport"


def test_priority_specific_over_generic():
    """Специфичные правила имеют приоритет."""
    # «Платье» само по себе → одежда; но если бы перед ним была «эротическая» — то sexual
    assert categorize("Эротическое платье") == "cat_sexual"  # sexual идёт раньше
    assert categorize("Платье повседневное") == "cat_clo_everyday"
