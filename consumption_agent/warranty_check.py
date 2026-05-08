#!/usr/bin/env python3
"""
Модуль проверки гарантий и генерации алертов.
Фаза 1 — consumption_agent roadmap.

Запуск: python3 warranty_check.py [--notify]
  --notify  отправить уведомления в Telegram
"""
import sqlite3
import sys
import os
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'consumption.db')

# Пороги для алертов
WARRANTY_WARN_DAYS = 30   # предупреждение за 30 дней до конца гарантии
EXPIRY_WARN_DAYS = 7      # предупреждение за 7 дней до конца срока годности

def parse_date(s):
    """Парсинг даты в разных форматах."""
    if not s:
        return None
    for fmt in ('%Y-%m-%d', '%d.%m.%Y', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None

def calc_warranty_until(purchase_date, warranty_months):
    """Расчёт даты окончания гарантии."""
    dt = parse_date(purchase_date)
    if not dt or not warranty_months:
        return None
    return dt + timedelta(days=warranty_months * 30)

def update_warranty_until(conn):
    """Пересчёт warranty_until для всех товаров с warranty_months."""
    rows = conn.execute('''
        SELECT id, purchase_date, warranty_months 
        FROM items 
        WHERE warranty_months IS NOT NULL 
          AND purchase_date IS NOT NULL 
          AND deleted_at IS NULL
          AND (warranty_until IS NULL OR warranty_until = '')
    ''').fetchall()
    
    updated = 0
    for item_id, pdate, wmonths in rows:
        wu = calc_warranty_until(pdate, wmonths)
        if wu:
            conn.execute('UPDATE items SET warranty_until = ? WHERE id = ?',
                         (wu.strftime('%Y-%m-%d'), item_id))
            updated += 1
    
    conn.commit()
    return updated

def check_warranties(conn):
    """Проверка гарантий. Возвращает список алертов."""
    now = datetime.now()
    alerts = []
    
    rows = conn.execute('''
        SELECT id, name, warranty_until, warranty_months, purchase_date
        FROM items 
        WHERE warranty_until IS NOT NULL 
          AND deleted_at IS NULL
          AND status = 'in_use'
    ''').fetchall()
    
    for item_id, name, wu_str, wm, pdate in rows:
        wu = parse_date(wu_str)
        if not wu:
            continue
        
        days_left = (wu - now).days
        
        if days_left < 0:
            alerts.append({
                'item_id': item_id,
                'name': name,
                'type': 'warranty_expired',
                'days': abs(days_left),
                'warranty_until': wu_str,
                'message': f'❌ Гарантия истекла {abs(days_left)}д назад: {name} (до {wu_str})'
            })
        elif days_left <= WARRANTY_WARN_DAYS:
            alerts.append({
                'item_id': item_id,
                'name': name,
                'type': 'warranty_expiring',
                'days': days_left,
                'warranty_until': wu_str,
                'message': f'⚠️ Гарантия истекает через {days_left}д: {name} (до {wu_str})'
            })
    
    return alerts

def check_expiry_dates(conn):
    """Проверка сроков годности."""
    now = datetime.now()
    alerts = []
    
    rows = conn.execute('''
        SELECT id, name, expiry_date
        FROM items 
        WHERE expiry_date IS NOT NULL 
          AND deleted_at IS NULL
          AND status = 'in_use'
    ''').fetchall()
    
    for item_id, name, exp_str in rows:
        exp = parse_date(exp_str)
        if not exp:
            continue
        
        days_left = (exp - now).days
        
        if days_left < 0:
            alerts.append({
                'item_id': item_id,
                'name': name,
                'type': 'expired',
                'days': abs(days_left),
                'message': f'🗑️ Срок годности истёк {abs(days_left)}д назад: {name} (до {exp_str})'
            })
        elif days_left <= EXPIRY_WARN_DAYS:
            alerts.append({
                'item_id': item_id,
                'name': name,
                'type': 'expiring',
                'days': days_left,
                'message': f'⏰ Срок годности через {days_left}д: {name} (до {exp_str})'
            })
    
    return alerts

def save_alerts(conn, alerts):
    """Сохраняет новые алерты в БД, не дублируя существующие."""
    saved = 0
    for a in alerts:
        # Проверяем дубликат
        existing = conn.execute('''
            SELECT id FROM alerts 
            WHERE item_id = ? AND alert_type = ? AND status = 'pending'
        ''', (a['item_id'], a['type'])).fetchone()
        
        if existing:
            continue
        
        conn.execute('''
            INSERT INTO alerts (item_id, alert_type, title, message, scheduled_at, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
        ''', (a['item_id'], a['type'], a['name'], a['message'], datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        saved += 1
    
    conn.commit()
    return saved

def get_warranties_report(conn):
    """Формирует текстовый отчёт по гарантиям для Telegram."""
    now = datetime.now()
    
    rows = conn.execute('''
        SELECT id, name, warranty_until, purchase_date, warranty_months
        FROM items 
        WHERE warranty_until IS NOT NULL 
          AND deleted_at IS NULL
        ORDER BY warranty_until ASC
    ''').fetchall()
    
    if not rows:
        return "📋 Нет товаров с гарантиями."
    
    expired = []
    warning = []
    ok = []
    
    for item_id, name, wu_str, pdate, wm in rows:
        wu = parse_date(wu_str)
        if not wu:
            continue
        days_left = (wu - now).days
        
        line = f"• {name[:45]} — до {wu_str}"
        if days_left < 0:
            expired.append(f"❌ {line} (истекла {abs(days_left)}д назад)")
        elif days_left <= WARRANTY_WARN_DAYS:
            warning.append(f"⚠️ {line} (осталось {days_left}д)")
        else:
            ok.append(f"✅ {line} ({days_left}д)")
    
    parts = ["🔧 *Гарантии*\n"]
    
    if expired:
        parts.append("*Истекшие:*")
        parts.extend(expired)
        parts.append("")
    
    if warning:
        parts.append("*Скоро истекут (< 30 дней):*")
        parts.extend(warning)
        parts.append("")
    
    if ok:
        parts.append("*Активные:*")
        parts.extend(ok)
    
    parts.append(f"\n📊 Всего: {len(rows)} | ❌ {len(expired)} | ⚠️ {len(warning)} | ✅ {len(ok)}")
    
    return "\n".join(parts)

def send_telegram_notification(message, chat_id='1477860192'):
    """Отправка уведомления через Telegram API consumption бота."""
    import urllib.request
    import json
    
    token_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    bot_token = None
    
    # Читаем токен из .env
    if os.path.exists(token_file):
        with open(token_file) as f:
            for line in f:
                if line.startswith('TELEGRAM_BOT_TOKEN='):
                    bot_token = line.split('=', 1)[1].strip().strip('"\'')
    
    # Фоллбэк — из переменных окружения
    if not bot_token:
        bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    
    if not bot_token:
        print("⚠️ TELEGRAM_BOT_TOKEN не найден, уведомление не отправлено")
        return False
    
    url = f'https://api.telegram.org/bot{bot_token}/sendMessage'
    data = json.dumps({
        'chat_id': chat_id,
        'text': message,
        'parse_mode': 'Markdown'
    }).encode()
    
    try:
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        print(f"⚠️ Ошибка отправки: {e}")
        return False

def main():
    notify = '--notify' in sys.argv
    
    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=5000')
    
    # 1. Пересчёт warranty_until
    updated = update_warranty_until(conn)
    if updated:
        print(f"📝 Обновлено warranty_until: {updated} товаров")
    
    # 2. Проверка гарантий
    warranty_alerts = check_warranties(conn)
    expiry_alerts = check_expiry_dates(conn)
    all_alerts = warranty_alerts + expiry_alerts
    
    if all_alerts:
        print(f"\n🔔 Найдено алертов: {len(all_alerts)}")
        for a in all_alerts:
            print(f"  {a['message']}")
        
        saved = save_alerts(conn, all_alerts)
        print(f"💾 Сохранено новых: {saved}")
        
        # 3. Отправка в Telegram
        if notify and all_alerts:
            msg = "🔔 *Уведомления по гарантиям*\n\n"
            msg += "\n".join(a['message'] for a in all_alerts)
            if send_telegram_notification(msg):
                print("📨 Уведомление отправлено в Telegram")
                # Помечаем как отправленные
                for a in all_alerts:
                    conn.execute('''
                        UPDATE alerts SET sent_at = ?, status = 'sent'
                        WHERE item_id = ? AND alert_type = ? AND status = 'pending'
                    ''', (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), a['item_id'], a['type']))
                conn.commit()
    else:
        print("✅ Алертов нет — все гарантии в порядке.")
    
    # 4. Отчёт
    report = get_warranties_report(conn)
    print(f"\n{report}")
    
    conn.close()

if __name__ == '__main__':
    main()
