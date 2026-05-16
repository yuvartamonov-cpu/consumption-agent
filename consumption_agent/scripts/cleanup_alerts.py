#!/usr/bin/env python3
"""
Очистка credit_alerts от некредитных уведомлений.

Анализирует источник, отправителя, тему и сумму — 
и деактивирует алерты, которые не являются кредитными платежами.

Запуск: python3 cleanup_alerts.py [--db PATH] [--dry-run]
"""

import argparse
import os
import re
import sqlite3
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(SCRIPT_DIR, '..') if os.path.basename(SCRIPT_DIR) == 'scripts' else SCRIPT_DIR

# ─────────────────────────────────────────────────────────────
# 1. Правила фильтрации
# ─────────────────────────────────────────────────────────────

# Банки/МФО, которые реально отправляют кредитные уведомления
# Банки и МФО, от которых могут приходить кредитные уведомления
# Реальные банки и МФО, от которых приходят уведомления о кредитах
CREDIT_SENDERS = {
    'sberbank', 'vtb', 'tinkoff', 'alfa',
    'sovcombank', 'raiffeisen', 'gazprombank', 'otkritie',
    'rosbank', 'uralsib', 'homecredit', 'rencredit',
    'pochtabank', 'akbars', 'absolut', 'mdm',
    'turbozaim', 'joy_finance', 'nebus',
    'ekvazaim', 'webzaim', 'dengi_srazu',
}

# Спам-МФО (не настоящие напоминания, а рекламный спам)
SPAM_MFO_SENDERS = {
    'iswis', 'iswis.ru', 'kapytal', 'kapytal.ru', 'c-m0ney', 'c-m0ney.ru',
    'speedcr', 'speedcrru', 'l0anpay', 'l0anpayru', 'l0anpay.ru',
    'hotloan', 'hotloan.ru', 'bistroz', 'bistroz.ru', 'webzaim', 'web-zaim',
    'iamzaem', 'iamzaem.ru', 'zaymer', 'vivus', 'my-cred', 'my-cred.ru',
    'fingis', 'fingis.ru', 'banki.ru', 'atb', 'bankzenit', 'bankzenit',
    'best-loan', 'best-loan.ru', 'stol-zaym', 'stol-zaym.ru',
    'techrub', 'techrub.ru', 'cred-rf', 'cred-rf.ru', 'rsb.ru',
    'uralsib', 'gazprombank', 'dk-pay', 'dk-pay.ru', 'ekapusta', 'ekapusta.ru',
    'gostzaim', 'gostzaim.ru', 'greenmoney', 'mon-now', 'mon-nowru',
    'fast-tut', 'fast-tut.ru', 'easycred', 'easycred.ru', 'rsb.ru',
    'mfcalfafin',  # тоже рекламный спам, не реальный кредит
    'beeline', 'beelineofd', 't-mob', 'atb', 'bankzenit', 'gazprombank',
    'uralsib', 'rsb', 'rsb.ru', '0919', 'rsb.ru', 'unknown',
    'boostra', 't-bank', 'tinkoff_non_reminder',
}

# Отправители, которые НЕ являются банками (ложная детекция)
NOT_BANK_FROM = {
    'iswis.ru', 'kapytal.ru', 'c-m0ney.ru', 'speedcrru', 'l0anpayru',
    'hotloan.ru', 'bistroz.ru', 'iamzaem.ru', 'my-cred.ru',
    'domclick', 'beeline', 'sberid',
}

