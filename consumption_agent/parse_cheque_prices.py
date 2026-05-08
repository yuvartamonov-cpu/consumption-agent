#!/usr/bin/env python3
"""
Parse PDF cheques with pdfplumber, extract per-item prices,
update items.purchase_price in DB.
"""
import sqlite3, os, re
import pdfplumber
from decimal import Decimal, InvalidOperation

DB_PATH = os.path.join(os.path.dirname(__file__), 'consumption.db')
CHEQUE_DIR = os.path.join(os.path.dirname(__file__), 'incoming_cheques')

def parse_pdf_prices(filepath):
    """
    Returns list of (item_name, price) tuples found in the PDF.
    Strategy: find lines matching "^N.  ITEM_NAME" -> look for next "1 x PRICE" line.
    """
    results = []
    try:
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                words = page.extract_words(keep_blank_chars=True, x_tolerance=3)
                # Group by y position
                lines = {}
                for w in words:
                    y = round(w['top'], 0)
                    if y not in lines:
                        lines[y] = []
                    lines[y].append((w['x0'], w['text']))
                
                sorted_y = sorted(lines)
                current_item = None
                pending_items = []
                
                for y in sorted_y:
                    l = sorted(lines[y], key=lambda x: x[0])
                    line_text = '  '.join(t for _, t in l)
                    
                    # Match item line: starts with a number and period
                    item_m = re.match(r'^\d+\.\s+(.+)', line_text)
                    if item_m:
                        # предыдущий товар без цены — запишем
                        if current_item:
                            pending_items.append(current_item)
                        current_item = item_m.group(1).strip()
                        continue
                    
                    # Match price line: "1 x 359,24  ≡359,24"
                    price_m = re.match(r'1 x ([\d\s]+[.,]\d{2})\s*[≡=]\s*([\d\s]+[.,]\d{2})', line_text)
                    if price_m and current_item:
                        try:
                            price = Decimal(price_m.group(1).replace(' ', '').replace(',', '.'))
                            results.append((current_item, float(price)))
                            current_item = None
                        except InvalidOperation:
                            pass
                        continue
                    
                    # Итог: final total line
                    if re.match(r'ИТОГ', line_text):
                        current_item = None
                
                # Last item without price
                if current_item:
                    pending_items.append(current_item)
    
    except Exception as e:
        print(f'    ERR reading {filepath}: {e}')
    
    return results, pending_items


def main():
    conn = sqlite3.connect(DB_PATH)
    
    # Get all cheques that came from PDF (cheques_log with source='ozon_pdf')
    cheques = conn.execute("""
        SELECT c.email_uid, c.subject, c.cheque_date, c.receipt_url
        FROM cheques_log c
        WHERE c.source = 'ozon_pdf'
        ORDER BY c.cheque_date
    """).fetchall()
    
    print(f'Всего PDF-чеков в БД: {len(cheques)}\n')
    
    total_updated = 0
    errors = []
    
    for uid, subject, ch_date, receipt_url in cheques:
        # receipt_url points to the text file; derive PDF path
        txt_path = receipt_url if receipt_url and receipt_url.endswith('.txt') else None
        if not txt_path:
            continue
        
        # Build PDF path from txt path
        pdf_name = os.path.basename(txt_path).replace('.txt', '.pdf')
        pdf_path = os.path.join(CHEQUE_DIR, pdf_name)
        
        if not os.path.exists(pdf_path):
            errors.append(f'  NOT FOUND: {pdf_name}')
            continue
        
        prices, pending = parse_pdf_prices(pdf_path)
        
        if not prices:
            print(f'  SKIP {pdf_name}: no prices parsed')
            if pending:
                print(f'       pending items: {pending}')
            continue
        
        # Find the purchase_id from email_uid
        purchase = conn.execute(
            "SELECT id FROM purchases WHERE email_message_id = ? AND deleted_at IS NULL",
            (uid,)
        ).fetchone()
        
        if not purchase:
            print(f'  SKIP {pdf_name}: no purchase found for uid={uid}')
            continue
        
        purchase_id = purchase[0]
        
        # Get items linked to this purchase
        items = conn.execute(
            "SELECT id, name FROM items WHERE purchase_id = ? AND deleted_at IS NULL",
            (purchase_id,)
        ).fetchall()
        
        if not items:
            print(f'  SKIP {pdf_name}: no items for purchase #{purchase_id}')
            continue
        
        # Filter out delivery/service items
        prices = [(n, p) for n, p in prices if 'доставк' not in n.lower() and 'компенсация' not in n.lower() and 'обработк' not in n.lower()]

        # Match prices to items by name prefix (fuzzy)
        matched = 0
        for pname, pprice in prices:
            pname_clean = pname.strip()
            prefix = pname_clean[:30].replace('%', '=%')
            item = conn.execute(
                "SELECT id, name FROM items WHERE id IN ({}) AND name LIKE ? AND deleted_at IS NULL LIMIT 1".format(
                    ','.join(str(i[0]) for i in items)
                ),
                (prefix + '%',)
            ).fetchone()
            
            if item:
                conn.execute("UPDATE items SET purchase_price = ? WHERE id = ?", (pprice, item[0]))
                matched += 1
                total_updated += 1
            else:
                # Try matching all items (might be renamed from PDF version)
                item2 = conn.execute(
                    "SELECT id FROM items WHERE purchase_id = ? AND deleted_at IS NULL ORDER BY id",
                    (purchase_id,)
                ).fetchall()
                if len(item2) == len(prices):
                    # Same count — match by position/order
                    pass  # fallback below
        
        # Fallback: if matched by name failed, match by position
        if matched == 0 and len(items) == len(prices):
            print(f'    {pdf_name}: fallback by position ({len(items)} items, {len(prices)} prices)')
            for i, (item, (pname, pprice)) in enumerate(zip(items, prices)):
                conn.execute("UPDATE items SET purchase_price = ? WHERE id = ?", (pprice, item[0]))
                total_updated += 1
                matched += 1
        
        print(f'  OK {pdf_name}: {len(prices)} товаров по ценам (matched={matched})')
    
    conn.commit()
    conn.close()
    
    print(f'\n=== Итог ===')
    print(f'Обновлено items с purchase_price: {total_updated}')
    if errors:
        print(f'Ошибки:')
        for e in errors:
            print(f'  {e}')


if __name__ == '__main__':
    main()
