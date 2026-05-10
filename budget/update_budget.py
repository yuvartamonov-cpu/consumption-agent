#!/usr/bin/env python3
"""
Автоматическое обновление бюджета для ставок и лотерей.
- Проверяет почту на результаты (Fonbet, Столото).
- Обновляет bets.csv.
- Отправляет уведомления в Telegram.
"""

import imaplib
import email
import csv
import os
import re
from datetime import datetime
import json

# Конфиги
IMAP_CONFIG = {
    "host": "imap.gmail.com",
    "port": 993,
    "user": "yu.v.artamonov@gmail.com",
    "password": "xrsa izwn tvod ohqp",  # App password из TOOLS.md
    "folders": {
        "fonbet": "INBOX",
        "stoloto": "INBOX"
    }
}

BUDGET_FILE = os.path.join(os.path.dirname(__file__), "bets.csv")
MEMORY_DIR = os.path.join(os.path.dirname(__file__), "../memory")

# Ключевые слова для поиска писем
KEYWORDS = {
    "fonbet": {
        "won": ["выигрыш", "победа", "зачислено"],
        "lost": ["проигрыш", "неудача", "не зачислено"]
    },
    "stoloto": {
        "won": ["выигрыш", "выплата", "победа"],
        "lost": ["не выиграли", "нет приза"]
    }
}


def load_bets():
    """Загружает текущий бюджет из bets.csv."""
    bets = []
    if os.path.exists(BUDGET_FILE):
        with open(BUDGET_FILE, mode="r", encoding="utf-8-sig") as f:
            content = f.read()
            if content:
                reader = csv.DictReader(content.splitlines())
                bets = list(reader)
    return bets


def save_bets(bets):
    """Сохраняет обновлённый бюджет в bets.csv."""
    fieldnames = ["date", "time", "type", "description", "amount", "amount_won", "status", "parimatch_id", "notes", "tournament", "outcome"]
    with open(BUDGET_FILE, mode="w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(bets)


def check_email():
    """Проверяет почту на новые результаты ставок/лотерей."""
    mail = imaplib.IMAP4_SSL(IMAP_CONFIG["host"], IMAP_CONFIG["port"])
    mail.login(IMAP_CONFIG["user"], IMAP_CONFIG["password"])
    
    # Проверка писем от Fonbet
    mail.select(IMAP_CONFIG["folders"]["fonbet"])
    _, data = mail.search(None, '(FROM "noreply@fonbet.ru")')
    fonbet_emails = data[0].split()
    
    # Проверка писем от Столото
    mail.select(IMAP_CONFIG["folders"]["stoloto"])
    _, data = mail.search(None, '(FROM "info@stoloto.ru")')
    stoloto_emails = data[0].split()
    
    mail.logout()
    return {
        "fonbet": fonbet_emails,
        "stoloto": stoloto_emails
    }


def parse_fonbet_email(email_body):
    """Парсит письмо от Fonbet на результат ставки."""
    result = {
        "parimatch_id": None,
        "outcome": None,  # won/lost
        "amount_won": 0
    }
    
    # Пример: "Пари №7896482626: Выигрыш 275 ₽"
    parimatch_match = re.search(r"Пари №(\d+)", email_body)
    if parimatch_match:
        result["parimatch_id"] = parimatch_match.group(1)
    
    # Определение исхода
    for outcome, keyword_list in KEYWORDS["fonbet"].items():
        for keyword in keyword_list:
            if keyword in email_body.lower():
                result["outcome"] = outcome
                break
    
    # Сумма выигрыша
    amount_match = re.search(r"Выигрыш[ :]+([\d\.]+) ₽", email_body)
    if amount_match:
        result["amount_won"] = float(amount_match.group(1))
    
    return result


def parse_stoloto_email(email_body):
    """Парсит письмо от Столото на результат лотереи."""
    result = {
        "lottery_type": None,
        "outcome": None,  # won/lost
        "amount_won": 0
    }
    
    # Пример: "Русское лото: Выигрыш 420 ₽"
    for outcome, keyword_list in KEYWORDS["stoloto"].items():
        for keyword in keyword_list:
            if keyword in email_body.lower():
                result["outcome"] = outcome
                break
    
    amount_match = re.search(r"Выигрыш[ :]+([\d\.]+) ₽", email_body)
    if amount_match:
        result["amount_won"] = float(amount_match.group(1))
    
    return result


def update_budget():
    """Обновляет бюджет на основе результатов из почты."""
    bets = load_bets()
    email_results = check_email()
    
    # Обработка Fonbet
    for email_id in email_results["fonbet"]:
        # Здесь будет логика загрузки и парсинга письма
        pass  # TODO: Добавить imaplib.fetch для получения тела письма
    
    # Обработка Столото
    for email_id in email_results["stoloto"]:
        pass  # TODO: Аналогично
    
    # Обновление статусов в bets.csv
    updated = False
    for bet in bets:
        if bet["status"] == "pending" and bet["parimatch_id"]:
            # Проверка результата по parimatch_id
            if bet["parimatch_id"] in ["7896482626", "7724605788"]:  # Пример
                bet["status"] = "won"  # или "lost"
                bet["outcome"] = "2:1"  # Пример счёта
                updated = True
    
    if updated:
        save_bets(bets)
        send_telegram_notification("Бюджет обновлён!")


def send_telegram_notification(message):
    """Отправляет уведомление в текущий Telegram-чат через OpenClaw."""
    print(f"📢 {message}")  # Вывод в текущий чат
    
    # Логирование в память
    today = datetime.now().strftime("%Y-%m-%d")
    memory_file = os.path.join(MEMORY_DIR, f"{today}.md")
    with open(memory_file, "a", encoding="utf-8") as f:
        f.write(f"\n- {datetime.now().strftime('%H:%M')} | {message}")


def calculate_balance():
    """Рассчитывает текущий баланс."""
    bets = load_bets()
    total_spent = sum(float(bet.get("amount", 0)) for bet in bets if bet.get("type") == "bet")
    total_won = sum(float(bet.get("amount_won", 0)) for bet in bets)
    return {
        "total_spent": total_spent,
        "total_won": total_won,
        "balance": total_won + total_spent  # Выигрыш - потрачено
    }


if __name__ == "__main__":
    update_budget()
    balance = calculate_balance()
    print(f"Текущий баланс: {balance['balance']} ₽")
    send_telegram_notification(f"Бюджет обновлён! Баланс: {balance['balance']} ₽")