# Известные рекламные/информационные темы (регистронезависимо)
AD_SUBJECT_PATTERNS = [
    # Реклама и маркетинг
    r'акци[яи]',
    r'кешбэк',
    r'дарим\s+(деньги|подарк)',
    r'до\s+[\d.]+\s*%',
    r'\d+%\s+годовых',
    r'скидк[аи]',
    r'повышенн(?:ая|ой)\s+ставк',
    r'рассрочк[аи]',
    r'международн(?:ая|ой)\s+карт',
    r'путешеств',
    r'делим\s+\d',
    r'разделим\s+\d',
    r'шопинг',
    r'самокат',
    r'\d+\s+руб\w*\s+за',
    r'кэшбэк\s+до\s+\d+%',
    r'кешбэк\s+\d+%\s+в\s+',
    r'кешбэк\s+\d+%',

    # Путешествия (не кредит)
    r'osaka|travel\s+guide|holidays|weekend',
    r'туристическ',

    # Поздравления и праздники
    r'дн[её]м\s+победы',
    r'с\s+праздник',
    r'поздравл',

    # Уведомления о доставке / заказах
    r'order\s+update',
    r'ваш\s+заказ.*уже',
    r'новая\s+дата\s+доставк',
    r'деревянн(?:ый|ого)\s+конструктор',
    r'культурн(?:ый|ого)\s+шок',

    # Подписки
    r'доступ.*plus\s+скоро\s+закончится',
    r'обновит[еь]\s+способ\s+оплаты',
    r'your\s+account\s+service.*not\s+been\s+activated',
    r'limited-time\s+coupon',

    # Отзывы и госуслуги
    r'отзыв\s+по\s+процесс',
    r'цифровой\s+паспорт',
    r'получение\s+сведений.*каталог.*кредитных\s+историй',
    r'статус\s+операции\s+№',
    r'домклик',
    r'штраф',
    r'оплат[аы]\s+прошл[аа]\s+успешн',
    r'оплата\s+прошла\s+успешно',
    r'у\s+вас\s+новый\s+штраф',
    r'штраф\s+отменён',
    r'штраф\s+оплачен',
    r'пришёл\s+штраф',
    r'счёт\s+на\s+оплату',
    r'счёт\s+оплачен',
    r'вам\s+пришел\s+новый\s+счет',
    r'результат\s+платежа',
    r'информаци[яя]\s+о\s+платеже',
    r'квитанци[яя].*google\s+play',
    r'квитанция.*east',

    # Чеки (не кредит)
    r'^чек\b',
    r'кассовый\s+чек',
    r'чека\s+\+\s+\(\d+\)\s+подарок',

    # Roadmap / отчёты consumption_agent
    r'consumption\s+agent',
    r'roadmap',
    r'архитектур',
    r'отчёт\s+о\s+проделанн',
    r'каршеринг',

    # Умный Ритейл/чеки магазинов
    r'умный\s+ритейл',
    r'центр\s+программ\s+лояльности',
    r'east\.ru',
    r'квитанция\s+от',

    # Прочее (явный не-кредит)
    r'важн(?:ая|ое)\s+информаци',
    r'ваш\s+доступ',
    r'выплата\s+выигрыш',
    r'ознакомьтесь\s+со\s+счётом',
    r'до\s+майских',
    r'сдач[аа]\s+квартиры',
    r'готовы\s+к\s+майским',
    r'без\s+этого\s+(?:на\s+)?майские',
    r'кешбэк\s+\d+%\s+в\s+топлив',
    r'лечени[ея]\s+и\s+поддержк',
    r'информаци[яя]\s+о\s+платеже\s+\d+',
]

# Суммы, которые явно нереальны для кредита (< 100 ₽ — чеки, > 5 млн — реклама)
SUSPICIOUS_AMOUNT_MAX = 5_000_000  # больше — явно реклама
SUSPICIOUS_AMOUNT_MIN = 100        # меньше — чек, а не кредит

# Категории для маркировки
class Category:
    REAL_CREDIT = 'credit'
    AD = 'advertising'
    RECEIPT = 'receipt'
    SUBSCRIPTION = 'subscription'
    GOV = 'government'
    ROADMAP = 'roadmap'
    UNKNOWN = 'unknown'


# ─────────────────────────────────────────────────────────────
# 2. SMS-специфичные паттерны
# ─────────────────────────────────────────────────────────────

# Паттерны НАСТОЯЩЕГО кредитного платежа (reminder с датой)
CREDIT_REMINDER_PATTERNS = [
    r'не\s+забудьте\s+внести',          # alfa: Не забудьте внести 613.19 RUR по кредитке
    r'внесите(?:\s+очередн[уы]ю)?\s+оплат',    # turbozaim: Внесите очередную оплату по займу
    r'дата\s+платежа[!.]',                      # joymoney: Сегодня дата платежа!
    r'к\s+оплате[!.:]',                          # joymoney: К оплате:
    r'внесите\s+плат[её]ж',                  # vtb: Внесите платеж по кредитке
    r'спишем\s+\d{1,3}(?:[\s\u00A0]?\d{3})*.*не\s+забудьте',  # alfa: спишем X. Не забудьте
    r'внесите\s+по\s+кредит',
    r'плат[её]ж\s+по\s+займ',            # alfa finance
    r'плат[её]ж\s+по\s+кредитке',
    r'очередн[оа]го\s+платеж[аа]',
    r'не\s+допустить\s+просрочку',       # sber: не допустить просрочку
]

