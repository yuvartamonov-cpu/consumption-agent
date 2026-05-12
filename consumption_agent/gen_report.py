#!/usr/bin/env python3
"""Generate PDF report for Consumption Agent with plan."""
import sqlite3, os
from datetime import date
from fpdf import FPDF

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, 'consumption.db')
REPORT_PATH = os.path.join(SCRIPT_DIR, 'report_consumption_agent.pdf')
FONT_DIR = '/usr/share/fonts/truetype/dejavu'

db = sqlite3.connect(DB_PATH)
db.row_factory = sqlite3.Row

class PDF(FPDF):
    def h1(self, s):
        self.set_font('DejaVu','B',16)
        self.cell(0,10,s or '', new_x='LMARGIN', new_y='NEXT')
        self.ln(2)
    def h2(self, s):
        self.set_font('DejaVu','B',12)
        self.cell(0,8,s or '', new_x='LMARGIN', new_y='NEXT')
    def bd(self, s):
        self.set_font('DejaVu','',10)
        text = str(s or '').replace('\n', ' ').replace('\r', '')
        self.multi_cell(0,6,text)
    def tr(self, cells, widths, fill=False):
        self.set_fill_color(240,240,240) if fill else self.set_fill_color(255,255,255)
        self.set_font('DejaVu','',8)
        for c,w in zip(cells,widths):
            self.cell(w,5,str(c or '')[:60],border=1,fill=fill)
        self.ln()
    def th(self, cells, widths):
        self.set_font('DejaVu','B',8); self.set_fill_color(200,200,200)
        for c,w in zip(cells,widths):
            self.cell(w,5,str(c or '')[:60],border=1,fill=True)
        self.ln()

pdf = PDF()
pdf.add_font('DejaVu','', os.path.join(FONT_DIR,'DejaVuSans.ttf'), uni=True)
pdf.add_font('DejaVu','B', os.path.join(FONT_DIR,'DejaVuSans-Bold.ttf'), uni=True)
pdf.set_auto_page_break(auto=True, margin=15)

# Cover
pdf.add_page()
pdf.set_font('DejaVu','B',22)
pdf.cell(0,12,'Consumption Agent', new_x='LMARGIN', new_y='NEXT')
pdf.set_font('DejaVu','',12)
pdf.cell(0,7,f'Report: {date.today().isoformat()}', new_x='LMARGIN', new_y='NEXT')
pdf.cell(0,7,'Personal Inventory & Lifecycle Tracker', new_x='LMARGIN', new_y='NEXT')
pdf.ln(8)

# Stats
tot_items = db.execute('SELECT COUNT(*) FROM items WHERE deleted_at IS NULL').fetchone()[0]
tot_purch = db.execute('SELECT COUNT(*) FROM purchases WHERE deleted_at IS NULL').fetchone()[0]
tot_cats = db.execute('SELECT COUNT(*) FROM categories').fetchone()[0]
tot_alerts = db.execute("SELECT COUNT(*) FROM alerts WHERE status='pending'").fetchone()[0]
tot_warr = db.execute('SELECT COUNT(*) FROM items WHERE warranty_months IS NOT NULL AND deleted_at IS NULL').fetchone()[0]
tot_expiry = db.execute('SELECT COUNT(*) FROM items WHERE lifespan_months IS NOT NULL AND deleted_at IS NULL').fetchone()[0]
tot_ocr = db.execute('SELECT COUNT(*) FROM recognized_items_log').fetchone()[0]
tot_matched = db.execute('SELECT COUNT(*) FROM recognized_items_log WHERE matched_item_id IS NOT NULL').fetchone()[0]
tot_cheques = db.execute('SELECT COUNT(*) FROM cheques_log').fetchone()[0]

pdf.h1('Statistics')
pdf.set_font('DejaVu','',10)
pdf.cell(0,6,f'Active items: {tot_items}', new_x='LMARGIN', new_y='NEXT')
pdf.cell(0,6,f'Purchases: {tot_purch}', new_x='LMARGIN', new_y='NEXT')
pdf.cell(0,6,f'Categories: {tot_cats}', new_x='LMARGIN', new_y='NEXT')
pdf.cell(0,6,f'Active alerts: {tot_alerts}', new_x='LMARGIN', new_y='NEXT')
pdf.cell(0,6,f'Items with warranty: {tot_warr}', new_x='LMARGIN', new_y='NEXT')
pdf.cell(0,6,f'Items with expiry: {tot_expiry}', new_x='LMARGIN', new_y='NEXT')
pdf.cell(0,6,f'Cheques processed: {tot_cheques}', new_x='LMARGIN', new_y='NEXT')
match_pct = 100*tot_matched//tot_ocr if tot_ocr else 0
pdf.cell(0,6,f'OCR records: {tot_ocr} (matched: {tot_matched}, {match_pct}%)', new_x='LMARGIN', new_y='NEXT')
pdf.ln(4)

# Categories
pdf.h1('Inventory by Category')
cats = db.execute('''
    SELECT c.name AS cat, COUNT(i.id) AS cnt, COALESCE(SUM(i.purchase_price),0) AS total
    FROM items i JOIN categories c ON i.category_id=c.id
    WHERE i.deleted_at IS NULL GROUP BY c.name ORDER BY cnt DESC
''').fetchall()
pdf.th(['Category','Count','Total RUB'],[70,20,25])
alt = False
for r in cats:
    pdf.tr([r['cat'][:60], str(r['cnt']), f'{r["total"]:.0f}'], [70,20,25], fill=alt)
    alt = not alt
