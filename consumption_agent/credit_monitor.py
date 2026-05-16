#!/usr/bin/env python3
"""
Consumption Agent — мониторинг кредитных платежей.
Проверяет почту и SMS на сообщения от банков/МФО о предстоящих платежах.
Отправляет предупреждения через Telegram bot за 3+ дня до платежа.

Расписание: 10:00 и 18:00 ежедневно
"""
import sqlite3
import os
import re
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Optional, Tuple
import json

from consumption.db import connect as db_connect

DB_PATH = os.path.join(os.path.dirname(__file__), 'consumption.db')

# IMAP конфигурации для всех почт
IMAP_CONFIGS = [
    {
        'name': 'Gmail',
        'host': 'imap.gmail.com',
        'port': 993,
        'user': 'yu.v.artamonov@gmail.com',
        'password': os.getenv('GMAIL_APP_PASSWORD', '').strip('"').replace(' ', ''),
    },
    {
        'name': 'Yandex',
        'host': 'imap.yandex.ru',
        'port': 993,
        'user': 'HKID2021@yandex.ru',
        'password': os.getenv('YANDEX_APP_PASSWORD', '').strip('"').replace(' ', ''),
    },
    {
        'name': 'Mail.ru zorea',
        'host': 'imap.mail.ru',
        'port': 993,
        'user': 'zorea2001@mail.ru',
        'password': os.getenv('MAILRU_ZOREA_PASSWORD', '').strip('"').replace(' ', ''),
    },
    {
        'name': 'Mail.ru neutrinon',
        'host': 'imap.mail.ru',
        'port': 993,
        'user': 'neutrinon@mail.ru',
        'password': os.getenv('MAILRU_NEUTRINON_PASSWORD', '').strip('"').replace(' ', ''),
    },
]

# Паттерны для определения банков и МФО
BANK_PATTERNS = {
    'sberbank': ['сбербанк', 'sberbank', 'сбер'],
    'sovcombank': ['совкомбанк', 'совком'],
    'vtb': ['втб', 'vtb'],
    'tinkoff': ['тинькофф', 'tinkoff', 'т-банк', 't-bank'],
    'alfa': ['альфа-банк', 'альфабанк', 'alfabank', 'alfa-bank'],
}

MFO_PATTERNS = {
    'joy_finance': ['joy finance', 'джой финанс'],
    'turbozaim': ['turbozaim', 'турбозайм'],
    'nebus': ['nebus finance', 'небус'],
    'boostra': ['boostra', 'бустра'],
    'ekvazaim': ['эквазайм', 'ekvazaim'],
    'webzaim': ['webzaim', 'вебзайм'],
    'dengi_srazu': ['деньги сразу', 'dengisrazu', 'dengi-srazu'],
}

ALL_PATTERNS = {**BANK_PATTERNS, **MFO_PATTERNS}

# Паттерны для извлечения даты и суммы платежа
DATE_PATTERNS = [
    # "ближайший платёж 15.05.2026" / "до 15.05.2026"
    r'(?:плат[её]ж|взнос|списание|до|не позднее|не позже).{0,30}(\d{1,2}[.\/]\d{1,2}[.\/]\d{2,4})',
    # "оплатите до 15 мая" / "до 15 мая 2026 г."
    r'(?:до|не позднее|не позже).{0,10}(\d{1,2}\s+[а-яa-z]+(?:\s+\d{2,4})?)',
    # "15.05.2026 спишется" / "15 мая 2026 г."
    r'(\d{1,2}[.\/]\d{1,2}[.\/]?\d{0,4})\s*(?:спишется|плат[её]ж|взнос|дата платежа)',
    # "дата платежа: 15.05.2026"
    r'(?:дата платежа|срок платежа|оплатить до).{0,20}(\d{1,2}[.\/]\d{1,2}[.\/]?\d{0,4})',
    # Turbozaim: "следующий платёж 15 мая"
    r'(?:следующий|очередной|предстоящий).{0,20}плат[её]ж.{0,20}(\d{1,2}\s+[а-яa-z]+)',
]

