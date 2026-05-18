import purchase_duplicate_detector as pdd


def test_format_duplicate_question_escapes_markdown_sensitive_text():
    group = {
        'store_name': 'FUNGRAD_KHODYNSK_P_QR',
        'purchase_date': '2026-05-16',
        'purchases': [
            {'id': 1, 'source': 'sms_sber', 'amount': 1800.0},
            {'id': 2, 'source': 'Mail.ru_Zorea', 'amount': 1800.0},
        ],
    }

    text = pdd.format_duplicate_question(group)

    assert 'FUNGRAD\\_KHODYNSK\\_P\\_QR - 2026-05-16' in text
    assert '📱 SMS\\(Сбер\\): *1800 ₽*' in text
