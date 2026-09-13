#!/usr/bin/env python3
"""
Nike Restock Checker (GitHub Actions version)
----------------------------------------------
Same logic as the standalone script, but reads email credentials from
environment variables instead of hardcoding them — so they can be stored
as encrypted GitHub Actions "secrets" instead of sitting in the code.

This only READS the public product page and emails you when your size
is available. It does not add to cart or place an order.

Required environment variables (set as GitHub Actions secrets):
    SMTP_USERNAME       - the Gmail address sending the alert
    SMTP_APP_PASSWORD   - a Gmail "App Password" (not your real password)
    ALERT_TO_EMAIL      - where the alert should be sent (can be same address)
"""

import json
import os
import re
import smtplib
import sys
from email.mime.text import MIMEText
from pathlib import Path

import requests

# ============== CONFIG ==============

PRODUCT_URL = "https://www.nike.com/t/24-7-mens-shoes-1UaK9D44/IR0447-200"
TARGET_SIZE_LABEL = "10"  # partial match against Nike's on-page size label

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USERNAME = os.environ["SMTP_USERNAME"]
SMTP_APP_PASSWORD = os.environ["SMTP_APP_PASSWORD"]
ALERT_TO_EMAIL = os.environ["ALERT_TO_EMAIL"]

# GitHub Actions gives each run a clean filesystem, so "already alerted"
# state can't persist between runs the simple file-based way. Instead we
# rely on GitHub Actions' built-in de-dupe: see the workflow file, which
# only emails once by checking a committed state file back into the repo.
STATE_FILE = Path(__file__).with_name("nike_checker_state.json")

# =====================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_page(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def extract_next_data(html: str) -> dict:
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not match:
        raise RuntimeError(
            "Couldn't find __NEXT_DATA__ on the page. Nike may have changed "
            "the page structure, or the request was blocked/served a "
            "different page (e.g. a bot-check page)."
        )
    return json.loads(match.group(1))


def find_skus(data: dict) -> list:
    found = []

    def walk(node):
        if isinstance(node, dict):
            has_avail = "available" in node
            label = node.get("nikeSize") or node.get("localizedSize") or node.get("size")
            if has_avail and label:
                found.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return found


def find_target_sku(skus: list, target_label: str):
    target_label_norm = target_label.lower().replace(" ", "")
    for sku in skus:
        label = (sku.get("nikeSize") or sku.get("localizedSize") or sku.get("size") or "")
        if target_label_norm in label.lower().replace(" ", ""):
            return sku
    return None


def send_email_alert(size_label: str):
    subject = f"🔥 Nike restock alert: size {size_label} is available!"
    body = (
        f"Size {size_label} looks to be in stock:\n\n{PRODUCT_URL}\n\n"
        "Go grab it before it sells out again — this script won't try to "
        "buy it for you."
    )
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SMTP_USERNAME
    msg["To"] = ALERT_TO_EMAIL

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_APP_PASSWORD)
        server.sendmail(SMTP_USERNAME, [ALERT_TO_EMAIL], msg.as_string())


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"already_alerted": False}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state))


def main():
    html = fetch_page(PRODUCT_URL)
    data = extract_next_data(html)
    skus = find_skus(data)
    if not skus:
        print("No size/sku data found on the page — Nike's structure may "
              "have changed.")
        sys.exit(1)

    target = find_target_sku(skus, TARGET_SIZE_LABEL)
    if not target:
        available_labels = [
            (s.get("nikeSize") or s.get("localizedSize") or s.get("size"))
            for s in skus
        ]
        print(f"Couldn't find a size matching '{TARGET_SIZE_LABEL}'. "
              f"Sizes found on page: {available_labels}")
        sys.exit(1)

    label = target.get("nikeSize") or target.get("localizedSize") or target.get("size")
    is_available = bool(target.get("available"))

    state = load_state()

    if is_available:
        print(f"Size {label}: IN STOCK ✅")
        if not state.get("already_alerted"):
            send_email_alert(label)
            state["already_alerted"] = True
            save_state(state)
            print("Alert email sent.")
        else:
            print("Already alerted previously — not sending a duplicate email.")
    else:
        print(f"Size {label}: still sold out.")
        if state.get("already_alerted"):
            state["already_alerted"] = False
            save_state(state)


if __name__ == "__main__":
    main()