AMOUNT_PATTERNS = [
    # "сумма 1500.50 руб" / "к оплате 1 500,00 ₽"
    r'(?:сумма|плат[её]ж|взнос|списание|к оплате|оплатить|внести).{0,20}(\d+[\s\d]*,?\d*)\s*(?:руб|₽|RUB|рублей)',
    # "1500.50 рублей" / "1 500,00 ₽"
    r'(\d+[\s\d]*,?\d*)\s*(?:руб|₽|RUB|рублей)',
    # "оплатите 1 500,50" / "внесите 1500 руб"
    r'(?:оплатите|внесите|переведите|заплатите).{0,10}(\d+[\s\d]*,?\d*)',
    # Turbozaim: "Сумма платежа: 1 500,00 руб"
    r'(?:сумма платежа|размер платежа|к оплате).{0,20}(\d+[\s\d]*,?\d*)\s*(?:руб|₽|RUB)',
    # Сбербанк: "Сумма к оплате 1500.00 RUB"
    r'(?:сумма к оплате|итого к оплате).{0,20}(\d+[.\d]*)\s*RUB',
]

# Ключевые слова для определения кредитных сообщений
CREDIT_KEYWORDS = [
    'кредит', 'займ', 'микрозайм', 'плат[её]ж', 'взнос',
    'списание', 'погашение', 'задолженность', 'долг',
    'просрочка', 'неустойка', 'штраф', 'пени',
    'ежемесячный', 'аннуитетный', 'график платежей',
]


@dataclass
class CreditAlert:
    """Структура для хранения информации о кредитном платеже."""
    id: Optional[int] = None
    source: str = ''  # 'email' или 'sms'
    sender: str = ''  # email-адрес или номер телефона
    sender_name: str = ''  # Название банка/МФО
    subject: str = ''  # Тема письма или текст SMS
    body: str = ''  # Тело сообщения
    payment_date: Optional[datetime] = None
    payment_amount: Optional[float] = None
    currency: str = 'RUB'
    detected_at: Optional[datetime] = None
    notified_at: Optional[datetime] = None
    days_until_payment: Optional[int] = None
    raw_message_id: str = ''  # ID письма в IMAP
    paid_confirmed_at: Optional[datetime] = None
    paid_confirmed_via: str = ''


def _db_connect():
    """Connect through the shared SQLite helper."""
    return db_connect(DB_PATH, timeout=10, max_retries=3, delay=0.5)


def init_credit_tables():
    """Создаёт таблицы для хранения кредитных алертов."""
    conn = _db_connect()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS credit_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,  -- 'email' или 'sms'
            sender TEXT,
            sender_name TEXT,  -- Название банка/МФО
            subject TEXT,
            body TEXT,
            payment_date TEXT,
            payment_amount REAL,
            currency TEXT DEFAULT 'RUB',
            detected_at TEXT DEFAULT (datetime('now')),
            notified_at TEXT,
            days_until_payment INTEGER,
            raw_message_id TEXT UNIQUE,
            is_active INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS credit_alert_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id INTEGER NOT NULL REFERENCES credit_alerts(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            telegram_chat_id TEXT,
            telegram_message_id TEXT,
            is_test INTEGER DEFAULT 0,
            sent_at TEXT DEFAULT (datetime('now')),
            UNIQUE(alert_id, kind, is_test)
        );
        
        CREATE INDEX IF NOT EXISTS idx_credit_alerts_date 
        ON credit_alerts(payment_date);
        
        CREATE INDEX IF NOT EXISTS idx_credit_alerts_notified 
        ON credit_alerts(notified_at);
        
        CREATE INDEX IF NOT EXISTS idx_credit_alerts_active 
        ON credit_alerts(is_active);
    ''')

    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(credit_alerts)").fetchall()}
    if 'paid_confirmed_at' not in existing_columns:
        conn.execute('ALTER TABLE credit_alerts ADD COLUMN paid_confirmed_at TEXT')
    if 'paid_confirmed_via' not in existing_columns:
        conn.execute('ALTER TABLE credit_alerts ADD COLUMN paid_confirmed_via TEXT')
    if 'paid_note' not in existing_columns:
        conn.execute('ALTER TABLE credit_alerts ADD COLUMN paid_note TEXT')

    conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_credit_alert_notifications_alert
        ON credit_alert_notifications(alert_id, kind, is_test)
    ''')
    conn.commit()
    conn.close()


