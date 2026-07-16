#!/usr/bin/env python3
"""Send an email through a chatmail relay (mail.zwitch.ru).

Usage:
  python3 send_via_chatmail.py
  python3 send_via_chatmail.py --to friend@other.domain --subject "Hello"

Requires:
  pip install requests
"""

import argparse
import smtplib
import ssl
import requests
import getpass


def create_account(domain: str = "mail.zwitch.ru") -> tuple[str, str]:
    """Create a temporary chatmail account."""
    url = f"https://{domain}/new"
    r = requests.post(url, timeout=15)
    r.raise_for_status()
    data = r.json()
    return data["email"], data["password"]


def send_email(
    smtp_host: str,
    smtp_port: int,
    email: str,
    password: str,
    to: str,
    subject: str,
    body: str,
):
    """Send an email via SMTP. Tries SSL (465), then STARTTLS (587), then plain."""
    message = f"""From: {email}
To: {to}
Subject: {subject}
Content-Type: text/plain; charset="utf-8"

{body}"""

    context = ssl.create_default_context()
    # Allow older TLS for older Python/OpenSSL
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    try:
        # Try SSL first
        with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context, timeout=30) as s:
            s.login(email, password)
            s.sendmail(email, [to], message.encode("utf-8"))
        print(f"✅ Sent to {to} via port {smtp_port} SSL")
        return
    except Exception as e1:
        print(f"  Port {smtp_port} SSL failed: {e1}")
        try:
            # Try STARTTLS on 587
            with smtplib.SMTP(smtp_host, 587, timeout=30) as s:
                s.starttls(context=context)
                s.login(email, password)
                s.sendmail(email, [to], message.encode("utf-8"))
            print(f"✅ Sent to {to} via port 587 STARTTLS")
            return
        except Exception as e2:
            print(f"  Port 587 STARTTLS failed: {e2}")
            raise


def main():
    parser = argparse.ArgumentParser(description="Send email via chatmail relay")
    parser.add_argument("--host", default="mail.zwitch.ru", help="SMTP host")
    parser.add_argument("--port", type=int, default=465, help="SMTP port")
    parser.add_argument("--email", help="Chatmail email (creates new if omitted)")
    parser.add_argument("--password", help="Chatmail password")
    parser.add_argument("--to", default="zwitch@mail.ru", help="Recipient")
    parser.add_argument("--subject", default="Test from chatmail", help="Subject")
    parser.add_argument("--body", default="This is a test message sent via mail.zwitch.ru", help="Body")
    parser.add_argument("--create", action="store_true", help="Create new account")
    args = parser.parse_args()

    email = args.email
    password = args.password

    if args.create or not email:
        print("Creating new chatmail account...")
        email, password = create_account(args.host)
        print(f"  Email:    {email}")
        print(f"  Password: {password}")

    if not password:
        password = getpass.getpass("Password: ")

    send_email(args.host, args.port, email, password, args.to, args.subject, args.body)

    if args.create:
        print(f"\nSave these credentials to read replies:\n  {email}\n  {password}")


if __name__ == "__main__":
    main()