# Паттерны 2FA/коды подтверждения — НЕ кредит
CODE_PATTERNS = [
    r'код[\s:]+\d{4,6}',
    r'код\s+для\s+вход',
    r'никому\s+не\s+сообщай',
    r'введите\s+код',
    r'проверочн[ыо]й?\s+код',
    r'для\s+подтвержден',
    r'code[\s:]+\d{4,8}',
    r'код\s+подтверждени',
    r'@id\.sber',
]

# Паттерны ОБЫЧНЫХ банковских оповещений (покупки, переводы, баланс)
BANK_NOTIFICATION_PATTERNS = [
    r'счёт\s+карты',           # Счёт карты MIR-XXXX
    r'сч[её]т\d{4}',           # СЧЁТ1374
    r'покупк[аи]\s',            # Покупка
    r'перевод\s',               # перевод на/от
    r'по\s+сбп',               # по СБП
    r'списание',
    r'зачисление',
    r'оплат[аы]\s',            # Оплата
    r'баланс[\s:]',
    r'недостаточно\s+средств',
    r'комиссия',
    r'отклон[её]н',
    r'заблокировали\s+перевод',
    r'приостановил',
    r'защит[аы]+\s+клиентов',
    r'мошенничеств',
    r'не\s+дозвонили',
    r'пополнен[ао]?\s+на',
    r'счет\s+\*\d+\s+пополнен',
    r'получите\s+до\s+[^%]+без\s+%',  # alfa: получите до 30 000₽ без %
    r'(?:пришел=|пришлем).*посоветуйте',  # t-bank: пришлем X р. посоветуйте кредитку
]

# Паттерны заявок на кредит (не напоминание)
CREDIT_APPLICATION_PATTERNS = [
    r'заявк[ау]\s+на\s+ипотек',
    r'заявк[ау]\s+на\s+кредит',
    r'одобрить\s+вам\s+кредит',
    r'статус\s+заявк',
    r'решение\s+по\s+заявк',
    r'отрицательное\s+решение',
    r'проверьте\s+статус',
    r'продолжите\s+заполнение',
    r'оформление\s+заявк',
]

# Паттерны подписок
SUBSCRIPTION_PATTERNS = [
    r'подписк[ау]',
    r'закончилась',
    r'продолжить\s+пользоваться',
]

# Паттерны спам-МФО (рекламный спам)
SPAM_MFO_PATTERNS = [
    r'готовы\s+перевест',
    r'(?:готовы|одобрен).*(?:на\s+карту|к\s+переводу)',
    r'заберите',
    r'получите\s+(?:до\s+)?\d{4,}',
    r'(?:выдача|кредит|заём|займ).*подтвержден',
    r'беспроцентн',
    r'на\s+любые\s+цели',
    r'предложени[яе]\s+по\s+(?:кредит|займ)',
    r'деньги\s+на\s+карту',
    r'мгновенно\s+на\s+карту',
    r'источник\s+денег',
    r'получите\s+деньги',
    r'успейте\s+взять',
    r'ваш\s+займ\s+готов',
    r'оформление\.?\s+получить',
    r'займы\s+на\s+разные\s+цели',
    r'попробуйте\s+кредитную\s+карту',
    r'получите\s+ссылк',
    r'cc\.|сlk\.|bee\.|beel\.ink',  # сокращатели ссылок спамеров
]


