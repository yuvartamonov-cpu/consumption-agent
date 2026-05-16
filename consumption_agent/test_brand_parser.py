#!/usr/bin/env python3
"""Проверочный CLI-скрипт для brand_parser.py — не pytest-модуль."""
import sys
sys.path.insert(0, '/home/yuri_artamonov/.openclaw/workspace/consumption_agent')

from brand_parser import parse_brand_and_name

__test__ = False


def run_case(name, text, expected_name, expected_brand, expected_replace):
    r = parse_brand_and_name(text)
    ok = True
    if r['name'] != expected_name:
        print(f"  FAIL name: got {r['name']!r}, expected {expected_name!r}")
        ok = False
    if r['brand'] != expected_brand:
        print(f"  FAIL brand: got {r['brand']!r}, expected {expected_brand!r}")
        ok = False
    if r['replace_months'] != expected_replace:
        print(f"  FAIL replace_months: got {r['replace_months']!r}, expected {expected_replace!r}")
        ok = False
    if ok:
        print(f"  OK")
    return ok


def main() -> int:
    all_ok = True
    print("=== Тесты из задачи ===")
    print("1. пиджак lardini 6 мес")
    all_ok &= run_case("p1", "пиджак lardini 6 мес", "пиджак", "lardini", 6)

    print("2. поло hamington 3 мес")
    all_ok &= run_case("p2", "поло hamington 3 мес", "поло", "hamington", 3)

    print("3. стремянка 5 ступеней (без срока)")
    all_ok &= run_case("p3", "стремянка 5 ступеней", "стремянка 5 ступеней", None, None)

    print("4. пиджак circolo замена 24 мес")
    all_ok &= run_case("p4", "пиджак circolo замена 24 мес", "пиджак", "circolo", 24)

    print("5. Носки | бренд Nike | замена 12 мес")
    all_ok &= run_case("p5", "носки | бренд Nike | замена 12 мес", "носки", "Nike", 12)

    print("6. Пылесос | бренд Xiaomi | замена 60 мес")
    all_ok &= run_case("p6", "Пылесос | бренд Xiaomi | замена 60 мес", "Пылесос", "Xiaomi", 60)

    print("7. Куртка The North Face замена 36 мес")
    all_ok &= run_case("p7", "Куртка The North Face замена 36 мес", "Куртка", "The North Face", 36)

    print("8. Джинсы Levi's 501 (без срока)")
    all_ok &= run_case("p8", "Джинсы Levi's 501", "Джинсы", "Levi's 501", None)

    print("9. Ноутбук Lenovo ThinkPad X1 Carbon замена 48 мес")
    all_ok &= run_case("p9", "Ноутбук Lenovo ThinkPad X1 Carbon замена 48 мес", "Ноутбук", "Lenovo ThinkPad X1 Carbon", 48)

    print("10. Только название без бренда и срока")
    all_ok &= run_case("p10", "футболка", "футболка", None, None)

    print("11. Рубашка белая (цвет не бренд)")
    all_ok &= run_case("p11", "рубашка белая", "рубашка белая", None, None)

    print()
    if all_ok:
        print("✅ Все тесты пройдены")
        return 0
    print("❌ Есть ошибки")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
