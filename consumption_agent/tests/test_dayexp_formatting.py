from telegram_bot import (
    append_expense_row,
    append_store_totals,
    extract_sms_display_time,
    markdown_to_plain_text,
    sanitize_expense_note,
)


def test_extract_sms_display_time_supports_explicit_marker():
    assert extract_sms_display_time('SMS: Самокат 2792₽ (время 20:20)') == '20:20'


def test_extract_sms_display_time_falls_back_to_plain_hhmm():
    note = 'SMS: Счёт карты MIR-0436 15:43 Покупка по СБП 1858р АЗС 77718 Баланс: 12 024.66р'
    assert extract_sms_display_time(note) == '15:43'


def test_append_expense_row_for_sms_hides_balance_and_shows_time():
    lines = []
    row = (
        '2026-05-16',
        1858.0,
        'АЗС_77718',
        'sms_sber',
        'SMS: Счёт карты MIR-0436 15:43 Покупка по СБП 1858р АЗС 77718 Баланс: 12 024.66р',
    )
    append_expense_row(lines, row, {'sms_sber': '📱'})

    assert lines == [
        '📱 *АЗС\\_77718* — 1 858 ₽',
        '   🕐 15:43',
    ]


def test_append_expense_row_can_hide_notes_for_compact_reports():
    lines = []
    row = (
        '2026-05-16',
        1290.0,
        'Самокат',
        'gmail',
        'Распознано из email-чека через OCR',
    )
    append_expense_row(lines, row, {'gmail': '📧'}, show_notes=False)

    assert lines == [
        '📧 *Самокат* — 1 290 ₽',
    ]


def test_sanitize_expense_note_hides_raw_recognition_json():
    note = '{"engine":"vision:gpt-4o-mini","ocr_score":100,"source_path":"/tmp/receipt.jpg"}'
    assert sanitize_expense_note(note) == ''


def test_sanitize_expense_note_keeps_human_text():
    note = 'Заказ с доставкой к 19:00'
    assert sanitize_expense_note(note) == 'Заказ с доставкой к 19:00'


def test_append_store_totals_escapes_markdown_sensitive_store_names():
    lines = []
    rows = [
        ('2026-05-17', 499.0, 'SMOTRESHKA', 'sms_sber', 'SMS: ...'),
        ('2026-05-17', 1800.0, 'FUNGRAD_KHODYNSK_P_QR', 'sms_sber', 'SMS: ...'),
    ]

    append_store_totals(lines, rows, '📌 *По магазинам:*')

    assert lines == [
        '\n📌 *По магазинам:*',
        '  • FUNGRAD\\_KHODYNSK\\_P\\_QR: 1 800 ₽',
        '  • SMOTRESHKA: 499 ₽',
    ]


def test_markdown_to_plain_text_removes_formatting_but_keeps_content():
    text = '📧 *FUNGRAD\\_KHODYNSK\\_P\\_QR* — 1 800 ₽\n   test\\(ok\\)'
    assert markdown_to_plain_text(text) == '📧 FUNGRAD_KHODYNSK_P_QR — 1 800 ₽\n   test(ok)'