def _compute_days_until(payment_date: Optional[datetime]) -> Optional[int]:
    if not payment_date:
        return None
    return (payment_date.date() - datetime.now().date()).days


def _row_to_alert(row: sqlite3.Row) -> CreditAlert:
    payment_date = datetime.strptime(row['payment_date'], '%Y-%m-%d') if row['payment_date'] else None
    detected_at = datetime.strptime(row['detected_at'], '%Y-%m-%d %H:%M:%S') if row['detected_at'] else None
    paid_confirmed_at = (
        datetime.strptime(row['paid_confirmed_at'], '%Y-%m-%d %H:%M:%S')
        if row['paid_confirmed_at'] else None
    )
    return CreditAlert(
        id=row['id'],
        source=row['source'],
        sender=row['sender'],
        sender_name=row['sender_name'],
        subject=row['subject'],
        body=row['body'],
        payment_date=payment_date,
        payment_amount=row['payment_amount'],
        currency=row['currency'],
        detected_at=detected_at,
        notified_at=(
            datetime.strptime(row['notified_at'], '%Y-%m-%d %H:%M:%S')
            if row['notified_at'] else None
        ),
        days_until_payment=_compute_days_until(payment_date),
        raw_message_id=row['raw_message_id'],
        paid_confirmed_at=paid_confirmed_at,
        paid_confirmed_via=row['paid_confirmed_via'] or '',
    )


def get_notification_kind(alert: CreditAlert) -> Optional[str]:
    days_until = _compute_days_until(alert.payment_date)
    alert.days_until_payment = days_until
    if days_until is None:
        return None
    if days_until < 0:
        return 'overdue'
    if days_until == 0:
        return 'due'
    if days_until >= 3:
        return 'advance'
    return None


def record_notification(alert_id: int, kind: str, telegram_chat_id: Optional[str] = None,
                        telegram_message_id: Optional[str] = None, is_test: bool = False):
    conn = _db_connect()
    conn.execute('''
        INSERT OR IGNORE INTO credit_alert_notifications
        (alert_id, kind, telegram_chat_id, telegram_message_id, is_test)
        VALUES (?, ?, ?, ?, ?)
    ''', (alert_id, kind, telegram_chat_id, telegram_message_id, 1 if is_test else 0))
    if not is_test:
        conn.execute(
            'UPDATE credit_alerts SET notified_at = datetime("now") WHERE id = ?',
            (alert_id,)
        )
    conn.commit()
    conn.close()


def was_notification_sent(alert_id: int, kind: str, is_test: bool = False) -> bool:
    conn = _db_connect()
    row = conn.execute(
        'SELECT 1 FROM credit_alert_notifications WHERE alert_id = ? AND kind = ? AND is_test = ? LIMIT 1',
        (alert_id, kind, 1 if is_test else 0)
    ).fetchone()
    conn.close()
    return bool(row)


def get_alert_by_id(alert_id: int) -> Optional[CreditAlert]:
    conn = _db_connect()
    conn.row_factory = sqlite3.Row
    row = conn.execute('SELECT * FROM credit_alerts WHERE id = ?', (alert_id,)).fetchone()
    conn.close()
    return _row_to_alert(row) if row else None


