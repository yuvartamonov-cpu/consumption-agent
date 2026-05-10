#!/usr/bin/env python3
"""Парсит email с сайтов медицинских стартапов и сводит в CSV."""
import subprocess, re, csv, time, sys

# Список (название, сайт, категория)
TARGETS = [
    ("Webiomed", "webiomed.ru", "medstartup"),
    ("Цельс", "celsus.ai", "medstartup"),
    ("Botkin.AI", "botkin.ai", "medstartup"),
    ("Платформа Третье Мнение", "thirdopinion.ai", "medstartup"),
    ("СберМедИИ", "sbermed.ai", "medstartup"),
    ("Care Mentor AI", "carementor.ai", "medstartup"),
    ("Эйдос-Медицина", "eidos-medicine.ru", "medstartup"),
    ("Моторика", "motorica.org", "medstartup"),
    ("Сенсор-Тех", "sensor-tech.ru", "medstartup"),
    ("Реатех", "reatech.tech", "medstartup"),
    ("Checkme", "checkme.health", "medstartup"),
    ("DocDoc", "docdoc.ru", "medstartup"),
    ("Кнопка жизни", "knopka24.ru", "medstartup"),
    ("Breffi", "breffi.com", "medstartup"),
    ("Долгожитель", "dolgozhitel.ru", "medstartup"),
    ("Med VR", "medvr.pro", "medstartup"),
    ("Dental Pro", "dentalpro.io", "medstartup"),
    ("Ulybnis AI", "ulybnis.ai", "medstartup"),
    ("re-feel", "re-feel.ru", "medstartup"),
    ("BRAINPHONE", "brainphone.ru", "medstartup"),
    ("Vizsionero", "vizsionero.com", "medstartup"),
    ("Polyptron", "polyptron.com", "medstartup"),
    ("Dentomo", "dentomo.com", "medstartup"),
    ("ФтизисБиоМед", "ftizisbiomed.ru", "medstartup"),
]

def fetch_emails(domain):
    """Качает главную и /contacts, ищет email."""
    emails = set()
    
    for path in ['', '/contacts', '/contact', '/about', '/o-kompanii', '/kontakty']:
        url = f'https://{domain}{path}'
        try:
            r = subprocess.run(
                ['curl', '-s', '-L', '--max-time', '8', '-A', 'Mozilla/5.0', url],
                capture_output=True, text=True, timeout=12
            )
            html = r.stdout
            found = re.findall(r'[a-zA-Z0-9._+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', html)
            for e in found:
                # фильтруем мусор
                if not any(skip in e.lower() for skip in ['example', 'sentry', '@2x', '.png', '.jpg', '.svg', 'wixpress', 'tilda']):
                    emails.add(e.lower())
        except:
            pass
    
    return emails

def main():
    out_file = '/home/yuri_artamonov/.openclaw/workspace/domains_sale/csv_data/wave2_contacts.csv'
    
    # Пишем инкрементально
    f = open(out_file, 'w', encoding='utf-8', newline='')
    w = csv.DictWriter(f, fieldnames=['name', 'email', 'website', 'source', 'category', 'status', 'notes'])
    w.writeheader()
    f.flush()
    
    total = 0
    with_email = 0
    
    for name, domain, category in TARGETS:
        print(f'[{name}] {domain}...', file=sys.stderr, flush=True)
        emails = fetch_emails(domain)
        if emails:
            for e in emails:
                w.writerow({
                    'name': name, 'email': e,
                    'website': f'https://{domain}',
                    'source': 'site_parse',
                    'category': category,
                    'status': 'target', 'notes': '',
                })
                total += 1
                with_email += 1
            print(f'  → {", ".join(emails)}', file=sys.stderr)
        else:
            print(f'  (нет email)', file=sys.stderr)
            w.writerow({
                'name': name, 'email': '',
                'website': f'https://{domain}',
                'source': 'site_parse',
                'category': category,
                'status': 'no_email',
                'notes': 'требует ручной проверки',
            })
            total += 1
        f.flush()
    
    f.close()
    print(f'\nСохранено: {total} строк в {out_file}', file=sys.stderr)
    print(f'С email: {with_email}', file=sys.stderr)

if __name__ == '__main__':
    main()
