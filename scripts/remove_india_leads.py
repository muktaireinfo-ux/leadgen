"""Remove all Indian leads from every Google Sheet tab.

Identifies Indian rows by any of:
  - Website domain ends in .in or .co.in
  - Phone starts with +91 or 0091
  - City/Country field contains 'India' (case-insensitive)

Usage:
    cd ~/leadgen && python3 scripts/remove_india_leads.py
"""

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent.parent))

from leadgen import config
from leadgen.utils import retry_with_backoff
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

INDIA_PHONE_RE = re.compile(r"^\+91|^0091")


def _is_india_row(row: list) -> bool:
    if len(row) < 2:
        return False

    city_country = row[1] if len(row) > 1 else ""   # column B: City / Country
    phone_col    = row[2] if len(row) > 2 else ""   # column C: Phone
    website_col  = row[4] if len(row) > 4 else ""   # column E: Website URL

    # City/Country field
    if "india" in city_country.lower():
        return True

    # Website TLD
    domain = urlparse(website_col).netloc if website_col else ""
    if domain.endswith(".in") or domain.endswith(".co.in"):
        return True

    # Phone prefix
    phone = re.sub(r"[\s\-().]", "", phone_col)
    if phone and INDIA_PHONE_RE.match(phone):
        return True

    return False


def main():
    creds = service_account.Credentials.from_service_account_file(
        config.GOOGLE_SERVICE_ACCOUNT_JSON, scopes=SCOPES
    )
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    sheet = service.spreadsheets()

    total_removed = 0

    for category in config.CATEGORIES:
        try:
            result = retry_with_backoff(
                lambda cat=category: sheet.values().get(
                    spreadsheetId=config.GOOGLE_SHEET_ID,
                    range=f"'{cat}'!A:M",
                ).execute()
            )
        except Exception as e:
            print(f"[Remove India] Could not read '{category}': {e}")
            continue

        rows = result.get("values", [])
        if len(rows) <= 1:
            print(f"[Remove India] '{category}': empty or header only, skipping.")
            continue

        header = rows[0]
        data_rows = rows[1:]
        clean_rows = [r for r in data_rows if not _is_india_row(r)]
        removed = len(data_rows) - len(clean_rows)

        if removed == 0:
            print(f"[Remove India] '{category}': no Indian leads found.")
            continue

        total_removed += removed
        print(f"[Remove India] '{category}': removing {removed} Indian lead(s)...")

        retry_with_backoff(
            lambda cat=category: sheet.values().clear(
                spreadsheetId=config.GOOGLE_SHEET_ID,
                range=f"'{cat}'!A:M",
            ).execute()
        )
        all_rows = [header] + clean_rows
        retry_with_backoff(
            lambda cat=category, r=all_rows: sheet.values().update(
                spreadsheetId=config.GOOGLE_SHEET_ID,
                range=f"'{cat}'!A1",
                valueInputOption="RAW",
                body={"values": r},
            ).execute()
        )
        print(f"[Remove India] '{category}': {len(clean_rows)} row(s) kept.")

    print(f"\nDone. Removed {total_removed} Indian lead(s) total.")


if __name__ == "__main__":
    main()