def confirm_alert_paid(alert_id: int, via: str = 'telegram_button', note: str = '') -> bool:
    conn = _db_connect()
    cur = conn.execute('''
        UPDATE credit_alerts
        SET paid_confirmed_at = datetime('now'),
            paid_confirmed_via = ?,
            paid_note = ?,
            is_active = 0
        WHERE id = ? AND paid_confirmed_at IS NULL
    ''', (via, note, alert_id))
    conn.commit()
    changed = cur.rowcount > 0
    conn.close()
    return changed


def get_alerts_ready_for_notification() -> List[Tuple[CreditAlert, str]]:
    init_credit_tables()
    conn = _db_connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute('''
        SELECT * FROM credit_alerts
        WHERE is_active = 1
          AND payment_date IS NOT NULL
          AND paid_confirmed_at IS NULL
        ORDER BY payment_date ASC
    ''').fetchall()
    conn.close()

    result: List[Tuple[CreditAlert, str]] = []
    for row in rows:
        alert = _row_to_alert(row)
        kind = get_notification_kind(alert)
        if not kind:
            continue
        if was_notification_sent(alert.id, kind):
            continue
        result.append((alert, kind))
    return result


def get_nearest_alerts(limit: int = 3) -> List[CreditAlert]:
    init_credit_tables()
    conn = _db_connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute('''
        SELECT * FROM credit_alerts
        WHERE payment_date IS NOT NULL
          AND is_active = 1
          AND paid_confirmed_at IS NULL
        ORDER BY ABS(julianday(payment_date) - julianday('now')) ASC, payment_date ASC
        LIMIT ?
    ''', (limit,)).fetchall()
    conn.close()
    alerts = [_row_to_alert(row) for row in rows]
    for alert in alerts:
        alert.days_until_payment = _compute_days_until(alert.payment_date)
    return alerts


def decode_subject(msg) -> str:
    """Декодирует тему письма."""
    subj = msg['Subject']
    if subj is None:
        return ''
    decoded = decode_header(subj)
    result = []
    for part, enc in decoded:
        if isinstance(part, bytes):
            try:
                result.append(part.decode(enc or 'utf-8', errors='replace'))
            except LookupError:
                result.append(part.decode('utf-8', errors='replace'))
        else:
            result.append(str(part))
    return ''.join(result)


def get_email_body(msg) -> str:
    """Извлекает текст из тела письма (предпочитает HTML для банковских писем)."""
    body = ''
    html_body = ''
    
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == 'text/plain':
                try:
                    body = part.get_payload(decode=True).decode('utf-8', errors='replace')
                except:
                    pass
            elif content_type == 'text/html':
                try:
                    html_body = part.get_payload(decode=True).decode('utf-8', errors='replace')
                except:
                    pass
    else:
        try:
            body = msg.get_payload(decode=True).decode('utf-8', errors='replace')
        except:
            pass
    
    # Для банковских писем используем HTML (там больше данных)
    if html_body:
        # Очистка HTML с помощью BeautifulSoup
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_body, 'html.parser')
            # Удаляем скрипты и стили
            for script in soup(['script', 'style']):
                script.decompose()
            text = soup.get_text()
            # Убираем лишние пробелы
            text = re.sub(r'\s+', ' ', text).strip()
            return text
        except ImportError:
            # Fallback на простую очистку
            text = re.sub(r'<[^>]+>', ' ', html_body)
            text = re.sub(r'\s+', ' ', text).strip()
            return text
    
    return body or html_body


def detect_sender_name(text: str) -> Tuple[str, str]:
    """Определяет название банка/МФО по тексту сообщения."""
    text_lower = text.lower()
    
    for bank_id, patterns in ALL_PATTERNS.items():
        for pattern in patterns:
            if pattern in text_lower:
                return bank_id, pattern
    
    return 'unknown', ''