def is_sms_category(sender_lower, subject_lower, body_lower, combined, amount):
    """
    Определяет категорию SMS-сообщения от банка.
    Возвращает 'credit', 'ad', 'unknown' или None если не SMS-специфичный.
    """
    # Если sender не в банках — не наша логика
    if sender_lower not in CREDIT_SENDERS:
        return None

    # 1. Коды подтверждения — никогда не кредит
    for pat in CODE_PATTERNS:
        if re.search(pat, combined):
            return Category.AD

    # 2. Настоящее напоминание о кредитном платеже — приоритет
    for pat in CREDIT_REMINDER_PATTERNS:
        if re.search(pat, combined):
            return Category.REAL_CREDIT

    # 3. Обычные банковские оповещения (покупки, переводы, баланс) — не кредит
    for pat in BANK_NOTIFICATION_PATTERNS:
        if re.search(pat, combined):
            return Category.AD

    # 4. Заявки на кредит (не напоминание, а процесс) — не кредит
    for pat in CREDIT_APPLICATION_PATTERNS:
        if re.search(pat, combined):
            return Category.AD

    # 5. Подписки — не кредит
    for pat in SUBSCRIPTION_PATTERNS:
        if re.search(pat, combined):
            return Category.AD

    # 6. Если остальное — проверяем по времени: платежи напоминают о себе
    # Настоящий кредит всегда содержит дату платежа
    date_patterns = [r'до\s+\d', r'\d{1,2}\s+[а-я]+', r'\d{2}\.\d{2}\.\d{2,4}']
    has_date = any(re.search(pat, combined) for pat in date_patterns)
    if has_date and amount and amount > 1000:
        # Не дата код (отличить дату от кода)
        if not re.search(r'\d{6}', combined[:20]):
            return Category.REAL_CREDIT

    # 7. Всё остальное от банков — не кредит (коды, уведомления)
    return Category.AD


def _detect_sender(raw_body, raw_subject):
    """Определяет реального отправителя по тексту сообщения."""
    lower = (raw_body + ' ' + raw_subject).lower()
    for spam_sender in NOT_BANK_FROM:
        if spam_sender in lower:
            return 'spam_mfo'
    return None


def classify_alert(alert_id, source, sender_name, subject, body, amount):
    """Классифицирует алерт и возвращает категорию."""
    subject_lower = (subject or '').lower()
    body_lower = (body or '').lower()
    combined = subject_lower + ' ' + body_lower
    sender_lower = (sender_name or '').lower()

    # Проверим, не спам ли это МФО, замаскированное под банк
    real_sender = _detect_sender(body_lower, subject_lower)
    if real_sender == 'spam_mfo':
        return Category.AD

    # 1. Проверка по рекламным паттернам (общие для всех source)
    for pattern in AD_SUBJECT_PATTERNS:
        if re.search(pattern, combined):
            return Category.AD

    # 2. Проверка по сумме (чеки < 100, нереально большие > 5 млн)
    if amount is not None:
        if amount > SUSPICIOUS_AMOUNT_MAX:
            return Category.AD
        if amount < SUSPICIOUS_AMOUNT_MIN and amount > 0:
            return Category.RECEIPT

    # 3. SMS-специфичная классификация
    if source == 'sms':
        cat = is_sms_category(sender_lower, subject_lower, body_lower, combined, amount)
        if cat:
            return cat

    # 4. Неизвестный отправитель — рекламный спам МФО?
    if sender_lower in SPAM_MFO_SENDERS or sender_lower == 'spam_mfo':
        return Category.AD

    if sender_lower and sender_lower not in CREDIT_SENDERS:
        # Проверка на спам-МФО по body
        for pat in SPAM_MFO_PATTERNS:
            if re.search(pat, combined):
                return Category.AD
        # Номера телефонов — не банки (спам-МФО или звонки)
        if re.match(r'^\+7\d{10}$|^0\d{3,4}', sender_lower):
            return Category.AD
        if amount is None or amount <= 0:
            return Category.UNKNOWN

    # 5. Email от МФО с напоминанием о платеже
    if sender_lower in CREDIT_SENDERS:
        if re.search(r'напомин(?:аем|ание)|необходимост[иь]\s+внесени[яя]|очередн[оа]го\s+платеж[аа]|плат[её]ж\s+по\s+займ|плат[её]ж\s+по\s+кредит', combined):
            return Category.REAL_CREDIT
        if amount and amount > SUSPICIOUS_AMOUNT_MIN:
            return Category.REAL_CREDIT
        return Category.UNKNOWN

    # 6. Платёж на joymoney / МФО
    if re.search(r'jоуmoney|joy\s*finance|платеж.*получен|номер\s+карты\s*:\s*2200|информация\s+о\s+платеже', combined):
        body_amount = amount
        if body_amount is None:
            m = re.search(r'Сумма\s*[=:>]\s*([\d.]+)', body_lower[:500])
            if m:
                body_amount = float(m.group(1))
        if body_amount and body_amount > SUSPICIOUS_AMOUNT_MIN:
            return Category.REAL_CREDIT
        if re.search(r'номер\s+карты', combined) and body_amount and body_amount > 1000:
            return Category.REAL_CREDIT

    # 7. Спам-МФО
    for pat in SPAM_MFO_PATTERNS:
        if re.search(pat, combined):
            return Category.AD

    return Category.UNKNOWN


