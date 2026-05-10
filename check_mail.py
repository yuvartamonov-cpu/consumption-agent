#!/usr/bin/env python3
import imaplib
import email
from email.header import decode_header
from datetime import datetime

EMAIL = "yu.v.artamonov@gmail.com"
PASSWORD = "xrsaizwntvodohqp"  # without spaces
IMAP_SERVER = "imap.gmail.com"

def decode_str(s):
    if s is None:
        return ""
    decoded, charset = decode_header(s)[0]
    if isinstance(decoded, bytes):
        try:
            return decoded.decode(charset or "utf-8")
        except:
            return decoded.decode("utf-8", errors="ignore")
    return decoded

def check_emails():
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL, PASSWORD)
        mail.select("inbox")

        # Search since 2026-05-05
        date_str = "05-May-2026"
        status, data = mail.search(None, f'(SINCE "{date_str}")')
        if status != "OK":
            print("Search failed")
            return

        email_ids = data[0].split()
        print(f"Found {len(email_ids)} emails since {date_str}")

        if not email_ids:
            print("No emails found in the date range.")
            mail.logout()
            return

        # Get the most recent ones first (last 50 or so)
        email_ids = email_ids[-50:]  # limit to recent

        results = {
            "stoloto": [],
            "fonbet": [],
            "contango": [],
            "bounces": []
        }

        for eid in reversed(email_ids):  # newest first
            status, msg_data = mail.fetch(eid, "(RFC822)")
            if status != "OK":
                continue
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            subject = decode_str(msg.get("Subject", ""))
            from_ = decode_str(msg.get("From", ""))
            date_ = msg.get("Date", "")

            # Check for Столото
            if "столото" in from_.lower() or "stoloto" in from_.lower() or "столото" in subject.lower():
                results["stoloto"].append({
                    "from": from_,
                    "subject": subject,
                    "date": date_
                })

            # Fonbet
            if "fonbet" in from_.lower() or "fonbet" in subject.lower():
                results["fonbet"].append({
                    "from": from_,
                    "subject": subject,
                    "date": date_
                })

            # admin@contango.su
            if "admin@contango.su" in from_.lower() or "contango" in from_.lower():
                results["contango"].append({
                    "from": from_,
                    "subject": subject,
                    "date": date_
                })

            # Mail delivery failed / bounce
            if "mail delivery failed" in subject.lower() or "delivery status notification" in subject.lower() or "undelivered" in subject.lower() or "bounce" in subject.lower():
                # Try to extract failed address
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode(errors="ignore")
                            break
                else:
                    body = msg.get_payload(decode=True).decode(errors="ignore") if msg.get_payload(decode=True) else ""
                failed_addr = ""
                if "to:" in body.lower():
                    import re
                    match = re.search(r"to:\s*([^\s<]+@[\w.]+)", body, re.I)
                    if match:
                        failed_addr = match.group(1)
                results["bounces"].append({
                    "from": from_,
                    "subject": subject,
                    "date": date_,
                    "failed_addr": failed_addr or "unknown"
                })

        # Print results
        print("\n=== СТОЛОТО ===")
        if results["stoloto"]:
            for e in results["stoloto"]:
                print(f"From: {e['from']}")
                print(f"Subject: {e['subject']}")
                print(f"Date: {e['date']}\n")
        else:
            print("No emails from Столото found.")

        print("\n=== FONBET ===")
        if results["fonbet"]:
            for e in results["fonbet"]:
                print(f"From: {e['from']}")
                print(f"Subject: {e['subject']}")
                print(f"Date: {e['date']}\n")
        else:
            print("No emails from Fonbet found.")

        print("\n=== CONTANGO ===")
        if results["contango"]:
            for e in results["contango"]:
                print(f"From: {e['from']}")
                print(f"Subject: {e['subject']}")
                print(f"Date: {e['date']}\n")
        else:
            print("No emails from admin@contango.su found.")

        print("\n=== BOUNCES (Mail delivery failed) ===")
        if results["bounces"]:
            for e in results["bounces"]:
                print(f"From: {e['from']}")
                print(f"Subject: {e['subject']}")
                print(f"Date: {e['date']}")
                print(f"Failed address: {e['failed_addr']}\n")
        else:
            print("No bounce emails found.")

        mail.logout()
        print("\nCheck completed.")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_emails()