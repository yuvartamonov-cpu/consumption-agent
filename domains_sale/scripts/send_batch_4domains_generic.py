#!/usr/bin/env python3
"""
Универсальный скрипт рассылки для волн 6-9.
Использование:
  python3 send_batch_4domains_generic.py csv_data/wave6_med_domains.csv logs/wave6_sent.log [test@email.com]
  python3 send_batch_4domains_generic.py csv_data/wave6_med_domains.csv logs/wave6_sent.log --send
"""
import csv, os, ssl, smtplib, sys, time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 465
SMTP_USER = "admin@contango.su"
SMTP_PASSWORD = "msaf xdad pcze jibb".replace(" ", "")

BASE_DIR = os.path.dirname(os.path.dirname(__file__))

DOMAINS = [
    {"domain": "medtrade.ru", "positioning": "B2B-площадка, маркетплейс или торговый бренд для медоборудования и расходников", "price": "по запросу"},
    {"domain": "gormed.ru", "positioning": "бренд для городского медицинского сервиса, каталога клиник или healthtech-проекта", "price": "по запросу"},
    {"domain": "medzakaz.ru", "positioning": "сервис заказа лекарств, медизделий или корпоративных поставок", "price": "по запросу"},
    {"domain": "medtender.ru", "positioning": "тендерная/закупочная платформа или отраслевой B2B-сервис для медицины", "price": "по запросу"},
]

TEMPLATE_FILE = os.path.join(BASE_DIR, "templates", "email_batch_4domains.html")


def load_template():
    with open(TEMPLATE_FILE, encoding="utf-8") as f:
        return f.read()


def domains_rows_html(primary_domain):
    rows = []
    for item in DOMAINS:
        style = "background: #fffbe6;" if item["domain"] == primary_domain else ""
        rows.append(
            f'<tr style="{style}"><td><strong>{item["domain"]}</strong></td>'
            f'<td>{item["positioning"]}</td><td>{item["price"]}</td></tr>'
        )
    return "\n".join(rows)


def render_email(row):
    org_name = (row.get("name") or "").strip()
    primary_domain = (row.get("domain") or "medtrade.ru").strip()
    pitch = "Подобрали для вас короткие и понятные домены с сильной медицинской семантикой."
    html = load_template()
    html = html.replace("{ORG_NAME}", org_name)
    html = html.replace("{CATEGORY_PITCH}", pitch)
    html = html.replace("{PRIMARY_DOMAIN}", primary_domain)
    html = html.replace("{DOMAINS_ROWS}", domains_rows_html(primary_domain))
    subject = "Эксклюзивные домены для вашего медицинского бизнеса"
    return subject, html


def build_message(row, redirect_to=None):
    subject, html = render_email(row)
    real_to = (row.get("email") or "").strip()
    send_to = redirect_to or real_to
    msg = MIMEMultipart("alternative")
    msg["From"] = f"ООО «Контанго» <{SMTP_USER}>"
    msg["To"] = send_to
    msg["Reply-To"] = SMTP_USER
    msg["Subject"] = subject if not redirect_to else f"[ТЕСТ] {subject} (оригинал: {real_to})"
    msg.attach(MIMEText(html, "html", "utf-8"))
    return msg, real_to, send_to


def send_message(msg):
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context, timeout=30) as server:
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, [msg["To"]], msg.as_string())


def append_log(log_path, row, status, detail=""):
    exists = os.path.exists(log_path)
    with open(log_path, "a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(["timestamp", "name", "email", "domain", "status", "detail"])
        writer.writerow([int(time.time()), row.get("name", ""), row.get("email", ""), row.get("domain", ""), status, detail])


def load_rows(csv_path):
    full_path = os.path.join(BASE_DIR, csv_path) if not os.path.isabs(csv_path) else csv_path
    with open(full_path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main():
    if len(sys.argv) < 3:
        print(f"Использование: {sys.argv[0]} <csv_file> <log_file> [test_email | --send]")
        sys.exit(1)

    csv_path = sys.argv[1]
    log_path = os.path.join(BASE_DIR, sys.argv[2]) if not os.path.isabs(sys.argv[2]) else sys.argv[2]
    rows = load_rows(csv_path)
    
    mode = sys.argv[3] if len(sys.argv) > 3 else "list"
    
    if mode == "list" or mode == "--list":
        print(f"Записей: {len(rows)}")
        for i, row in enumerate(rows, 1):
            print(f"[{i:02d}] {row.get('name',''):40s} {row.get('email',''):30s}")
        return
    
    if "@" in mode:
        # Тестовый режим — отправить первое письмо на указанный email
        test_email = mode
        row = rows[0]
        msg, real_to, send_to = build_message(row, redirect_to=test_email)
        try:
            send_message(msg)
            append_log(log_path, row, "test-success", f"redirected-to:{send_to};original:{real_to}")
            print(f"✅ Тест отправлен на {send_to} (оригинал: {real_to})")
        except Exception as e:
            append_log(log_path, row, "test-error", str(e))
            print(f"❌ Ошибка: {e}")
        return
    
    if mode == "--send":
        print(f"Отправка {len(rows)} писем...")
        for idx, row in enumerate(rows, 1):
            msg, real_to, send_to = build_message(row)
            try:
                send_message(msg)
                append_log(log_path, row, "success", "batch-send")
                print(f"[{idx:02d}] ✅ {real_to}")
                time.sleep(8)
            except Exception as e:
                append_log(log_path, row, "error", str(e))
                print(f"[{idx:02d}] ❌ {real_to}: {e}")
        print("Готово.")
        return
    
    print(f"Неизвестный режим: {mode}")
    print(f"Варианты: --list, <email> (тест), --send")


if __name__ == "__main__":
    main()
