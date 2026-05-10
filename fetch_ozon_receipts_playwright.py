#!/usr/bin/env python3
import os
import re
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

# Config
EMAIL = "yu.v.artamonov@gmail.com"
PASSWORD = "xrsa izwn tvod ohqp"
OUTPUT_DIR = os.path.expanduser("~/.openclaw/workspace/ozoncheques")
SEARCH_SINCE = (datetime.now() - timedelta(days=30)).strftime("%m/%d/%Y")

# Ensure output dir exists
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Regex to extract order numbers
order_number_regex = re.compile(r'Заказ\s*№?\s*([A-Z0-9-]+)')

def download_receipts():
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        
        try:
            # Open Gmail
            page.goto("https://mail.google.com")
            page.wait_for_load_state("networkidle")
            
            # Login
            page.fill('input[type="email"]', EMAIL)
            page.click('button:has-text("Далее")')
            page.wait_for_selector('input[type="password"]', timeout=10000)
            page.fill('input[type="password"]', PASSWORD)
            page.click('button:has-text("Далее")')
            page.wait_for_load_state("networkidle")
            
            # Search for Ozon receipts
            page.fill('input[aria-label="Поиск в почте"]', f'from:ozonbank@news.ozon.ru subject:чек since:{SEARCH_SINCE}')
            page.press('input[aria-label="Поиск в почте"]', "Enter")
            page.wait_for_load_state("networkidle")
            
            # Get email subjects
            emails = page.query_selector_all('div[role="main"] tr')
            print(f"Found {len(emails)} emails")
            
            for email in emails[:5]:  # Limit to 5 emails for testing
                try:
                    # Click email
                    email.click()
                    page.wait_for_load_state("networkidle")
                    
                    # Extract subject and date
                    subject = page.inner_text('h2')
                    date_element = page.query_selector('span[aria-label*="Отправлено"]')
                    email_date = date_element.get_attribute("aria-label") if date_element else datetime.now().strftime("%Y-%m-%d")
                    
                    # Extract order number
                    order_number = "unknown"
                    order_match = order_number_regex.search(subject)
                    if order_match:
                        order_number = order_match.group(1)
                    
                    # Find PDF links
                    pdf_links = page.query_selector_all('a[href*=".pdf"]')
                    print(f"Found {len(pdf_links)} PDF links in email: {subject}")
                    
                    for link in pdf_links:
                        pdf_url = link.get_attribute("href")
                        if not pdf_url:
                            continue
                        
                        # Generate filename
                        filename = f"ozon_receipt_{email_date[:10]}_{order_number}.pdf"
                        filepath = os.path.join(OUTPUT_DIR, filename)
                        
                        # Skip if already downloaded
                        if os.path.exists(filepath):
                            print(f"Skipping {filename} - already exists")
                            continue
                        
                        # Download PDF
                        print(f"Downloading {filename}...")
                        with page.expect_download() as download_info:
                            link.click()
                        download = download_info.value
                        download.save_as(filepath)
                        print(f"Saved {filename}")
                        
                except Exception as e:
                    print(f"Error processing email: {e}")
                
                # Go back to inbox
                page.go_back()
                page.wait_for_load_state("networkidle")
                
        except Exception as e:
            print(f"Error: {e}")
        finally:
            browser.close()

if __name__ == "__main__":
    download_receipts()
    print("Done!")