def extract_payment_date(text: str) -> Optional[datetime]:
    """Извлекает дату платежа из текста."""
    text_lower = text.lower()
    
    # Месяцы для парсинга
    months = {
        'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4,
        'мая': 5, 'июня': 6, 'июля': 7, 'августа': 8,
        'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12,
    }
    
    for pattern in DATE_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            date_str = match.group(1)
            try:
                # Формат DD.MM.YYYY или DD/MM/YYYY
                if '.' in date_str or '/' in date_str:
                    parts = re.split(r'[.\/]', date_str)
                    day = int(parts[0])
                    month = int(parts[1])
                    year = int(parts[2]) if len(parts) > 2 and len(parts[2]) == 4 else 2000 + int(parts[2])
                    return datetime(year, month, day)
                # Формат "15 мая"
                else:
                    parts = date_str.split()
                    day = int(parts[0])
                    month_name = parts[1].lower()
                    month = months.get(month_name, datetime.now().month)
                    year = datetime.now().year
                    # Если месяц уже прошёл в этом году, берём следующий год
                    if month < datetime.now().month:
                        year += 1
                    return datetime(year, month, day)
            except (ValueError, IndexError):
                continue
    
    return None


def extract_payment_amount(text: str) -> Optional[float]:
    """Извлекает сумму платежа из текста."""
    for pattern in AMOUNT_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            amount_str = match.group(1)
            try:
                # Убираем пробелы и заменяем запятую на точку
                amount_str = amount_str.replace(' ', '').replace('\xa0', '').replace(',', '.')
                return float(amount_str)
            except ValueError:
                continue
    
    return None


# Маркетинговые слова — исключаем письма с этими терминами
MARKETING_KEYWORDS = [
    'скидка', 'кэшбэк', 'кешбэк', 'подарок', 'акция', 'промокод',
    'выгода', 'экономьте', 'бонус', 'кешбек', 'кэшбек',
    'страховка жилья', 'страхование', 'полис',
    'самокат', 'доставка', 'город мчит',
    'топливо', 'заправка', 'азс',
    'пицца', 'шашлык', 'майские',
    'кэшбэк', 'кешбэк', 'cashback',
    'приз', 'розыгрыш', 'конкурс',
    'карта лояльности', 'баллы',
    'реферал', 'пригласи друга',
    'новый сервис', 'попробуйте',
    'успейте', 'только сегодня', 'последний шанс',
    'бесплатная доставка', 'бесплатно',
    'кешбэк', 'кэшбек',
]

# Обязательные признаки реального кредитного уведомления
CREDIT_REQUIRED_INDICATORS = [
    'плат[её]ж', 'взнос', 'списание', 'погашение',
    'задолженность', 'долг', 'просрочка',
    'неустойка', 'штраф', 'пени',
    'график платежей', 'аннуитетный',
    'сумма к оплате', 'к оплате',
    'платеж по кредиту', 'погашение кредита',
    'задолженность по кредиту',
    'просроченный платеж',
]

def is_credit_message(subject: str, body: str) -> bool:
    """Проверяет, является ли сообщение кредитным уведомлением (не маркетингом).
    
    Логика:
    1. Если есть маркетинговые слова — исключаем
    2. Если есть обязательные признаки кредита — включаем
    3. Если есть ключевые слова кредита + контекст оплаты — включаем
    4. Иначе — исключаем (дефолтно False)
    """
    text = (subject + ' ' + body).lower()
    
    # 1. Маркетинг — строго исключаем
    for mk in MARKETING_KEYWORDS:
        if mk in text:
            return False
    
    # 2. Обязательные признаки реального уведомления
    for indicator in CREDIT_REQUIRED_INDICATORS:
        if re.search(indicator, text):
            return True
    
    # 3. Ключевые слова кредита — только если есть контекст оплаты/долга
    credit_keywords_found = False
    for keyword in ['кредит', 'займ', 'микрозайм']:
        if re.search(keyword, text):
            credit_keywords_found = True
            break
    
    if credit_keywords_found:
        # Проверяем контекст — должны быть слова об оплате/долге
        payment_context = ['оплат', 'плат', 'долг', 'задолжен', 'взнос', 'списан']
        for ctx in payment_context:
            if ctx in text:
                return True
    
    # 4. По умолчанию — не кредитное (избегаем false positive)
    return False


