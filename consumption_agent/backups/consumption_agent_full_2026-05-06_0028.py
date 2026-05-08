#!/usr/bin/env python3
"""
Consumption Agent — полный проект
Дата: 03.05.2026

Персональная система инвентаризации и отслеживания жизненного цикла покупок.
Собирает данные с почты (чеки Ozon), ведёт каталогизированный инвентарь
с категориями, сроками годности, гарантиями и уведомлениями.

База: SQLite (consumption.db)
Каналы: Telegram, CLI

Запуск: python3 consumption_agent_full_030526.py <команда>
Команды: init, import, parse, match, enrich, check, report, all, help
"""

import argparse, sqlite3, os, sys, json, re, imaplib, email
from datetime import datetime, date, timedelta, timezone
from email.header import decode_header as email_decode_header
from email.utils import parsedate as email_parsedate
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Load .env for secrets (IMAP_PASSWORD, CONSUMPTION_BOT_TOKEN)
env_path = os.path.join(SCRIPT_DIR, '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and '=' in line and not line.startswith('#'):
                k, _, v = line.partition('=')
                os.environ.setdefault(k.strip(), v.strip())
    os.chmod(env_path, 0o600)  # ensure secure permissions
DB_PATH = os.path.join(SCRIPT_DIR, 'consumption.db')
REPORT_PATH = os.path.join(SCRIPT_DIR, 'report_consumption_agent.pdf')
FONT_DIR = '/usr/share/fonts/truetype/dejavu'
IMAP_CFG = {'host': 'imap.gmail.com', 'port': 993,
            'user': os.environ.get('IMAP_USER', 'yu.v.artamonov@gmail.com'),
            'password': os.environ.get('IMAP_PASSWORD', '')}
# Marketplace senders (add new senders here)
MARKETPLACE_SENDERS = {
    'ozon': {'from': 'sender.ozon.ru', 'cheque_subject': 'ваш чек'},
    'yandex_market': {'from': 'market.yandex', 'cheque_subject': 'чек'},
    'wildberries': {'from': 'noreply@wb.ru', 'cheque_subject': 'чек'},
    'megamarket': {'from': 'info@megamarket.ru', 'cheque_subject': 'заказ'}
}

try:
    from rapidfuzz import fuzz as _fuzz; HAS_FUZZ = True
except ImportError:
    HAS_FUZZ = False
try:
    from fpdf import FPDF; HAS_PDF = True
except ImportError:
    HAS_PDF = False

# ───────────────────────────────────────────────────────────────
# 1. СХЕМА БД
# ───────────────────────────────────────────────────────────────
SCHEMA = '''
PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY DEFAULT 'default', name TEXT DEFAULT 'Default', currency TEXT DEFAULT 'RUB', timezone TEXT DEFAULT 'Europe/Moscow', notification_config TEXT DEFAULT '{}', created_at TEXT DEFAULT (datetime("now")), updated_at TEXT DEFAULT (datetime("now")));
CREATE TABLE IF NOT EXISTS categories (id TEXT PRIMARY KEY, parent_id TEXT REFERENCES categories(id), name TEXT NOT NULL, slug TEXT NOT NULL, sort_order INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1, created_at TEXT DEFAULT (datetime("now")));
CREATE TABLE IF NOT EXISTS purchases (id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id TEXT NOT NULL DEFAULT 'default', purchase_date TEXT NOT NULL, total_amount REAL, currency TEXT DEFAULT 'RUB', payment_method TEXT, source TEXT, store_name TEXT, order_number TEXT, receipt_url TEXT, email_message_id TEXT UNIQUE, notes TEXT, data_origin TEXT DEFAULT 'local', created_at TEXT DEFAULT (datetime("now")), deleted_at TEXT);
CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id TEXT NOT NULL DEFAULT 'default', category_id TEXT REFERENCES categories(id), name TEXT NOT NULL, brand TEXT, model TEXT, sku TEXT, description TEXT, attributes TEXT DEFAULT '{}', status TEXT DEFAULT 'in_use' CHECK (status IN ('wishlist','in_use','low_stock','storage','expired','broken','disposed','replaced')), quantity INTEGER DEFAULT 1, unit TEXT, remaining REAL, purchase_date TEXT, purchase_price REAL, purchase_currency TEXT DEFAULT 'RUB', purchase_source TEXT, purchase_url TEXT, purchase_id INTEGER REFERENCES purchases(id), warranty_months INTEGER, expiry_date TEXT, lifespan_months INTEGER, priority TEXT CHECK (priority IN ('critical','must','planned','backlog','wish')), target_price REAL, current_price REAL, price_tracking INTEGER DEFAULT 0, discovery_source TEXT, replaces_id INTEGER REFERENCES items(id), notes TEXT, tags TEXT DEFAULT '[]', data_origin TEXT DEFAULT 'local', created_at TEXT DEFAULT (datetime("now")), updated_at TEXT DEFAULT (datetime("now")), deleted_at TEXT);
CREATE TABLE IF NOT EXISTS recognized_items_log (id INTEGER PRIMARY KEY AUTOINCREMENT, source_file TEXT NOT NULL, source_type TEXT NOT NULL, recognized_product TEXT NOT NULL, confidence TEXT, matched_item_id INTEGER REFERENCES items(id), notes TEXT, imported_at TEXT DEFAULT (datetime("now")));
CREATE TABLE IF NOT EXISTS cheques_log (id INTEGER PRIMARY KEY AUTOINCREMENT, email_uid TEXT UNIQUE, source TEXT DEFAULT 'ozon', cheque_date TEXT, subject TEXT, receipt_url TEXT, imported_at TEXT DEFAULT (datetime("now")));
CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id TEXT NOT NULL DEFAULT 'default', item_id INTEGER REFERENCES items(id), purchase_id INTEGER REFERENCES purchases(id), alert_type TEXT NOT NULL CHECK (alert_type IN ('warranty_expiring','warranty_expired','expiry_approaching','expired','low_stock','price_drop','seasonal_reminder','dependency_alert','budget_warning')), title TEXT NOT NULL, message TEXT, scheduled_at TEXT, sent_at TEXT, status TEXT DEFAULT 'pending' CHECK (status IN ('pending','sent','dismissed','actioned')), created_at TEXT DEFAULT (datetime("now")));
CREATE TABLE IF NOT EXISTS subscriptions (id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id TEXT NOT NULL DEFAULT 'default', name TEXT NOT NULL, provider TEXT, price_monthly REAL, price_yearly REAL, currency TEXT DEFAULT 'RUB', billing_date INTEGER, next_billing TEXT, status TEXT DEFAULT 'active' CHECK (status IN ('active','paused','cancelled','expired')), auto_renew INTEGER DEFAULT 1, notes TEXT, created_at TEXT DEFAULT (datetime("now")));
CREATE INDEX IF NOT EXISTS idx_items_deleted ON items(deleted_at);
CREATE INDEX IF NOT EXISTS idx_items_category ON items(category_id);
CREATE INDEX IF NOT EXISTS idx_items_purchase ON items(purchase_id);
CREATE INDEX IF NOT EXISTS idx_items_warranty ON items(warranty_months) WHERE warranty_months IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_items_lifespan ON items(lifespan_months) WHERE lifespan_months IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_purchases_deleted ON purchases(deleted_at);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);
'''

CATS = [
    ('cat_clothing',None,'Одежда и обувь','clothing',10), ('cat_tech',None,'Техника и электроника','tech',20),
    ('cat_food',None,'Продукты питания','food',30), ('cat_cosmetics',None,'Косметика и уход','cosmetics',40),
    ('cat_health',None,'Здоровье и аптека','health',50), ('cat_home',None,'Дом и ремонт','home',60),
    ('cat_sports',None,'Спорт и активный отдых','sports',70), ('cat_auto',None,'Авто и транспорт','auto',80),
    ('cat_hobbies',None,'Хобби и развлечения','hobbies',90), ('cat_digital',None,'Цифровое','digital',100),
    ('cat_pets',None,'Животные','pets',110), ('cat_subscriptions',None,'Подписки','subscriptions',120),
    ('cat_clo_outer','cat_clothing','Верхняя одежда','outerwear',1), ('cat_clo_everyday','cat_clothing','Повседневная одежда','everyday',2),
    ('cat_clo_shoes','cat_clothing','Обувь','shoes',3), ('cat_clo_access','cat_clothing','Аксессуары','accessories',4),
    ('cat_clo_underwear','cat_clothing','Бельё и домашнее','underwear',5), ('cat_tech_comp','cat_tech','Компьютеры и планшеты','computers',1),
    ('cat_tech_audio','cat_tech','Аудио и видео','audio_video',2), ('cat_tech_phone','cat_tech','Телефоны и носимые','phones',3),
    ('cat_tech_appl','cat_tech','Бытовые приборы','appliances',4), ('cat_tech_kitchen','cat_tech','Кухонная техника','kitchen',5),
    ('cat_pets_food','cat_pets','Корм для животных','pet_food',1), ('cat_pets_med','cat_pets','Ветеринария','vet',2),
    ('cat_pets_access','cat_pets','Зоотовары','pet_access',3), ('cat_home_furn','cat_home','Мебель','furniture',1),
    ('cat_home_decor','cat_home','Декор','decor',2), ('cat_home_kitchen','cat_home','Кухня и хранение','home_kitchen',3),
    ('cat_sport','cat_sports','Спортивные товары','sport_goods',1), ('cat_culture_books','cat_hobbies','Книги и культура','books',1),
    ('cat_sexual','cat_hobbies','Интимные товары','sexual',2), ('cat_other','cat_hobbies','Прочее','other',99),
    ('cat_health_med','cat_health','Лекарства','medicine',1), ('cat_health_vit','cat_health','Витамины и БАДы','vitamins',2),
]

# ───────────────────────────────────────────────────────────────
# 2. ФУНКЦИИ
# ───────────────────────────────────────────────────────────────

def _normalize(t):
    if not t: return ''
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', t.lower().strip())).strip()

def _parse_date(s):
    if not s: return None
    s = s.strip()
    for fmt in ['%Y-%m-%d', '%d.%m.%Y']:
        try: return datetime.strptime(s, fmt).date()
        except: pass
    return None

def _decode_subj(raw):
    if not raw: return ''
    parts = email_decode_header(raw)
    return ''.join(p.decode(e or 'utf-8', errors='replace') if isinstance(p, bytes) else str(p) for p, e in parts)

def _parse_imap_date(s):
    if not s: return ''
    parsed = email_parsedate(s)
    if parsed:
        try: return date(*parsed[:3]).strftime('%Y-%m-%d')
        except: pass
    return ''

def _is_garbage(text):
    if not text or len(text.strip()) < 10: return True
    if not re.search(r'[а-яёА-ЯЁ]', text): return True
    # Shell/terminal garbage
    if re.search(r'(curl|netsh|ssh|wget|ping|ps\s|chmod|chown|system32|powershell|python[23]|pip\s|npm\s|yarn\s|apt\s|brew\s)', text, re.I): return True
    if re.search(r'(openclaw|OpenClaw|CLI|config\.json|Gateway|gateway\.)', text, re.I): return True
    if re.search(r'^[$#>]|\$|^PS\s', text.strip()): return True
    if re.search(r'import json|base64|encode\(json|decode\(|def \w+|\.py\b', text): return True
    return False


# ───────────────────────────────────────────────────────────────
# 3. КОМАНДЫ
# ───────────────────────────────────────────────────────────────

def cmd_init(args):
    conn = sqlite3.connect(args.db or DB_PATH)
    if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='profiles'").fetchone() and not args.force:
        print('БД уже инициализирована. Используйте --force.'); conn.close(); return
    if args.force:
        for t in ['subscriptions','alerts','cheques_log','recognized_items_log','items','purchases','categories','profiles']:
            conn.execute(f'DROP TABLE IF EXISTS {t}')
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR IGNORE INTO profiles (id,name) VALUES ('default','Default')")
    for c in CATS:
        conn.execute("INSERT OR IGNORE INTO categories (id,parent_id,name,slug,sort_order) VALUES (?,?,?,?,?)", c)
    conn.commit(); conn.close()
    print(f'БД инициализирована: {args.db or DB_PATH}')


def cmd_import(args):
    cfg = IMAP_CFG.copy()
    if args.user: cfg['user'] = args.user
    if args.password: cfg['password'] = args.password
    print('Подключаюсь...')
    mail = imaplib.IMAP4_SSL(cfg['host'], cfg['port'])
    mail.login(cfg['user'], cfg['password']); mail.select('INBOX')
    conn = sqlite3.connect(args.db or DB_PATH); imported = 0
    for ms_name, ms_cfg in MARKETPLACE_SENDERS.items():
        if args.sender and ms_name != args.sender:
            continue
        _, ids = mail.search(None, 'FROM', ms_cfg['from'])
        recent = ids[0].split()[-args.max:] if ids[0] else []
        cheques = []
        for uid in recent:
            _, fd = mail.fetch(uid, '(BODY.PEEK[HEADER.FIELDS (SUBJECT DATE)])')
            raw = fd[0][1].decode('utf-8', errors='replace')
            ds, sj = '', ''
            for ln in raw.split('\n'):
                ln = ln.strip()
                if ln.lower().startswith('date:'): ds = ln[5:].strip()
                elif ln.lower().startswith('subject:'): sj = _decode_subj(ln[8:].strip())
            if ms_cfg.get('cheque_subject','').lower() in sj.lower():
                cheques.append((uid, ds, sj, ms_name))
        print(f'{ms_name}: {len(cheques)} чеков из {len(recent)} писем')
        for uid, ds, sj, src in reversed(cheques):
            iso = _parse_imap_date(ds)
            uid_s = uid.decode() if isinstance(uid, bytes) else str(uid)
            if conn.execute("SELECT id FROM cheques_log WHERE email_uid=?", (uid_s,)).fetchone(): continue
            url = ''
            try:
                uid_b = uid if isinstance(uid, bytes) else bytes(uid_s, 'utf-8')
                _, fd = mail.fetch(uid_b, '(BODY.PEEK[])')
                msg = email.message_from_bytes(fd[0][1]); html = ''
                if msg.is_multipart():
                    for p in msg.walk():
                        if p.get_content_type() == 'text/html' and p.get_payload(decode=True):
                            html += p.get_payload(decode=True).decode('utf-8', errors='replace')
                else:
                    pl = msg.get_payload(decode=True)
                    if pl: html += pl.decode('utf-8', errors='replace')
                # Ozon: find e-check download link
                if src == 'ozon':
                    links = re.findall(r'href=["\']([^"\']*/e-check/download/[^"\']+)["\']', html)
                    if links: url = links[0].split('?')[0]
            except: pass
            conn.execute("INSERT OR IGNORE INTO purchases (purchase_date,source,email_message_id,receipt_url,notes) VALUES (?,?,?,?,?)",
                         (iso, src, uid_s, url, sj[:60]))
            cheque_source = f'{src}_pdf' if url else src
            conn.execute("INSERT OR IGNORE INTO cheques_log (email_uid,cheque_date,subject,receipt_url,source) VALUES (?,?,?,?,?)",
                         (uid_s, ds[:20], sj[:60], url, cheque_source))
            imported += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM purchases").fetchone()[0]
    conn.close(); mail.logout()
    print(f'Всего импортировано: {imported}, всего покупок: {total}')


def parse_fiscal_cheque(text):
    result = {}
    m = re.search(r'Кассовый чек\s+№\s*(\d+)\s+(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})', text)
    if m:
        result['cheque_number'] = int(m.group(1))
        result['date'] = f"{m.group(4)}-{m.group(3)}-{m.group(2)}"
        result['time'] = f"{m.group(5)}:{m.group(6)}"
    m = re.search(r'ИТОГ\s*.*?(\d[\d\s]*\d)', text, re.DOTALL)
    if m: result['total'] = float(m.group(1).replace(' ', '').replace(',', '.'))
    items = []
    for mt in re.finditer(r'(\d+)\.\s*(.+?)\s+(\d+)\s*x\s*(\d[\d\s,]*\d)\s*≡\s*(\d[\d\s,]*\d)', text, re.DOTALL):
        items.append({'num': int(mt.group(1)), 'name': mt.group(2).strip(), 'qty': int(mt.group(3)),
                      'price': float(mt.group(4).replace(' ', '').replace(',', '.')),
                      'total': float(mt.group(5).replace(' ', '').replace(',', '.'))})
    result['items'] = items; result['item_count'] = len(items)
    return result if (items or 'total' in result) else None


def cmd_parse(args):
    conn = sqlite3.connect(args.db or DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT cl.id,cl.email_uid,cl.receipt_url,cl.subject FROM cheques_log cl
        LEFT JOIN purchases p ON cl.email_uid=p.email_message_id AND p.deleted_at IS NULL
        WHERE cl.source='ozon_pdf' AND cl.receipt_url IS NOT NULL
        AND (p.id IS NULL OR p.data_origin != 'ozon_pdf_cheque') ORDER BY cl.id
    """).fetchall()
    print(f'Нераспарсенных чеков: {len(rows)}')
    for r in rows:
        fp = r['receipt_url']
        if fp.startswith('http'):
            local = os.path.join(SCRIPT_DIR, f'receipt_{r["id"]}.txt')
            try:
                urllib.request.urlretrieve(fp, local)
                fp = local
            except Exception as e:
                print(f'  Ошибка скачивания {fp}: {e}'); continue
        if not os.path.exists(fp): print(f'  Нет файла: {fp}'); continue
        try: text = open(fp, 'r', encoding='utf-8', errors='replace').read()
        except: print(f'  Ошибка чтения {fp}'); continue
        if 'Кассовый чек' not in text: print(f'  Не чек: {fp}'); continue
        parsed = parse_fiscal_cheque(text)
        if not parsed: print(f'  Не распарсилось: {fp}'); continue
        print(f'  Чек {parsed.get("cheque_number","?")} | {parsed.get("date")} | {parsed.get("total",0):.0f} ₽ | {parsed.get("item_count",0)} поз.')
        try:
            cur = conn.execute("""
                INSERT INTO purchases (purchase_date,total_amount,source,store_name,order_number,receipt_url,email_message_id,notes,data_origin)
                VALUES (?,?,?,?,?,?,?,?,'ozon_pdf_cheque')
            """, (parsed.get('date'), parsed.get('total'), 'ozon_pdf', 'Ozon',
                  str(parsed.get('cheque_number','')), '', r['email_uid'],
                  f"Чек {parsed.get('cheque_number','')}" if parsed.get('cheque_number') else r['subject']))
            pid = cur.lastrowid
        except sqlite3.IntegrityError:
            row = conn.execute("SELECT id FROM purchases WHERE email_message_id=?", (r['email_uid'],)).fetchone()
            if row is None:
                print(f'  Ошибка IntegrityError, purchase не найден для uid={r["email_uid"]}'); continue
            pid = row[0]
        matched, new_items = 0, []
        for it in parsed.get('items', []):
            exist = conn.execute("SELECT id FROM items WHERE name LIKE ? AND deleted_at IS NULL LIMIT 1",
                                 (f'%{it["name"][:30]}%',)).fetchone()
            if exist:
                conn.execute("UPDATE items SET purchase_id=?,purchase_price=?,purchase_date=?,quantity=? WHERE id=?",
                             (pid, it['price'], parsed.get('date'), it['qty'], exist[0]))
                matched += 1
            else:
                cur = conn.execute("""
                    INSERT INTO items (name,purchase_id,purchase_date,purchase_price,purchase_currency,quantity,data_origin,status,category_id)
                    VALUES (?,?,?,?,'RUB',?,'cheque_parse','in_use',(SELECT id FROM categories WHERE slug='other' LIMIT 1))
                """, (it['name'], pid, parsed.get('date'), it['price'], it['qty']))
                new_items.append(it['name'][:50])
        conn.commit()
        print(f'    purchase_id={pid}, matched={matched}, new={len(new_items)}')
    conn.close()


def cmd_match(args):
    if not HAS_FUZZ: print('Установите rapidfuzz: pip install rapidfuzz')
    conn = sqlite3.connect(args.db or DB_PATH)
    conn.row_factory = sqlite3.Row
    items_db = conn.execute("SELECT id,name,COALESCE(brand,'') AS brand,COALESCE(sku,'') AS sku FROM items WHERE deleted_at IS NULL").fetchall()
    items_list = [dict(r) for r in items_db]
    records = conn.execute("SELECT id,source_type,recognized_product,confidence FROM recognized_items_log WHERE matched_item_id IS NULL ORDER BY id").fetchall()
    filtered, garbage = [], []
    for rec in records:
        d = dict(rec)
        if not args.include_screen_ocr and d.get('source_type') == 'screen_ocr': garbage.append(d); continue
        if _is_garbage(d.get('recognized_product','')): garbage.append(d); continue
        filtered.append(d)
    if args.limit: filtered = filtered[:args.limit]
    stats = {'total': len(filtered), 'matched': 0, 'skipped': 0, 'garbage': len(garbage)}
    for rec in filtered:
        norm = _normalize(rec['recognized_product'])
        if not norm: stats['skipped'] += 1; continue
        candidates = []
        for it in items_list:
            if _normalize(it['name']) == norm: candidates.append({'item': it, 'score': 100, 'method': 'exact'})
        if not candidates and HAS_FUZZ:
            for it in items_list:
                s = _fuzz.token_set_ratio(norm, _normalize(it['name']))
                if s >= 70: candidates.append({'item': it, 'score': s, 'method': 'fuzzy'})
        candidates.sort(key=lambda x: x['score'], reverse=True)
        if candidates:
            b = candidates[0]
            notes = json.dumps({'method': b['method'], 'score': b['score'], 'at': datetime.now(timezone.utc).isoformat()}, ensure_ascii=False)
            if not args.dry_run: conn.execute("UPDATE recognized_items_log SET matched_item_id=?,notes=? WHERE id=?", (b['item']['id'], notes, rec['id']))
            stats['matched'] += 1
        else: stats['skipped'] += 1
    if not args.dry_run: conn.commit()
    conn.close()
    print(f'Матчинг: всего={stats["total"]}, совпало={stats["matched"]}, пропущено={stats["skipped"]}, мусора={stats["garbage"]}')


def cmd_list(args):
    """Compact item listing for Telegram/list output."""
    conn = sqlite3.connect(args.db or DB_PATH)
    c = conn.cursor()
    total = c.execute("SELECT COUNT(*) FROM items WHERE deleted_at IS NULL").fetchone()[0]
    by_cat = c.execute("""SELECT c.name, COUNT(i.id), COALESCE(SUM(i.purchase_price),0)
        FROM items i JOIN categories c ON i.category_id=c.id
        WHERE i.deleted_at IS NULL GROUP BY c.name ORDER BY COUNT(i.id) DESC""").fetchall()
    print(f'📦 Инвентарь: {total} товаров\n')
    for cat, cnt, total_p in by_cat:
        print(f'  {cat}: {cnt} шт. ({total_p:.0f} ₽)')
    print(f'\nВсего категорий: {len(by_cat)}')
    conn.close()


def cmd_alerts(args):
    """Compact alerts listing for Telegram."""
    conn = sqlite3.connect(args.db or DB_PATH)
    rows = conn.execute("SELECT alert_type,title,message,created_at FROM alerts WHERE status='pending' ORDER BY created_at").fetchall()
    if not rows:
        print('✅ Нет активных алертов')
    else:
        for r in rows:
            icon = {'warranty_expiring':'⚠️','warranty_expired':'❌','expiry_approaching':'⏳','expired':'🚫','low_stock':'📉','price_drop':'💰',}.get(r[0],'🔔')
            print(f'{icon} {r[1]}\n   {r[2]}\n')
    conn.close()


def cmd_add(args):
    """Add item manually: python3 agent.py add --name "..." --price 999 --cat food"""
    conn = sqlite3.connect(args.db or DB_PATH)
    cat_id = None
    if args.category:
        row = conn.execute("SELECT id FROM categories WHERE slug=? OR name LIKE ? LIMIT 1", (args.category, f'%{args.category}%')).fetchone()
        if row: cat_id = row[0]
    if cat_id is None:
        cat_id = conn.execute("SELECT id FROM categories WHERE slug='other' LIMIT 1").fetchone()[0]
    cur = conn.execute("""INSERT INTO items (name,purchase_price,purchase_date,category_id,status,quantity,data_origin)
        VALUES (?,?,?,?,'in_use',1,'manual')""",
        (args.name, args.price or None, args.date or date.today().isoformat(), cat_id))
    conn.commit()
    print(f'✅ Добавлено: {args.name} (id={cur.lastrowid})')
    conn.close()


def cmd_enrich(args):
    conn = sqlite3.connect(args.db or DB_PATH)
    conn.row_factory = sqlite3.Row
    today = date.today()

    # Категории
    UNC = {
        'тушь для ресниц':'cat_cosmetics','ozon premium':'cat_subscriptions',
        'кольцо-перстень':'cat_clo_access','патчи для глаз':'cat_cosmetics',
        'палочки для маникюра':'cat_cosmetics','брелок':'cat_clo_access','брошь':'cat_clo_access',
        'ватные диски':'cat_cosmetics','губки для мытья посуды':'cat_home_kitchen',
        'держатель для туалетной':'cat_home','комбинезон':'cat_clo_everyday',
        'контейнер пищевой':'cat_home_kitchen','машинка mercedes':'cat_hobbies',
        'мой любимый sputnik':'cat_culture_books','оливковое масло':'cat_food',
        'очки солнцезащитные':'cat_clo_access','перчатки':'cat_clo_access',
        'псиллиум':'cat_health_vit','игрушка-паровозик':'cat_hobbies',
        'сумка на плечо':'cat_clo_access','сухари панировочные':'cat_food',
        'тапочки':'cat_clo_everyday','трусы':'cat_clo_underwear','туалетная бумага':'cat_home',
        'циркуль школьный':'cat_hobbies',
    }
    for row in conn.execute("SELECT id,name FROM items WHERE category_id IS NULL AND deleted_at IS NULL").fetchall():
        nl = row['name'].lower()
        for kw, cid in UNC.items():
            if kw in nl: conn.execute("UPDATE items SET category_id=? WHERE id=?", (cid, row['id'])); break

    # Гарантии
    for cid, m in [('cat_tech_comp',24),('cat_tech_phone',12),('cat_tech_audio',12),
                    ('cat_tech_appl',12),('cat_tech_kitchen',12),('cat_tech',12),('cat_sport',6)]:
        conn.execute("UPDATE items SET warranty_months=? WHERE category_id=? AND warranty_months IS NULL AND deleted_at IS NULL", (m, cid))

    # Сроки годности
    for cid, m in [('cat_food',12),('cat_health_med',36),('cat_health_vit',24),
                    ('cat_pets_food',18),('cat_pets_med',36),('cat_cosmetics',36)]:
        conn.execute("UPDATE items SET lifespan_months=? WHERE category_id=? AND lifespan_months IS NULL AND deleted_at IS NULL", (m, cid))

    # Алерты
    conn.execute("DELETE FROM alerts WHERE status='pending'"); ac = 0
    for row in conn.execute("SELECT id,name,purchase_date,warranty_months FROM items WHERE warranty_months IS NOT NULL AND purchase_date IS NOT NULL AND deleted_at IS NULL").fetchall():
        pd = _parse_date(row['purchase_date'])
        if not pd: continue
        end = pd + timedelta(days=row['warranty_months']*30); left = (end - today).days
        if left < 0:
            conn.execute("INSERT INTO alerts (item_id,alert_type,title,message,scheduled_at,status) VALUES (?,?,?,?,datetime('now'),'pending')",
                         (row['id'],'warranty_expired',f'Гарантия истекла: {row["name"][:60]}',f'Истекла {-left} дн. назад')); ac += 1
        elif left <= 30:
            conn.execute("INSERT INTO alerts (item_id,alert_type,title,message,scheduled_at,status) VALUES (?,?,?,?,datetime('now'),'pending')",
                         (row['id'],'warranty_expiring',f'Гарантия истекает: {row["name"][:60]}',f'Осталось {left} дн.')); ac += 1
    for row in conn.execute("""
        SELECT i.id,i.name,i.purchase_date,i.lifespan_months FROM items i
        WHERE i.lifespan_months IS NOT NULL AND i.purchase_date IS NOT NULL AND i.deleted_at IS NULL
        AND i.category_id IN ('cat_food','cat_health_med','cat_health_vit','cat_pets_food','cat_pets_med','cat_cosmetics')
    """).fetchall():
        pd = _parse_date(row['purchase_date'])
        if not pd: continue
        end = pd + timedelta(days=row['lifespan_months']*30); left = (end - today).days
        if left < 0:
            conn.execute("INSERT INTO alerts (item_id,alert_type,title,message,scheduled_at,status) VALUES (?,?,?,?,datetime('now'),'pending')",
                         (row['id'],'expired',f'Срок годности истек: {row["name"][:60]}',f'Истек {-left} дн. назад')); ac += 1
        elif left <= 90:
            conn.execute("INSERT INTO alerts (item_id,alert_type,title,message,scheduled_at,status) VALUES (?,?,?,?,datetime('now'),'pending')",
                         (row['id'],'expiry_approaching',f'Срок годности истекает: {row["name"][:60]}',f'Осталось {left} дн.')); ac += 1
    conn.commit()
    for q, lab in [
        ("SELECT COUNT(*) FROM items WHERE category_id IS NULL AND deleted_at IS NULL","Без категории"),
        ("SELECT COUNT(*) FROM items WHERE warranty_months IS NOT NULL AND deleted_at IS NULL","С гарантией"),
        ("SELECT COUNT(*) FROM items WHERE lifespan_months IS NOT NULL AND deleted_at IS NULL","Со сроком"),
        ("SELECT COUNT(*) FROM alerts","Алертов"),
    ]: print(f'{lab}: {conn.execute(q).fetchone()[0]}')
    conn.close()


def cmd_check(args):
    conn = sqlite3.connect(args.db or DB_PATH)
    c = conn.cursor()
    print('=== СТАТИСТИКА ===')
    for q, lab in [
        ("SELECT COUNT(*) FROM items WHERE deleted_at IS NULL","Товаров активных"),
        ("SELECT COUNT(*) FROM items WHERE category_id IS NULL AND deleted_at IS NULL","Без категории"),
        ("SELECT COUNT(*) FROM items WHERE purchase_id IS NOT NULL AND deleted_at IS NULL","Связано с покупками"),
        ("SELECT COUNT(*) FROM items WHERE warranty_months IS NOT NULL AND deleted_at IS NULL","С гарантией"),
        ("SELECT COUNT(*) FROM items WHERE lifespan_months IS NOT NULL AND deleted_at IS NULL","Со сроком годности"),
        ("SELECT COUNT(*) FROM purchases WHERE deleted_at IS NULL","Покупок"),
        ("SELECT COUNT(*) FROM categories","Категорий"),
        ("SELECT COUNT(*) FROM alerts","Алертов"),
        ("SELECT COUNT(*) FROM recognized_items_log","Распознанных"),
        ("SELECT COUNT(*) FROM cheques_log","Чеков"),
    ]: print(f'  {lab}: {c.execute(q).fetchone()[0]}')
    print('\nПоследние покупки:')
    for r in c.execute("SELECT id,purchase_date,store_name,total_amount,source FROM purchases WHERE deleted_at IS NULL ORDER BY id DESC LIMIT 10").fetchall():
        print(f'  id={r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]}')
    print('\nАлерты:')
    c.execute("SELECT alert_type,count(*) FROM alerts GROUP BY alert_type")
    for r in c.fetchall(): print(f'  {r[0]}: {r[1]}')
    conn.close()


# ───────────────────────────────────────────────────────────────
# 7. PDF-ОТЧЁТ
# ───────────────────────────────────────────────────────────────

def cmd_report(args):
    if not HAS_PDF: print('Установите fpdf2: pip install fpdf2'); return
    if not os.path.exists(FONT_DIR): print(f'Шрифты не найдены в {FONT_DIR}'); return
    db_path = args.db or DB_PATH
    if not os.path.exists(db_path): print(f'БД не найдена: {db_path}'); return

    class R(FPDF):
        def __init__(s):
            super().__init__()
            s.add_font('DJV','',os.path.join(FONT_DIR,'DejaVuSans.ttf'))
            s.add_font('DJV','B',os.path.join(FONT_DIR,'DejaVuSans-Bold.ttf'))
            s.add_font('DJV','I',os.path.join(FONT_DIR,'DejaVuSansMono-Oblique.ttf'))
        def header(s):
            s.set_font('DJV','B',9); s.cell(0,6,'Consumption Agent \u2014 Project Status Report',align='C',new_x='LMARGIN',new_y='NEXT'); s.ln(8)
        def footer(s):
            s.set_y(-15); s.set_font('DJV','I',7)
            s.cell(0,8,f'Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}  |  Page {s.page_no()}/{{nb}}',align='C',new_x='LMARGIN',new_y='NEXT')
        def h1(s,t): s.set_font('DJV','B',13); s.set_fill_color(40,60,90); s.set_text_color(255,255,255); s.cell(0,8,f'  {t}',fill=True,new_x='LMARGIN',new_y='NEXT'); s.ln(4)
        def h2(s,t): s.set_font('DJV','B',10); s.set_text_color(40,60,90); s.cell(0,6,t,new_x='LMARGIN',new_y='NEXT'); s.ln(2)
        def bd(s,t,sz=8.5): s.set_font('DJV','',sz); s.set_text_color(30,30,30); s.multi_cell(0,4.5,t); s.ln(1)
        def kv(s,k,v): s.set_font('DJV','B',8.5); s.set_text_color(50,50,50); s.cell(55,5,f'{k}: '); s.set_font('DJV','',8.5); s.set_text_color(30,30,30); s.cell(0,5,str(v),new_x='LMARGIN',new_y='NEXT')
        def th(s,cols,ws): s.set_font('DJV','B',7); s.set_fill_color(50,70,100); s.set_text_color(255,255,255); [s.cell(ws[i],5,c,border=1,fill=True,align='C') for i,c in enumerate(cols)]; s.ln()
        def tr(s,cells,ws,fill=False):
            s.set_font('DJV','',7); s.set_text_color(30,30,30)
            s.set_fill_color(240,243,248) if fill else s.set_fill_color(255,255,255)
            for i,c in enumerate(cells): s.cell(ws[i],5,str(c)[:60],border=1,fill=fill)
            s.ln()
        def info(s,t,c,color=(230,240,250)):
            s.set_fill_color(*color); s.set_draw_color(180,190,210); s.set_font('DJV','B',8.5); s.set_text_color(40,60,90)
            s.cell(0,5,f'  {t}',fill=True,new_x='LMARGIN',new_y='NEXT',border='TLR')
            s.set_font('DJV','',7.5); s.set_text_color(50,50,50); s.multi_cell(0,4.5,f'  {c}',fill=True,border='BLR'); s.ln(3)

    db = sqlite3.connect(db_path); db.row_factory = sqlite3.Row
    pdf = R(); pdf.alias_nb_pages(); pdf.set_auto_page_break(auto=True,margin=20); pdf.add_page()
    pdf.ln(15)
    pdf.set_font('DJV','B',22); pdf.set_text_color(40,60,90); pdf.cell(0,10,'Consumption Agent',align='C',new_x='LMARGIN',new_y='NEXT')
    pdf.set_font('DJV','',10); pdf.set_text_color(80,80,80)
    pdf.cell(0,6,'Persistent Inventory & Lifecycle Tracking System',align='C',new_x='LMARGIN',new_y='NEXT')
    pdf.cell(0,6,f'Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")} MSK',align='C',new_x='LMARGIN',new_y='NEXT'); pdf.ln(10)

    stats = {}
    for q,k in [
        ("SELECT COUNT(*) FROM items WHERE deleted_at IS NULL","items"),
        ("SELECT COUNT(*) FROM purchases WHERE deleted_at IS NULL","purchases"),
        ("SELECT COUNT(*) FROM categories","cats"),
    ]:
        stats[k] = db.execute(q).fetchone()[0]

    pdf.h1('Executive Summary')
    pdf.info('Project Overview','Consumption Agent \u2014 inventory & lifecycle tracking system.')
    pdf.h2('Key Metrics')
    for k,v in stats.items(): pdf.kv(k,v)

    pdf.add_page(); pdf.h1('Purchases')
    pdf.th(['ID','Date','Store','Amount','Items'],[8,22,30,20,20])
    alt = False
    for i,r in enumerate(db.execute('SELECT p.id,p.purchase_date,p.store_name,p.total_amount,COUNT(i.id) as ic FROM purchases p LEFT JOIN items i ON i.purchase_id=p.id AND i.deleted_at IS NULL WHERE p.deleted_at IS NULL GROUP BY p.id ORDER BY p.purchase_date DESC').fetchall()):
        pdf.tr([r['id'],(r['purchase_date'] or '')[:10],(r['store_name'] or '')[:10],f'{r["total_amount"]:.0f}' if r['total_amount'] else '-',str(r['ic'] or 0)],[8,22,30,20,20],fill=alt); alt = not alt

    # Items by category
    pdf.add_page(); pdf.h1('Items by Category')
    cats_items = db.execute('SELECT c.name AS cat, COUNT(i.id) AS cnt, SUM(COALESCE(i.purchase_price,0)) AS total FROM items i JOIN categories c ON i.category_id=c.id WHERE i.deleted_at IS NULL GROUP BY c.name ORDER BY cnt DESC').fetchall()
    pdf.th(['Category','Count','Total ₽'],[70,20,20])
    alt = False
    for r in cats_items:
        pdf.tr([r['cat'][:50],str(r['cnt']),f'{r["total"]:.0f}'],[70,20,20],fill=alt); alt = not alt

    # Top 10 most expensive items
    pdf.ln(6)
    pdf.h2('Top 10 Most Expensive Items')
    top_items = db.execute('SELECT i.name,i.purchase_price,c.name AS cat FROM items i LEFT JOIN categories c ON i.category_id=c.id WHERE i.deleted_at IS NULL AND i.purchase_price IS NOT NULL ORDER BY i.purchase_price DESC LIMIT 10').fetchall()
    if top_items:
        pdf.th(['Name','Price ₽','Category'],[60,20,30])
        alt = False
        for r in top_items:
            pdf.tr([r['name'][:55],f'{r["purchase_price"]:.0f}',r['cat'][:25]],[60,20,30],fill=alt); alt = not alt

    # Active alerts
    pdf.add_page(); pdf.h1('Active Alerts')
    alerts = db.execute("SELECT alert_type,title,message,created_at FROM alerts WHERE status='pending' ORDER BY created_at DESC").fetchall()
    if alerts:
        pdf.th(['Type','Title','Message','Created'],[25,45,45,20])
        alt = False
        for r in alerts:
            pdf.tr([r['alert_type'][:15],r['title'][:40],r['message'][:40],(r['created_at'] or '')[:10]],[25,45,45,20],fill=alt); alt = not alt
    else:
        pdf.bd('No active alerts. All warranties and expiry dates are current.')

    # Warranty summary
    pdf.ln(6)
    pdf.h2('Warranty Coverage')
    wr = db.execute("SELECT COUNT(*) AS cnt FROM items WHERE warranty_months IS NOT NULL AND deleted_at IS NULL").fetchone()
    we = db.execute("SELECT COUNT(*) AS cnt FROM items WHERE warranty_months IS NOT NULL AND purchase_date IS NOT NULL AND deleted_at IS NULL AND date(purchase_date,'+'||warranty_months||' months') <= date('now')").fetchone()
    pdf.bd(f'Items with warranty: {wr["cnt"]}  |  Expired warranties: {we["cnt"]}')

    # OCR matching summary
    pdf.ln(4)
    pdf.h2('OCR & Recognition')
    ocr_t = db.execute('SELECT COUNT(*) AS cnt FROM recognized_items_log').fetchone()['cnt']
    ocr_m = db.execute('SELECT COUNT(*) AS cnt FROM recognized_items_log WHERE matched_item_id IS NOT NULL').fetchone()['cnt']
    pdf.bd(f'Total recognized: {ocr_t}  |  Matched to items: {ocr_m}  |  Precision: {100*ocr_m//ocr_t if ocr_t else 0}%')

    db.close(); pdf.output(REPORT_PATH)
    print(f'PDF: {REPORT_PATH}')


def cmd_all_safe(args):
    """Run all steps except parse (which hangs on Ozon PDF)."""
    cmds = globals()
    for name in ['import','match','enrich','check','report']:
        fn = cmds.get(f'cmd_{name}')
        if fn:
            print(f'\n=== {name.upper()} ===')
            fn(args)

def main():
    p = argparse.ArgumentParser(description='Consumption Agent')
    p.add_argument('--db', help='Path to DB')
    p.add_argument('--force', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--limit', type=int)
    p.add_argument('--include-screen-ocr', action='store_true')
    p.add_argument('--max', type=int, default=60)
    p.add_argument('--sender', type=str, help='Filter by sender (e.g., ozon, yandex_market)')
    p.add_argument('--user')
    p.add_argument('--password')
    p.add_argument('--name')
    p.add_argument('--price', type=float)
    p.add_argument('--date')
    p.add_argument('--category')
    p.add_argument('cmd', nargs='?', default='help',
                    choices=['init','import','parse','match','enrich','check','report','all','list','alerts','add','help'])
    args = p.parse_args()

    cmds = {'init':cmd_init,'import':cmd_import,'parse':cmd_parse,'match':cmd_match,
            'enrich':cmd_enrich,'check':cmd_check,'report':cmd_report,'list':cmd_list,'alerts':cmd_alerts,'add':cmd_add,'all':cmd_all_safe}

    if args.cmd == 'help':
        p.print_help()
        print('\nCommands: init, import, parse, match, enrich, check, report, list, alerts, add, all')
        return
    cmds[args.cmd](args)


if __name__ == '__main__':
    main()
