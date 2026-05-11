#!/usr/bin/env python3
"""
Тестовый скрипт для проверки кредитного мониторинга.
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from credit_monitor import (
    check_email_account, init_credit_tables, save_alerts,
    get_pending_alerts, format_alert_message
)
from sms_monitor import scan_sms_messages

def test_email():
    """Тестирует подключение к почте."""
    print("📧 Тестирование email...")
    
    configs = [
        {
            'name': 'Mail.ru zorea',
            'host': 'imap.mail.ru',
            'port': 993,
            'user': 'zorea2001@mail.ru',
            'password': os.getenv('MAILRU_ZOREA_PASSWORD', '').strip('"').replace(' ', ''),
        },
        {
            'name': 'Yandex',
            'host': 'imap.yandex.ru',
            'port': 993,
            'user': 'HKID2021@yandex.ru',
            'password': os.getenv('YANDEX_APP_PASSWORD', '').strip('"').replace(' ', ''),
        }
    ]
    
    for config in configs:
        if not config['password']:
            print(f"⚠️ Нет пароля для {config['name']}")
            continue
        
        print(f"🔑 Проверяем {config['name']}...")
        alerts = check_email_account(config, days_back=3)
        print(f"   Найдено: {len(alerts)} кредитных сообщений")
        
        for alert in alerts[:2]:
            print(f"     🏦 {alert.sender_name}: {alert.subject[:50]}...")
            print(f"     💰 {alert.payment_amount} ₽, 📅 {alert.payment_date}")

def test_sms():
    """Тестирует сканирование SMS."""
    print("\n📱 Тестирование SMS...")
    alerts = scan_sms_messages(days_back=3)
    print(f"   Найдено: {len(alerts)} SMS")
    
    for alert in alerts[:2]:
        print(f"     📱 {alert['sender']}: {alert['subject'][:50]}...")
        print(f"     💰 {alert['payment_amount']} ₽, 📅 {alert['payment_date']}")

def test_db():
    """Тестирует работу с БД."""
    print("\n💾 Тестирование БД...")
    init_credit_tables()
    
    # Создаём тестовый алерт
    from credit_monitor import CreditAlert
    test_alert = CreditAlert(
        source='test',
        sender='test@bank.ru',
        sender_name='sberbank',
        subject='Тестовый платёж',
        body='Оплатите 1500 руб до 15.05.2026',
        payment_date=datetime(2026, 5, 15),
        payment_amount=1500.0,
    )
    
    save_alerts([test_alert])
    pending = get_pending_alerts(min_days=3)
    print(f"   Ожидающих алертов: {len(pending)}")
    
    for alert in pending:
        print(f"     🔔 {alert.sender_name}: {format_alert_message(alert)}")


if __name__ == '__main__':
    print(f"🧪 Тест кредитного мониторинга: {datetime.now()}")
    test_email()
    test_sms()
    test_db()
    print("✅ Тест завершён")