def check_email_account(config: dict, days_back: int = 1) -> List[CreditAlert]:
    """Проверяет почтовый ящик на кредитные сообщения."""
    alerts = []
    
    if not config.get('password'):
        print(f"⚠️ Нет пароля для {config['name']}")
        return alerts
    
    try:
        import socket
        # Устанавливаем таймаут на сокет
        socket.setdefaulttimeout(15)
        
        mail = imaplib.IMAP4_SSL(config['host'], config['port'])
        # Для Gmail нужно использовать полный email как логин
        login_user = config['user']
        # Убираем пробелы из пароля (для app password)
        password_clean = config['password'].replace(' ', '')
        mail.login(login_user, password_clean)
        mail.select('INBOX')
        
        # Ищем письма ТОЛЬКО за текущий день (ON)
        on_date = datetime.now().strftime('%d-%b-%Y')
        
        # Сначала пробуем только непрочитанные (быстро)
        _, message_numbers = mail.search(None, f'(ON {on_date} UNSEEN)')
        nums = message_numbers[0].split()
        
        # Если непрочитанных нет — ищем все за сегодня
        if not nums:
            _, message_numbers = mail.search(None, f'(ON {on_date})')
            nums = message_numbers[0].split()
            print(f"   Писем за сегодня (все): {len(nums)}")
        else:
            print(f"   Писем за сегодня (непрочитанных): {len(nums)}")
        
        for num in nums[:100]:  # Ограничиваем 100 письмами для скорости
            try:
                _, msg_data = mail.fetch(num, '(RFC822)')
                msg = email.message_from_bytes(msg_data[0][1])
                
                subject = decode_subject(msg)
                body = get_email_body(msg)
                sender = msg.get('From', '')
                message_id = msg.get('Message-ID', '')
                
                if is_credit_message(subject, body):
                    bank_id, bank_name = detect_sender_name(subject + ' ' + body)
                    payment_date = extract_payment_date(subject + ' ' + body)
                    payment_amount = extract_payment_amount(subject + ' ' + body)
                    
                    alert = CreditAlert(
                        source='email',
                        sender=sender,
                        sender_name=bank_id,
                        subject=subject,
                        body=body[:500],  # Ограничиваем длину
                        payment_date=payment_date,
                        payment_amount=payment_amount,
                        raw_message_id=message_id,
                    )
                    alerts.append(alert)
            except Exception as e:
                print(f"⚠️ Ошибка обработки письма: {e}")
                continue
        
        mail.close()
        mail.logout()
        
    except socket.timeout:
        print(f"⏱️ Таймаут подключения к {config['name']}")
    except Exception as e:
        print(f"❌ Ошибка подключения к {config['name']}: {e}")
    finally:
        socket.setdefaulttimeout(None)
    
    return alerts


def save_alerts(alerts: List[CreditAlert]) -> List[CreditAlert]:
    """Сохраняет алерты в БД, возвращает только новые."""
    new_alerts = []
    
    conn = _db_connect()
    
    for alert in alerts:
        # Проверяем, не существует ли уже такой алерт
        existing = conn.execute(
            'SELECT id FROM credit_alerts WHERE raw_message_id = ?',
            (alert.raw_message_id,)
        ).fetchone()
        
        if existing:
            continue
        
        # Вычисляем дни до платежа
        days_until = None
        if alert.payment_date:
            days_until = (alert.payment_date - datetime.now()).days
        
        conn.execute('''
            INSERT INTO credit_alerts 
            (source, sender, sender_name, subject, body, payment_date, 
             payment_amount, currency, detected_at, days_until_payment, raw_message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?)
        ''', (
            alert.source, alert.sender, alert.sender_name, alert.subject,
            alert.body, 
            alert.payment_date.strftime('%Y-%m-%d') if alert.payment_date else None,
            alert.payment_amount, alert.currency, days_until, alert.raw_message_id
        ))
        
        alert.days_until_payment = days_until
        new_alerts.append(alert)
    
    conn.commit()
    conn.close()
    
    return new_alerts


