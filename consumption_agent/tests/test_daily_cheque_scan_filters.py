import daily_cheque_scan as dcs


def test_extract_amount_from_body_requires_charge_marker():
    assert dcs.extract_amount_from_body("Промокод на товары за 1 ₽", "") is None


def test_extract_amount_from_body_reads_total_line():
    assert dcs.extract_amount_from_body("Сумма к оплате: 449 ₽", "") == 449.0


def test_should_accept_sender_detected_purchase_blocks_yandex_plus_marketing():
    assert not dcs.should_accept_sender_detected_purchase(
        "Яндекс Плюс",
        "Новинки мая уже на Кинопоиске",
        "Смотри подборку и получай бонусы",
        "",
    )


def test_should_accept_sender_detected_purchase_allows_yandex_plus_charge():
    assert dcs.should_accept_sender_detected_purchase(
        "Яндекс Плюс",
        "Чек за подписку Плюс",
        "Списали 449 ₽ за подписку",
        "",
    )


def test_extract_amount_from_body_does_not_guess_fixed_yandex_plus_price():
    assert dcs.extract_amount_from_body("Новинки мая уже на Кинопоиске 449 ₽", "") is None


def test_should_accept_sender_detected_purchase_blocks_ozon_promo():
    assert not dcs.should_accept_sender_detected_purchase(
        "Ozon",
        "Промокод на товары за 1 ₽",
        "Только сегодня скидка и подарок",
        "",
    )


def test_build_sms_dedup_key_normalizes_whitespace_and_case():
    left = dcs.build_sms_dedup_key("900", "Покупка   399р Самокат")
    right = dcs.build_sms_dedup_key("900", "покупка 399р самокат")
    assert left == right
