#!/usr/bin/env python3
"""Consumption Agent — генерация подробного PDF-отчёта (fpdf2 + DejaVu Unicode)."""
import sqlite3, os
from datetime import datetime
from fpdf import FPDF

DB_PATH = os.path.join(os.path.dirname(__file__), 'consumption.db')
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), 'report_consumption_agent.pdf')
FONT_DIR = '/usr/share/fonts/truetype/dejavu'

class Report(FPDF):
    def __init__(self):
        super().__init__()
        self.add_font('DJV', '',  os.path.join(FONT_DIR, 'DejaVuSans.ttf'), uni=True)
        self.add_font('DJV', 'B', os.path.join(FONT_DIR, 'DejaVuSans-Bold.ttf'), uni=True)
        self.add_font('DJV', 'I', os.path.join(FONT_DIR, 'DejaVuSansMono-Oblique.ttf'), uni=True)

    def header(self):
        self.set_font('DJV', 'B', 9)
        self.cell(0, 6, 'Consumption Agent — Project Status Report', align='C')
        self.ln(8)

    def footer(self):
        self.set_y(-15)
        self.set_font('DJV', 'I', 7)
        self.cell(0, 8, f'Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}  |  Page {self.page_no()}/{{nb}}', align='C')

    def cover_title(self, text, sz=22):
        self.set_font('DJV', 'B', sz)
        self.set_text_color(40, 60, 90)
        self.cell(0, 10, text, align='C', ln=True, new_x='LMARGIN', new_y='NEXT')

    def subtitle(self, text, sz=10, c=80):
        self.set_font('DJV', '', sz)
        self.set_text_color(c, c, c)
        self.cell(0, 6, text, align='C', ln=True, new_x='LMARGIN', new_y='NEXT')

    def h1(self, text):
        self.set_font('DJV', 'B', 13)
        self.set_fill_color(40, 60, 90)
        self.set_text_color(255, 255, 255)
        self.cell(0, 8, f'  {text}', fill=True, ln=True, new_x='LMARGIN', new_y='NEXT')
        self.ln(4)

    def h2(self, text):
        self.set_font('DJV', 'B', 10)
        self.set_text_color(40, 60, 90)
        self.cell(0, 6, text, ln=True, new_x='LMARGIN', new_y='NEXT')
        self.ln(2)

    def h3(self, text):
        self.set_font('DJV', 'I', 8.5)
        self.set_text_color(80, 80, 80)
        self.cell(0, 5, text, ln=True, new_x='LMARGIN', new_y='NEXT')
        self.ln(1)

    def body(self, text, sz=8.5):
        self.set_font('DJV', '', sz)
        self.set_text_color(30, 30, 30)
        self.multi_cell(0, 4.5, text)
        self.ln(1)

    def kv(self, key, value):
        self.set_font('DJV', 'B', 8.5)
        self.set_text_color(50, 50, 50)
        self.cell(55, 5, key + ': ')
        self.set_font('DJV', '', 8.5)
        self.set_text_color(30, 30, 30)
        self.cell(0, 5, str(value), ln=True)

    def tbl_head(self, cols, widths):
        self.set_font('DJV', 'B', 7)
        self.set_fill_color(50, 70, 100)
        self.set_text_color(255, 255, 255)
        for i, c in enumerate(cols):
            self.cell(widths[i], 5, c, border=1, fill=True, align='C')
        self.ln()

    def tbl_row(self, cells, widths, fill=False):
        self.set_font('DJV', '', 7)
        self.set_text_color(30, 30, 30)
        self.set_fill_color(240, 243, 248) if fill else self.set_fill_color(255, 255, 255)
        x0, y0 = self.get_x(), self.get_y()
        for i, c in enumerate(cells):
            self.set_xy(x0 + sum(widths[:i]), y0)
            self.cell(widths[i], 5, str(c)[:60], border=1, fill=fill)
        self.ln(5)

    def info(self, title, content, color=(230, 240, 250)):
        self.set_fill_color(*color)
        self.set_draw_color(180, 190, 210)
        self.set_font('DJV', 'B', 8.5)
        self.set_text_color(40, 60, 90)
        self.cell(0, 5, f'  {title}', fill=True, ln=True, new_x='LMARGIN', new_y='NEXT', border='TLR')
        self.set_font('DJV', '', 7.5)
        self.set_text_color(50, 50, 50)
        self.multi_cell(0, 4.5, f'  {content}', fill=True, border='BLR')
        self.ln(3)

    def cat_item(self, name, indent=0):
        self.set_font('DJV', 'B' if indent == 0 else '', 8.5 if indent == 0 else 7.5)
        self.set_text_color(40, 60, 90) if indent == 0 else self.set_text_color(60, 60, 60)
        self.cell(indent * 4, 5, '')
        self.cell(0, 5, name, ln=True)


