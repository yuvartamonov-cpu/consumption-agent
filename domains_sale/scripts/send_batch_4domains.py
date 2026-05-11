#!/usr/bin/env python3
import csv
import os
import ssl
import smtplib
import sys
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 465
SMTP_USER = "admin@contango.su"
SMTP_PASSWORD = "msaf xdad pcze jibb".replace(" ", "")

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
BATCH_FILE = os.path.join(BASE_DIR, "csv_data", "wave2_next_batch.csv")
TEMPLATE_FILE = os.path.join(BASE_DIR, "templates", "email_batch_4domains.html")
LOG_FILE = os.path.join(BASE_DIR, "logs", "wave2_batch_log.csv")

DOMAINS = [
    {
        "domain": "medtrade.ru",
        "positioning": "B2B-площадка, маркетплейс или торговый бренд для медоборудования и расходников",
        "price": "по запросу",
    },
    {
        "domain": "gormed.ru",
        "positioning": "бренд для городского медицинского сервиса, каталога клиник или healthtech-проекта",
        "price": "по запросу",
    },
    {
        "domain": "medzakaz.ru",
        "positioning": "сервис заказа лекарств, медизделий или корпоративных поставок",
        "price": "по запросу",
    },
    {
        "domain": "medtender.ru",
        "positioning": "тендерная/закупочная платформа или отраслевой B2B-сервис для медицины",
        "price": "по запросу",
    },
]

CATEGORY_PITCHES = {
    "тендерные": "С учётом вашего профиля особенно релевантны домены для тендерных и закупочных сценариев в медицине.",
    "клиники": "Для вашей ниши домены можно использовать под отдельный digital-сервис, онлайн-заказ, новый бренд или защиту смежных направлений.",
    "поставщики": "Для поставщиков и дистрибьюторов такие домены подходят под B2B-витрину, отдельное направление продаж или отраслевой суббренд.",
    "medstartup": "Для medtech-команд такие домены подходят под запуск нового продукта, вертикали или отдельного go-to-market бренда.",
    "инвесторы": "Для доменных инвесторов и профильных площадок это ликвидные адреса с понятной медицинской семантикой.",
}


def load_rows(path=BATCH_FILE):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_template(path=TEMPLATE_FILE):
    with open(path, encoding="utf-8") as f:
        return f.read()


def domains_rows_html(primary_domain):
    rows = []
    for item in DOMAINS:
        style = "background: #fffbe6;" if item["domain"] == primary_domain else ""
        rows.append(
            f"<tr style=\"{style}\"><td><strong>{item['domain']}</strong></td>"
            f"<td>{item['positioning']}</td><td>{item['price']}</td></tr>"
        )
    return "\n".join(rows)


def render_email(row):
    category = (row.get("category") or "").strip()
    org_name = (row.get("name") or "").strip()
    primary_domain = (row.get("suggested_domain") or "medtrade.ru").strip()
    pitch = CATEGORY_PITCHES.get(category, "Подобрали для вас короткие и понятные домены с сильной медицинской семантикой.")
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


def append_log(row, status, detail=""):
    exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(["timestamp", "name", "email", "category", "suggested_domain", "status", "detail"])
        writer.writerow([
            int(time.time()),
            row.get("name", ""),
            row.get("email", ""),
            row.get("category", ""),
            row.get("suggested_domain", ""),
            status,
            detail,
        ])


def send_message(msg):
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context, timeout=30) as server:
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, [msg["To"]], msg.as_string())


def list_rows(rows):
    for i, row in enumerate(rows, 1):
        print(f"[{i:02d}] {row['name']} <{row['email']}> | {row['category']} | primary={row['suggested_domain']}")


def preview_row(row):
    subject, html = render_email(row)
    print("=" * 72)
    print(f"ORG: {row['name']}")
    print(f"TO:  {row['email']}")
    print(f"SUBJ: {subject}")
    print(f"PRIMARY: {row['suggested_domain']}")
    print("-" * 72)
    print(html)
    print("=" * 72)


def usage(total):
    print("Использование:")
    print("  python3 send_batch_4domains.py list")
    print("  python3 send_batch_4domains.py preview <N>")
    print("  python3 send_batch_4domains.py test <N> [test_email]")
    print("  python3 send_batch_4domains.py send <N> [--yes]")
    print("  python3 send_batch_4domains.py send-all [--yes]")
    print(f"\nВсего записей в батче: {total}")


def main():
    rows = load_rows()
    if len(sys.argv) == 1:
        usage(len(rows))
        return

    cmd = sys.argv[1]
    if cmd == "list":
        list_rows(rows)
        return
    if cmd == "send-all":
        if "--yes" not in sys.argv:
            print("Защита от случайной отправки: добавьте --yes")
            return
        for idx, row in enumerate(rows, 1):
            msg, real_to, send_to = build_message(row)
            try:
                send_message(msg)
                append_log(row, "success", "batch-send")
                print(f"[{idx:02d}] OK  {real_to}")
                time.sleep(8)
            except Exception as e:
                append_log(row, "error", str(e))
                print(f"[{idx:02d}] ERR {real_to}: {e}")
        return

    if len(sys.argv) < 3:
        usage(len(rows))
        return

    try:
        idx = int(sys.argv[2]) - 1
    except ValueError:
        print("Номер записи должен быть числом")
        sys.exit(1)

    if idx < 0 or idx >= len(rows):
        print(f"Номер должен быть от 1 до {len(rows)}")
        sys.exit(1)

    row = rows[idx]

    if cmd == "preview":
        preview_row(row)
        return

    if cmd == "test":
        test_email = sys.argv[3] if len(sys.argv) > 3 else "yu.v.artamonov@gmail.com"
        msg, real_to, send_to = build_message(row, redirect_to=test_email)
        try:
            send_message(msg)
            append_log(row, "test-success", f"redirected-to:{send_to};original:{real_to}")
            print(f"Тест отправлен на {send_to} (оригинал был {real_to})")
        except Exception as e:
            append_log(row, "test-error", str(e))
            print(f"Ошибка тестовой отправки: {e}")
        return

    if cmd == "send":
        if "--yes" not in sys.argv:
            print("Защита от случайной отправки: добавьте --yes")
            return
        msg, real_to, send_to = build_message(row)
        try:
            send_message(msg)
            append_log(row, "success", "single-send")
            print(f"Отправлено: {real_to}")
        except Exception as e:
            append_log(row, "error", str(e))
            print(f"Ошибка отправки: {e}")
        return

    usage(len(rows))


if __name__ == "__main__":
    main()
