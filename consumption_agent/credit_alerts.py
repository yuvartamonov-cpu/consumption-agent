#!/usr/bin/env python3
"""
Consumption Agent — отправка кредитных алертов через Telegram.
Используется как отдельный скрипт для cron или вызов из основного бота.
"""
import os
import sys
import asyncio
import json
from urllib import parse, request

# Добавляем путь к модулю
sys.path.insert(0, os.path.dirname(__file__))

from credit_monitor import (
    run_check, format_alert_message,
    init_credit_tables, save_alerts, CreditAlert,
    get_alerts_ready_for_notification, record_notification, get_nearest_alerts,
    get_notification_kind,
)
from sms_monitor import scan_sms_messages

# Telegram bot token и chat_id
TELEGRAM_BOT_TOKEN = os.getenv('CONSUMPTION_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('CONSUMPTION_BOT_CHAT_ID', '1477860192')


def build_paid_reply_markup(alert_id: int) -> dict:
    return {
        'inline_keyboard': [[
            {'text': '✅ Оплатил', 'callback_data': f'credit_paid:{alert_id}'}
        ]]
    }


async def send_telegram_message(token: str, chat_id: str, message: str, reply_markup: dict | None = None):
    """Отправляет сообщение через Telegram Bot API."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': message,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    }
    if reply_markup:
        payload['reply_markup'] = reply_markup

    def _send() -> int | None:
        req = request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        )
        with request.urlopen(req, timeout=20) as response:
            result = json.loads(response.read().decode('utf-8'))
        if not result.get('ok'):
            print(f"❌ Ошибка отправки: {result}")
            return None
        return result.get('result', {}).get('message_id')

    return await asyncio.to_thread(_send)


async def process_sms_alerts():
    """Обрабатывает SMS алерты и сохраняет в БД."""
    print("📱 Проверка SMS...")
    sms_alerts = scan_sms_messages()
    
    if not sms_alerts:
        print("   SMS алертов не найдено")
        return []
    
    # Конвертируем в CreditAlert
    credit_alerts = []
    for alert in sms_alerts:
        ca = CreditAlert(
            source='sms',
            sender=alert['sender'],
            sender_name=alert['sender_name'],
            subject=alert['subject'],
            body=alert['body'],
            payment_date=alert['payment_date'],
            payment_amount=alert['payment_amount'],
            raw_message_id=alert.get('raw_message_id', ''),
        )
        credit_alerts.append(ca)
    
    # Сохраняем
    new_alerts = save_alerts(credit_alerts)
    print(f"   Новых SMS алертов: {len(new_alerts)}")
    return new_alerts


async def send_daily_status():
    """Ежедневная проверка и отправка статуса в 20:00.
    
    Если есть алерты — отправляет их.
    Если нет — отправляет сообщение "Сегодня оповещений не поступало".
    """
    if not TELEGRAM_BOT_TOKEN:
        print("❌ Не задан CONSUMPTION_BOT_TOKEN")
        return
    
    from datetime import datetime
    today = datetime.now().strftime('%d.%m.%Y')
    
    # Инициализируем таблицы
    init_credit_tables()
    
    # Проверяем почту
    print("📧 Проверка email...")
    try:
        run_check()
    except Exception as e:
        print(f"⚠️ Ошибка проверки email: {e}")
    
    # Проверяем SMS
    try:
        await process_sms_alerts()
    except Exception as e:
        print(f"⚠️ Ошибка проверки SMS: {e}")
    
    # Получаем все ожидающие по боевым правилам: >=3 дня / сегодня / просрочено
    pending = get_alerts_ready_for_notification()
    
    if not pending:
        # Нет алертов — отправляем статусное сообщение
        message = f"📋 <b>Ежедневный отчёт ({today})</b>\n\n" \
                  f"Сегодня оповещений о платежах по кредитам и займам " \
                  f"и новым штрафам не поступало."
        await send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, message)
        print("📭 Нет алертов — отправлено статусное сообщение")
        return
    
    # Есть алерты — отправляем заголовок
    header = f"🚨 <b>Ежедневный отчёт ({today})</b>\n\n" \
             f"Найдено {len(pending)} оповещений:\n"
    await send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, header)
    
    print(f"📤 Отправка {len(pending)} уведомлений...")
    
    sent_ids = []
    for alert, kind in pending:
        message = format_alert_message(alert)
        message_id = await send_telegram_message(
            TELEGRAM_BOT_TOKEN, 
            TELEGRAM_CHAT_ID, 
            message,
            reply_markup=build_paid_reply_markup(alert.id)
        )
        if message_id:
            sent_ids.append(alert.id)
            record_notification(alert.id, kind, TELEGRAM_CHAT_ID, str(message_id), is_test=False)
            print(f"✅ Отправлено: {alert.sender_name} - {alert.payment_date} ({kind})")
        else:
            print(f"❌ Не удалось отправить: {alert.sender_name}")
    
    if sent_ids:
        print(f"📤 Всего отправлено: {len(sent_ids)}")


async def send_pending_alerts():
    """Отправляет все ожидающие алерты (устаревшая, используй send_daily_status)."""
    await send_daily_status()


async def send_test_alerts(limit: int = 3):
    """Отправляет тестовые уведомления по ближайшим алертам и помечает их отдельно."""
    if not TELEGRAM_BOT_TOKEN:
        print("❌ Не задан CONSUMPTION_BOT_TOKEN")
        return

    init_credit_tables()
    alerts = get_nearest_alerts(limit=limit)
    if not alerts:
        print("📭 Нет алертов для теста")
        return

    for alert in alerts:
        kind = get_notification_kind(alert) or 'test'
        test_message = "🧪 <b>ТЕСТ</b>\n\n" + format_alert_message(alert)
        message_id = await send_telegram_message(
            TELEGRAM_BOT_TOKEN,
            TELEGRAM_CHAT_ID,
            test_message,
            reply_markup=build_paid_reply_markup(alert.id)
        )
        if message_id:
            record_notification(alert.id, kind, TELEGRAM_CHAT_ID, str(message_id), is_test=True)
            print(f"🧪 Тест отправлен: alert={alert.id} message_id={message_id}")


def main():
    """Точка входа для синхронного вызова."""
    asyncio.run(send_pending_alerts())


if __name__ == '__main__':
    main()