def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def main():
    if not os.path.exists(DB_PATH):
        print(f'ERROR: {DB_PATH} not found')
        return

    db = conn()

    pdf = Report()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # === COVER ===
    pdf.ln(20)
    pdf.cover_title('Consumption Agent')
    pdf.subtitle('Persistent Inventory & Lifecycle Tracking System')
    pdf.subtitle('Project Status Report')
    pdf.ln(3)
    pdf.subtitle(f'Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")} MSK', 8, 140)
    pdf.subtitle('MVP Phase 0 — Initial Data Ingestion', 8, 140)
    pdf.ln(25)

    # TOC
    pdf.h2('TABLE OF CONTENTS')
    toc = [
        '1. Executive Summary',
        '2. Database Schema Overview',
        '3. Categories (Hierarchy)',
        '4. Inventory — All Items',
        '5. Inventory by Category',
        '6. Purchases History',
        '7. Recognized Products Log',
        '8. Cheques Log (Ozon Emails)',
        '9. System Configuration',
        '10. Roadmap & Next Steps',
        'Appendix A — Full Item List',
    ]
    pdf.set_font('DJV', '', 9)
    pdf.set_text_color(60, 60, 60)
    for s in toc:
        pdf.cell(0, 5, f'     {s}', ln=True)

    # ====================================================================
    stats = {}
    for q in [
        ('total_items', "SELECT COUNT(*) FROM items WHERE deleted_at IS NULL"),
        ('total_purchases', "SELECT COUNT(*) FROM purchases WHERE deleted_at IS NULL"),
        ('total_rec', "SELECT COUNT(*) FROM recognized_items_log"),
        ('total_cheques', "SELECT COUNT(*) FROM cheques_log"),
        ('total_cats', "SELECT COUNT(*) FROM categories"),
        ('cats_with_items', "SELECT COUNT(DISTINCT category_id) FROM items WHERE category_id IS NOT NULL AND deleted_at IS NULL"),
        ('uncategorized', "SELECT COUNT(*) FROM items WHERE category_id IS NULL AND deleted_at IS NULL"),
        ('with_expiry', "SELECT COUNT(*) FROM items WHERE expiry_date IS NOT NULL AND deleted_at IS NULL"),
        ('with_warranty', "SELECT COUNT(*) FROM items WHERE warranty_months IS NOT NULL AND deleted_at IS NULL"),
        ('with_price', "SELECT COUNT(*) FROM items WHERE purchase_price IS NOT NULL AND deleted_at IS NULL"),
    ]:
        stats[q[0]] = db.execute(q[1]).fetchone()[0]

    # === 1. EXECUTIVE SUMMARY ===
    pdf.add_page()
    pdf.h1('1. Executive Summary')

    pdf.info('Project Overview',
        'Consumption Agent — персональная система инвентаризации и отслеживания '
        'жизненного цикла покупок. Автоматически собирает данные с почты (чеки Ozon), '
        'ведёт каталогизированный инвентарь с поддержкой категорий, сроков годности, '
        'гарантий и уведомлений.')

    pdf.info('Current Status: MVP Phase 0 — Data Ingestion Complete',
        f'Database: SQLite\n'
        f'Total items: {stats["total_items"]}\n'
        f'Total purchases: {stats["total_purchases"]}\n'
        f'Recognized products: {stats["total_rec"]}\n'
        f'Cheques processed: {stats["total_cheques"]}\n'
        f'Categories defined: {stats["total_cats"]} ({stats["cats_with_items"]} active)\n'
        f'Uncategorized: {stats["uncategorized"]}\n'
        f'Data sources: Ozon email, CSV, screen recognition',
        color=(240, 255, 240))

    pdf.h2('Key Metrics')
    pdf.kv('Total inventory items', stats['total_items'])
    pdf.kv('Total purchases', stats['total_purchases'])
    pdf.kv('Active categories (leaf nodes)', stats['cats_with_items'])
    pdf.kv('Uncategorized items', stats['uncategorized'])
    pdf.kv('Items with expiry dates', stats['with_expiry'])
    pdf.kv('Items with warranties', stats['with_warranty'])
    pdf.kv('Items with purchase prices', stats['with_price'])

    pdf.h2('Status Distribution')
    sc = db.execute("SELECT status, COUNT(*) as c FROM items WHERE deleted_at IS NULL GROUP BY status").fetchall()
    pdf.tbl_head(['Status', 'Count'], [40, 30])
    alt = False
    for s in sc:
        pdf.tbl_row([s['status'] or 'in_use', s['c']], [40, 30], fill=alt)
        alt = not alt

    pdf.h2('Data Source Distribution')
    sr = db.execute("SELECT purchase_source, COUNT(*) as c FROM items WHERE deleted_at IS NULL GROUP BY purchase_source").fetchall()
    pdf.tbl_head(['Source', 'Count'], [40, 30])
    alt = False
    for s in sr:
        pdf.tbl_row([s['purchase_source'] or 'unknown', s['c']], [40, 30], fill=alt)
        alt = not alt

    # TOC page markers
    pdf.set_text_color(140, 140, 140)
    pdf.set_font('DJV', 'I', 7.5)
    pdf.cell(0, 10, '-- report continues --', align='C', ln=True)

    # === 2. DATABASE SCHEMA ===
    pdf.add_page()
    pdf.h1('2. Database Schema Overview')
    pdf.body(f'Database engine: SQLite 3.x  |  Size: {os.path.getsize(DB_PATH) / 1024:.0f} KB')
    pdf.body('The schema implements a normalized relational model with 8 tables.')

    tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    for tbl in tables:
        tn = tbl[0]
        cols = db.execute(f'PRAGMA table_info({tn})').fetchall()
        rc = db.execute(f'SELECT COUNT(*) FROM {tn}').fetchone()[0]
        pdf.h3(f'Table: {tn} ({rc} rows)')

        descs = []
        for c in cols:
            tags = ' '.join(filter(None, ['PK' if c[4] else '', 'NN' if not c[3] else '']))
            descs.append(f'{c[1]} ({c[2]}) {tags}')
        pdf.body('  |  '.join(descs), 7)

        fks = db.execute(f'PRAGMA foreign_key_list({tn})').fetchall()
        if fks:
            lines = [f'{fk[3]} -> {fk[2]}.{fk[4]}' for fk in fks]
            pdf.body(f'  FK: {", ".join(lines)}', 7)

    # === 3. CATEGORIES ===
    pdf.add_page()
    pdf.h1('3. Categories (Hierarchy)')
    pdf.body(f'Total categories: {stats["total_cats"]}')

    roots = db.execute("SELECT * FROM categories WHERE parent_id IS NULL ORDER BY sort_order").fetchall()
    for r in roots:
        cc = db.execute('SELECT COUNT(*) FROM items WHERE category_id = ? AND deleted_at IS NULL', (r['id'],)).fetchone()[0]
        pdf.cat_item(f'{r["name"]} (slug: {r["slug"]}, sort: {r["sort_order"]}) — {cc} items', 0)
        children = db.execute("SELECT * FROM categories WHERE parent_id = ? ORDER BY sort_order", (r['id'],)).fetchall()
        for ch in children:
            ic = db.execute('SELECT COUNT(*) FROM items WHERE category_id = ? AND deleted_at IS NULL', (ch['id'],)).fetchone()[0]
            pdf.cat_item(f'{ch["name"]} (slug: {ch["slug"]}) — {ic} items', 1)
        pdf.ln(2)

    # === 4. INVENTORY ===
    pdf.add_page()
    pdf.h1('4. Inventory — All Items')

    items = db.execute('''
        SELECT i.id, c.name as cat, i.name, i.status, i.quantity,
               i.purchase_price, i.warranty_months, i.expiry_date,
               i.purchase_source, i.purchase_date
        FROM items i LEFT JOIN categories c ON c.id = i.category_id
        WHERE i.deleted_at IS NULL
        ORDER BY i.purchase_date DESC, i.name
    ''').fetchall()

    widths = [8, 34, 62, 14, 10, 14, 14, 14]
    headers = ['ID', 'Category', 'Item Name', 'Status', 'Qty', 'Price', 'Warr.', 'Source']
    pdf.tbl_head(headers, widths)
    alt = False
    for idx, it in enumerate(items):
        if idx > 0 and idx % 28 == 0:
            pdf.add_page()
            pdf.tbl_head(headers, widths); alt = False
        d = dict(it)
        pr = f'{d["purchase_price"]:.0f}' if d['purchase_price'] else '-'
        w = f'{d["warranty_months"]}m' if d['warranty_months'] else '-'
        pdf.tbl_row([d['id'], (d['cat'] or '—')[:16], d['name'][:45],
                     d['status'] or 'in_use', d['quantity'] or 1, pr, w,
                     d['purchase_source'] or '?'], widths, fill=alt)
        alt = not alt
    pdf.ln(3)
    pdf.body(f'Total items: {len(items)}')

    # === 5. BY CATEGORY ===
    pdf.add_page()
    pdf.h1('5. Inventory by Category')

    bc = db.execute('''
        SELECT c.name, c.parent_id, COUNT(i.id) as cnt
        FROM categories c LEFT JOIN items i ON i.category_id = c.id AND i.deleted_at IS NULL
        GROUP BY c.id HAVING cnt > 0
        ORDER BY c.parent_id NULLS FIRST, c.sort_order
    ''').fetchall()

    pdf.tbl_head(['#', 'Category', 'Items'], [8, 60, 20])
    alt = False
    for i, r in enumerate(bc):
        pdf.tbl_row([i+1, r['name'], r['cnt']], [8, 60, 20], fill=alt)
        alt = not alt

    if stats['uncategorized'] > 0:
        pdf.h2('Uncategorized Items')
        ucs = db.execute("SELECT id, name FROM items WHERE category_id IS NULL AND deleted_at IS NULL").fetchall()
        for u in ucs:
            pdf.body(f'  [{u["id"]}] {u["name"][:60]}')

    # === 6. PURCHASES ===
    pdf.add_page()
    pdf.h1('6. Purchases History')

    purchases = db.execute('''
        SELECT p.id, p.purchase_date, p.store_name, p.source, p.total_amount,
               p.email_message_id, COUNT(i.id) as item_count
        FROM purchases p LEFT JOIN items i ON i.purchase_id = p.id AND i.deleted_at IS NULL
        WHERE p.deleted_at IS NULL GROUP BY p.id ORDER BY p.purchase_date DESC
    ''').fetchall()

    widths = [8, 22, 28, 20, 20, 20, 16]
    headers = ['ID', 'Date', 'Store', 'Source', 'Amount', 'Email ID', 'Items']
    pdf.tbl_head(headers, widths)
    alt = False
    for i, pur in enumerate(purchases):
        if i > 0 and i % 35 == 0:
            pdf.add_page(); pdf.tbl_head(headers, widths); alt = False
        d = dict(pur)
        amt = f'{d["total_amount"]:.0f}' if d['total_amount'] else '-'
        pdf.tbl_row([d['id'], (d['purchase_date'] or '')[:10], (d['store_name'] or '')[:10],
                     d['source'] or '?', amt, (d['email_message_id'] or '—')[:14],
                     d['item_count'] or 0], widths, fill=alt)
        alt = not alt

    pdf.h2('Purchases by Month')
    ms = db.execute("SELECT substr(purchase_date,1,7) as m, COUNT(*) as c FROM purchases WHERE deleted_at IS NULL AND purchase_date IS NOT NULL GROUP BY m ORDER BY m DESC").fetchall()
    for m in ms[:6]:
        pdf.body(f'  {m["m"]}: {m["c"]} purchases')

    pdf.h2('Purchases by Source')
    ss = db.execute("SELECT source, COUNT(*) as c FROM purchases WHERE deleted_at IS NULL GROUP BY source ORDER BY c DESC").fetchall()
    for s in ss:
        pdf.body(f'  {s["source"] or "unknown"}: {s["c"]}')

    # === 7. RECOGNIZED PRODUCTS ===
    pdf.add_page()
    pdf.h1('7. Recognized Products Log')

    recs = db.execute('''
        SELECT rl.id, rl.recognized_product, rl.confidence, rl.source_file,
               rl.imported_at, i.name as matched_name
        FROM recognized_items_log rl LEFT JOIN items i ON i.id = rl.matched_item_id
        ORDER BY rl.imported_at DESC
    ''').fetchall()

    widths = [8, 62, 14, 40, 14]
    headers = ['ID', 'Product Name', 'Conf.', 'Source', 'Matched']
    pdf.tbl_head(headers, widths)
    alt = False
    for i, rec in enumerate(recs):
        if i > 0 and i % 35 == 0:
            pdf.add_page(); pdf.tbl_head(headers, widths); alt = False
        d = dict(rec)
        pdf.tbl_row([d['id'], d['recognized_product'][:50], d['confidence'] or '-',
                     (d['source_file'] or '')[:25], 'Yes' if d['matched_name'] else 'No'],
                    widths, fill=alt)
        alt = not alt
    pdf.ln(3)
    pdf.body(f'Total recognized: {len(recs)}')

    pdf.h2('Confidence Distribution')
    cs = db.execute("SELECT confidence, COUNT(*) as c FROM recognized_items_log GROUP BY confidence").fetchall()
    for c in cs:
        pdf.body(f'  {c["confidence"] or "unknown"}: {c["c"]}')

    # === 8. CHEQUES LOG ===
    pdf.add_page()
    pdf.h1('8. Cheques Log (Ozon Emails)')

    chs = db.execute('''
        SELECT id, cheque_date, subject, source, receipt_url IS NOT NULL as has_receipt, imported_at
        FROM cheques_log ORDER BY cheque_date DESC
    ''').fetchall()

    widths = [8, 22, 55, 14, 14, 16]
    headers = ['ID', 'Date', 'Subject', 'Source', 'Receipt', 'Imported']
    pdf.tbl_head(headers, widths)
    alt = False
    for i, ch in enumerate(chs):
        if i > 0 and i % 35 == 0:
            pdf.add_page(); pdf.tbl_head(headers, widths); alt = False
        d = dict(ch)
        pdf.tbl_row([d['id'], (d['cheque_date'] or '')[:10], (d['subject'] or '')[:50],
                     d['source'] or '?', 'Yes' if d['has_receipt'] else 'No',
                     (d['imported_at'] or '')[:10]], widths, fill=alt)
        alt = not alt
    pdf.ln(3)
    pdf.body(f'Total cheques: {len(chs)}')
    pdf.body(f'With receipt URLs: {sum(1 for c in chs if dict(c)["has_receipt"])}')

    # === 9. SYSTEM CONFIGURATION ===
    pdf.add_page()
    pdf.h1('9. System Configuration')

    pdf.info('Profile',
        'Name: Default\nCurrency: RUB\nTimezone: Europe/Moscow\n'
        'Quiet hours: 23:00-08:00\nMax daily notifications: 3')

    pdf.info('Email Ingestion',
        'IMAP: imap.gmail.com:993 (SSL)\n'
        'Account: yu.v.artamonov@gmail.com\n'
        'Source focus: Ozon (sender@sender.ozon.ru)\n'
        'Import script: email_importer.py')

    pdf.info('Automation',
        'Clock: cron daily @ 10:00 - lottery/check_email.py\n'
        'Email import: python3 email_importer.py\n'
        'Laptop keepalive: set-windows-keepalive.ps1')

    pdf.info('Data Sources',
        '1. Ozon email (IMAP) - receipts and order confirmations\n'
        '2. Screenshots - recognized_product CSV from screen captures\n'
        '3. Manual CSV - recognized_products CSV for known purchases\n'
        '4. Lottery - stoloto.ru tickets via check_email.py\n'
        '5. Betting - fonbet results via check_email.py')

    pdf.info('OpenClaw Integration',
        'Current model: Mistral (primary), fallbacks: GPT-5.4, DeepSeek\n'
        'Channel: Telegram (direct)\n'
        'Agent: main (default)\n'
        'Workspace: /home/yuri_artamonov/.openclaw/workspace')

    # === 10. ROADMAP ===
    pdf.add_page()
    pdf.h1('10. Roadmap & Next Steps')

    pdf.h2('MVP Phase 0 -- Data Ingestion (CURRENT)')
    pdf.body('Status: DONE\n'
        '- [X] Database schema defined and created (SQLite 3.x)\n'
        '- [X] Category hierarchy seeded (29 categories)\n'
        '- [X] Ozon email import pipeline (email_importer.py)\n'
        '- [X] Recognized products imported (39 entries)\n'
        '- [X] Inventory populated (34 items)\n'
        '- [X] Item categorization (auto-classify by name)\n'
        '- [X] Cheque log populated (20 emails)')

    pdf.h2('Phase 1 -- Active Tracking (NEXT)')
    pdf.body('Status: PLANNED\n'
        '- [ ] Warranty/expiry dates - populate for relevant items\n'
        '- [ ] Alert system - generate notifications for:\n'
        '      * Warranty expiring in 30 days\n'
        '      * Items nearing expiry date\n'
        '      * Low stock reminders\n'
        '- [ ] Subscription tracking - add Ozon Premium, etc.\n'
        '- [ ] Price tracking - monitor current prices vs purchase')

    pdf.h2('Phase 2 -- Automation & Integration')
    pdf.body('Status: PLANNED\n'
        '- [ ] Auto-replenishment reminders for consumables\n'
        '- [ ] Weekly digest / Telegram push\n'
        '- [ ] Full Ozon order integration (API)\n'
        '- [ ] Wishlist with price tracking')

    pdf.h2('Phase 3 -- Advanced')
    pdf.body('Status: FUTURE\n'
        '- [ ] Dashboard (local web UI or CLI TUI)\n'
        '- [ ] Multi-profile support\n'
        '- [ ] Export/backup automation\n'
        '- [ ] OCR for paper receipts')

    # === APPENDIX A ===
    pdf.add_page()
    pdf.h1('Appendix A - Full Item List')

    all_items = db.execute('''
        SELECT i.*, c.name as category_name
        FROM items i LEFT JOIN categories c ON c.id = i.category_id
        WHERE i.deleted_at IS NULL ORDER BY c.name, i.name
    ''').fetchall()

    for idx, it in enumerate(all_items):
        d = dict(it)
        cat = d['category_name'] or '???'
        pr = f'{d["purchase_price"]:.0f} RUB' if d['purchase_price'] else 'N/A'
        dt = d['purchase_date'] or 'N/A'
        st = d['status'] or 'in_use'
        wr = f'{d["warranty_months"]} months' if d['warranty_months'] else 'N/A'
        exp = d['expiry_date'] or 'N/A'
        src = d['purchase_source'] or 'unknown'

        pdf.set_font('DJV', 'B', 7.5)
        pdf.set_text_color(40, 60, 90)
        pdf.cell(0, 5, f'{idx+1}. {d["name"][:60]}', ln=True)
        pdf.set_x(pdf.l_margin + 8)
        pdf.set_font('DJV', '', 7)
        pdf.set_text_color(70, 70, 70)
        pdf.cell(0, 4, f'Category: {cat}  |  Status: {st}  |  Price: {pr}  |  Date: {dt}', ln=True)
        pdf.set_x(pdf.l_margin + 8)
        pdf.cell(0, 4, f'Warranty: {wr}  |  Expiry: {exp}  |  Source: {src}  |  ID: {d["id"]}', ln=True)
        if idx < len(all_items) - 1 and pdf.get_y() > 260:
            pdf.add_page()
        pdf.ln(2)

    db.close()
    pdf.output(OUTPUT_PATH)
    print(f'PDF generated: {OUTPUT_PATH}')
    print(f'Size: {os.path.getsize(OUTPUT_PATH) / 1024:.1f} KB')

if __name__ == '__main__':
    main()