def run_cleanup(db_path, dry_run=False):
    """Анализирует и деактивирует некредитные алерты."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    alerts = c.execute('SELECT * FROM credit_alerts WHERE is_active = 1').fetchall()
    
    stats = {cat: 0 for cat in [Category.REAL_CREDIT, Category.AD, Category.RECEIPT, 
                                  Category.SUBSCRIPTION, Category.GOV, Category.ROADMAP,
                                  Category.UNKNOWN]}
    
    to_deactivate = []
    
    print(f'Анализирую {len(alerts)} активных алертов...')
    
    for alert in alerts:
        cat = classify_alert(
            alert['id'],
            alert['source'],
            alert['sender_name'],
            alert['subject'],
            alert['body'],
            alert['payment_amount'],
        )
        stats[cat] = stats.get(cat, 0) + 1
        
        if cat != Category.REAL_CREDIT:
            to_deactivate.append({
                'id': alert['id'],
                'source': alert['source'],
                'sender': alert['sender_name'],
                'subject': alert['subject'][:60] if alert['subject'] else '',
                'amount': alert['payment_amount'],
                'category': cat,
            })
    
    print()
    print('Статистика:')
    print(f'  ✅ Реальные кредиты:        {stats[Category.REAL_CREDIT]}')
    print(f'  📢 Реклама:                 {stats[Category.AD]}')
    print(f'  🧾 Чеки:                    {stats[Category.RECEIPT]}')
    print(f'  📋 Подписки:                {stats[Category.SUBSCRIPTION]}')
    print(f'  🏛 Госуслуги:               {stats[Category.GOV]}')
    print(f'  🗺 Roadmap/отчёты:           {stats[Category.ROADMAP]}')
    print(f'  ❓ Неопределено:            {stats[Category.UNKNOWN]}')
    print(f'  ─────────────────────────')
    print(f'  🗑 На удаление:              {len(to_deactivate)}')
    print()
    
    if to_deactivate:
        print('Детали:')
        for item in to_deactivate:
            print(f'  #{item["id"]:3d} [{item["category"]:12s}] {item["source"]:6s} | {item["sender"]:12s} | {item["subject"]:60s} | {item["amount"]}')
    
    print()
    
    if dry_run:
        print('🧪 Dry-run — изменения не внесены')
    elif to_deactivate:
        ids = [item['id'] for item in to_deactivate]
        c.execute(f"UPDATE credit_alerts SET is_active = 0 WHERE id IN ({','.join('?' * len(ids))})", ids)
        conn.commit()
        print(f'✅ Деактивировано {len(ids)} алертов')
        
        # Удаление уведомлений для них
        c.execute(f"DELETE FROM credit_alert_notifications WHERE alert_id IN ({','.join('?' * len(ids))})", ids)
        conn.commit()
        print(f'✅ Удалены связанные уведомления')
    
    # Итог
    remaining = c.execute('SELECT COUNT(*) FROM credit_alerts WHERE is_active = 1').fetchone()[0]
    print(f'Осталось активных: {remaining}')
    
    conn.close()
    return len(to_deactivate)


def main():
    parser = argparse.ArgumentParser(description='Очистка некредитных алертов')
    parser.add_argument('--db', default=None, help='Путь к consumption.db')
    parser.add_argument('--dry-run', action='store_true', help='Только показать, не удалять')
    args = parser.parse_args()
    
    db_path = args.db or os.path.join(PROJECT_DIR, 'consumption.db')
    
    if not os.path.exists(db_path):
        print(f'❌ БД не найдена: {db_path}')
        sys.exit(1)
    
    run_cleanup(db_path, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
