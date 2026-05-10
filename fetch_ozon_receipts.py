#!/usr/bin/env python3
import imaplib
import email
import email.policy
import re
import os
import requests
from datetime import datetime, timedelta

# Config
EMAIL = "yu.v.artamonov@gmail.com"
PASSWORD = "[REDACTED_OLD_GMAIL_APP_PASSWORD]"
IMAP_SERVER = "imap.gmail.com"
IMAP_PORT = 993
SEARCH_SINCE = (datetime.now() - timedelta(days=30)).strftime("%d-%b-%Y")
OUTPUT_DIR = os.path.expanduser("~/.openclaw/workspace/ozoncheques")

# Ensure output dir exists
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Connect to IMAP
mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
mail.login(EMAIL, PASSWORD)
mail.select("INBOX")

# Search for Ozon receipt emails
status, messages = mail.search(None, f'(SUBJECT "Ozon" SINCE "{SEARCH_SINCE}")')
if status != "OK":
    print("No messages found")
    exit(1)

email_ids = messages[0].split()
print(f"Found {len(email_ids)} emails from Ozon")

# Regex to extract PDF links and order numbers
pdf_link_regex = re.compile(r'https://[^"]+\.pdf[^"]*')
order_number_regex = re.compile(r'Заказ\s*№?\s*([A-Z0-9-]+)')

# Headers for requests
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

for email_id in email_ids:
    # Fetch email
    status, msg_data = mail.fetch(email_id, "(BODY.PEEK[])")
    if status != "OK":
        continue
    
    # Parse email
    for response_part in msg_data:
        if isinstance(response_part, tuple):
            msg = email.message_from_bytes(response_part[1], policy=email.policy.default)
            subject = msg["subject"]
            
            # Skip non-receipt emails
            if "чек" not in subject.lower():
                continue
            
            # Get email date
            date_tuple = email.utils.parsedate_tz(msg["date"])
            if date_tuple:
                email_date = datetime.fromtimestamp(email.utils.mktime_tz(date_tuple)).strftime("%Y-%m-%d")
            else:
                email_date = datetime.now().strftime("%Y-%m-%d")
            
            # Extract order number from subject
            order_number = "unknown"
            order_match = order_number_regex.search(subject)
            if order_match:
                order_number = order_match.group(1)
            
            # Find PDF links in email body
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    html = part.get_payload(decode=True).decode(errors="ignore")
                    pdf_links = pdf_link_regex.findall(html)
                    
                    for pdf_url in pdf_links:
                        try:
                            # Clean URL
                            pdf_url = pdf_url.replace("&amp;", "&")
                            
                            # Generate filename
                            filename = f"ozon_receipt_{email_date}_{order_number}.pdf"
                            filepath = os.path.join(OUTPUT_DIR, filename)
                            
                            # Skip if already downloaded
                            if os.path.exists(filepath):
                                print(f"Skipping {filename} - already exists")
                                continue
                            
                            # Download PDF
                            print(f"Downloading {filename} from {pdf_url}...")
                            response = requests.get(pdf_url, headers=headers, allow_redirects=True, timeout=10)
                            response.raise_for_status()
                            
                            with open(filepath, "wb") as f:
                                f.write(response.content)
                            
                            print(f"Saved {filename}")
                            
                        except Exception as e:
                            print(f"Failed to download {pdf_url}: {e}")

mail.close()
mail.logout()
print("Done!")