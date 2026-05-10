import smtplib
import csv
import random
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Учётка
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
FROM_EMAIL = "yu.v.artamonov@gmail.com"
APP_PASSWORD = "[REDACTED_OLD_GMAIL_APP_PASSWORD]"

# Список доменов
DOMAINS = ["medtrade.ru", "gormed.ru", "medzakaz.ru", "medtender.ru"]

def make_email(recipient_name, recipient_email, domain):
    """Формирует письмо от ООО «Контанго» с предложением одного домена."""
    desc = {
        "medtrade.ru": "премиум-домен для B2B-торговли товарами медицинского назначения",
        "gormed.ru": "короткий брендовый домен для медицинского маркетплейса или агрегатора",
        "medzakaz.ru": "домен для тендерных площадок, госзакупок и заказа медоборудования",
        "medtender.ru": "профессиональный домен для торгов и тендеров в медицинской сфере",
    }
    d = desc.get(domain, "премиум-домен медицинской тематики")

    subject = f"Коммерческое предложение: {domain}"

    body = f"""Здравствуйте, {recipient_name}.

ООО «Контанго» предлагает к приобретению премиум-домен {domain} — {d}.

Домен полностью готов к передаче, без истории нарушений, зарегистрирован в зоне .RU, пригоден к использованию как основной домен сайта или для редиректа/защиты бренда.

Будем рады обсудить условия сделки.

С уважением,
ООО «Контанго»
info@contango.su"""

    msg = MIMEMultipart()
    msg["From"] = FROM_EMAIL
    msg["To"] = recipient_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    return msg

def send_email(msg):
    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15)
        server.set_debuglevel(1)
        server.starttls()
        server.login(FROM_EMAIL, APP_PASSWORD)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"  Ошибка отправки: {e}", flush=True)
        return False

def main():
    # Загружаем CSV
    with open('/home/yuri_artamonov/.openclaw/workspace/domains_sale/csv_data/wave2_contacts.csv', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    # Только с реальными email
    with_email = [r for r in rows if r['email'] and r['status'] == 'target']
    selected = random.sample(with_email, min(5, len(with_email)))

    for i, r in enumerate(selected):
        domain = DOMAINS[i % len(DOMAINS)]
        msg = make_email(r['name'], r['email'], domain)
        # Перенаправляем на тестовый адрес
        msg["To"] = "yu.v.artamonov@gmail.com"
        msg["Subject"] = f"[ТЕСТ] {msg['Subject']} (оригинал: {r['email']})"

        body_text = msg.get_payload()[0].get_payload()
        print(f"[{i+1}] Для: {r['name']} <{r['email']}> | Домен: {domain}")
        send_email(msg)
        print(f"  Отправлено на yu.v.artamonov@gmail.com")
        print()

if __name__ == "__main__":
    main()