pdf.ln(4)

# Top items
pdf.h1('Top 10 Most Expensive Items')
top = db.execute('''
    SELECT i.name,i.purchase_price,c.name AS cat
    FROM items i LEFT JOIN categories c ON i.category_id=c.id
    WHERE i.deleted_at IS NULL AND i.purchase_price IS NOT NULL
    ORDER BY i.purchase_price DESC LIMIT 10
''').fetchall()
if top:
    pdf.th(['Name','Price RUB','Category'],[60,20,30])
    alt = False
    for r in top:
        pdf.tr([r['name'][:55], f'{r["purchase_price"]:.0f}', r['cat'][:25]], [60,20,30], fill=alt)
        alt = not alt
pdf.ln(4)

# Alerts
pdf.add_page()
pdf.h1('Active Alerts')
alerts = db.execute("SELECT alert_type,title,message,created_at FROM alerts WHERE status='pending' ORDER BY created_at DESC").fetchall()
if alerts:
    pdf.th(['Type','Title','Message','Created'],[25,45,50,15])
    alt = False
    for r in alerts:
        pdf.tr([r['alert_type'][:15], r['title'][:40], r['message'][:50], (r['created_at'] or '')[:10]], [25,45,50,15], fill=alt)
        alt = not alt
else:
    pdf.bd('No active alerts.')
pdf.ln(4)

# Warranty
pdf.h1('Warranty Coverage')
wr = db.execute("SELECT COUNT(*) FROM items WHERE warranty_months IS NOT NULL AND deleted_at IS NULL").fetchone()[0]
we = db.execute("SELECT COUNT(*) FROM items WHERE warranty_months IS NOT NULL AND purchase_date IS NOT NULL AND deleted_at IS NULL AND date(purchase_date,'+'||warranty_months||' months') <= date('now')").fetchone()[0]
pdf.bd(f'Items with warranty: {wr}  |  Expired: {we}')
pdf.ln(2)
pdf.h2('Items with Active Warranty')
warr = db.execute('''
    SELECT i.name,i.purchase_date,i.warranty_months,
           date(i.purchase_date,'+'||i.warranty_months||' months') AS expiry,
           c.name AS cat
    FROM items i LEFT JOIN categories c ON i.category_id=c.id
    WHERE i.warranty_months IS NOT NULL AND i.deleted_at IS NULL
      AND date(i.purchase_date,'+'||i.warranty_months||' months') > date('now')
    ORDER BY expiry
''').fetchall()
if warr:
    pdf.th(['Name','Purchased','Months','Expiry','Category'],[50,20,15,25,25])
    alt = False
    for r in warr:
        pdf.tr([r['name'][:45], (r['purchase_date'] or '')[:10], str(r['warranty_months']), (r['expiry'] or '')[:10], r['cat'][:20]], [50,20,15,25,25], fill=alt)
        alt = not alt
else:
    pdf.bd('No active warranties.')

pdf.ln(4)
pdf.h1('OCR & Recognition')
pdf.set_font('DejaVu','',10)
pdf.cell(0,6,f'Total OCR records: {tot_ocr}  |  Matched to items: {tot_matched}', new_x='LMARGIN', new_y='NEXT')
pdf.cell(0,6,f'Precision: {100*tot_matched//tot_ocr if tot_ocr else 0}%', new_x='LMARGIN', new_y='NEXT')
pdf.cell(0,6,f'Cheques imported: {tot_cheques}', new_x='LMARGIN', new_y='NEXT')

# Plan
pdf.add_page()
pdf.h1('Development Plan')
plan = [
    # Each item becomes a line via multi_cell
    'Phase 1 — Telegram Bot (Current)',
    '  [done] /list — compact inventory by category',
    '  [done] /alerts — active alerts listing',
    '  [done] /check — statistics overview',
    '  [done] /add — manual item addition',
    '  [done] @ConsumptionAgentBot — live polling via Telegram API',
    '',
    'Phase 2 — Import Integration',
    '  [waiting] Ozon PDF cheque parsing (needs auth session with cookies)',
    '  [waiting] Yandex.Market text cheque parsing (cheques are images only)',
    '  [waiting] Wildberries email import (no emails found yet)',
    '  [waiting] Megamarket email import (no emails found)',
    '',
    'Phase 3 — Smart Features',
    '  [planned] Photo input / Memory Lane OCR',
    '  [planned] Price tracking & drop alerts',
    '  [planned] Budget planning per category',
    '  [done] rapidfuzz auto-match (threshold 70, +86 new matches)',
    '',
    'Phase 4 — Analytics',
    '  [planned] Monthly spending trends chart in PDF',
    '  [planned] Category breakdown visualisation',
    '  [planned] Lifetime value of items',
    '  [planned] Subscription renewal calendar',
    '',
    '---',
    '',
    f'Snapshot: {tot_items} items | {tot_purch} purchases | {tot_cats} categories',
    f'Alerts: {tot_alerts} active | Warranty: {tot_warr} items | Expiry: {tot_expiry} items',
    f'OCR: {tot_ocr} records | Cheques: {tot_cheques} imported',
]
pdf.set_font('DejaVu','',9)
for l in plan:
    safe_l = str(l).replace('\n', ' ').replace('\r', '')
    pdf.cell(0,5,safe_l, new_x='LMARGIN', new_y='NEXT')
    if not l:
        pdf.ln(1)

db.close()
pdf.output(REPORT_PATH)
print(f'OK: {REPORT_PATH}')