def get_pending_alerts(min_days: int = 3) -> List[CreditAlert]:
    """Получает алерты, по которым ещё не отправлено уведомление и платёж через min_days+ дней."""
    conn = _db_connect()
    conn.row_factory = sqlite3.Row
    
    rows = conn.execute('''
        SELECT * FROM credit_alerts 
        WHERE is_active = 1 
        AND paid_confirmed_at IS NULL
        AND notified_at IS NULL
        AND payment_date IS NOT NULL
        ORDER BY payment_date ASC
    ''').fetchall()

    alerts = []
    for row in rows:
        alert = _row_to_alert(row)
        if alert.days_until_payment is not None and alert.days_until_payment >= min_days:
            alerts.append(alert)
    
    conn.close()
    return alerts


def mark_notified(alert_ids: List[int]):
    """Отмечает алерты как отправленные."""
    conn = _db_connect()
    for alert_id in alert_ids:
        conn.execute(
            'UPDATE credit_alerts SET notified_at = datetime("now") WHERE id = ?',
            (alert_id,)
        )
    conn.commit()
    conn.close()


def format_alert_message(alert: CreditAlert) -> str:
    """Форматирует сообщение для Telegram."""
    sender_display = alert.sender_name if alert.sender_name != 'unknown' else 'Банк/МФО'
    days_until = _compute_days_until(alert.payment_date)
    alert.days_until_payment = days_until

    msg = f"⚠️ <b>Кредитный платёж</b>\n\n"
    msg += f"🆔 Alert #{alert.id}\n"
    msg += f"🏦 <b>{sender_display}</b>\n"
    
    if alert.payment_date:
        msg += f"📅 Дата: {alert.payment_date.strftime('%d.%m.%Y')}\n"
        if days_until is not None:
            if days_until > 0:
                msg += f"⏰ Осталось: {days_until} дн.\n"
            elif days_until == 0:
                msg += f"⏰ Срок: сегодня\n"
            else:
                msg += f"⏰ Просрочено: {abs(days_until)} дн.\n"
    
    if alert.payment_amount is not None:
        msg += f"💰 Сумма: {alert.payment_amount:,.2f} {alert.currency}\n"

    if alert.paid_confirmed_at:
        msg += f"✅ Отмечено как оплачено\n"
    
    if alert.subject:
        msg += f"\n📝 {alert.subject[:200]}"
    
    return msg


def check_all_emails() -> List[CreditAlert]:
    """Проверяет все почтовые ящики."""
    all_alerts = []
    
    for config in IMAP_CONFIGS:
        print(f"📧 Проверяем {config['name']}...")
        alerts = check_email_account(config, days_back=1)
        print(f"   Найдено {len(alerts)} кредитных сообщений")
        all_alerts.extend(alerts)
    
    return all_alerts


def run_check():
    """Основная функция проверки."""
    print(f"🔍 Проверка кредитных платежей: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    
    # Инициализируем таблицы
    init_credit_tables()
    
    # Проверяем почту
    alerts = check_all_emails()
    
    # Сохраняем новые алерты
    new_alerts = save_alerts(alerts)
    print(f"✅ Новых алертов: {len(new_alerts)}")
    
    # Получаем ожидающие уведомления (за 3+ дня)
    pending = get_pending_alerts(min_days=3)
    print(f"📋 Ожидают уведомления: {len(pending)}")
    
    return pending


if __name__ == '__main__':
    pending = run_check()
    for alert in pending:
        print(format_alert_message(alert))
        print("---")
