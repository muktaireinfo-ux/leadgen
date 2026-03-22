"""One-time cleanup: remove non-UK leads from all Google Sheet tabs.

Identifies Indian/wrong-country rows by:
  - Website domain ends in .in or .co.in
  - Phone is present and doesn't match UK format (+44 or starts with 0)

Usage:
    cd ~/leadgen && python3 scripts/clean_sheet.py [--country uk]
"""

import re
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from leadgen import config
from leadgen.utils import retry_with_backoff
from pathlib import Path
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Expected phone prefix per country
COUNTRY_PHONE_RE = {
    "uk": re.compile(r"^\+44|^0[1-9]"),
    "de": re.compile(r"^\+49|^0[1-9]"),
    "fr": re.compile(r"^\+33|^0[1-9]"),
    "nl": re.compile(r"^\+31|^0[1-9]"),
    "es": re.compile(r"^\+34|^[6-9]\d"),
    "it": re.compile(r"^\+39|^0[1-9]"),
    "pl": re.compile(r"^\+48|^0[1-9]"),
    "se": re.compile(r"^\+46|^0[1-9]"),
}


def _is_bad_row(row: list, pattern) -> bool:
    """Return True if this row is clearly from the wrong country."""
    if len(row) < 5:
        return False

    phone_col = row[2] if len(row) > 2 else ""   # column C: Phone
    website_col = row[4] if len(row) > 4 else ""  # column E: Website URL

    # Website TLD check
    from urllib.parse import urlparse  # noqa
    domain = urlparse(website_col).netloc if website_col else ""
    if domain.endswith(".in") or domain.endswith(".co.in"):
        return True

    # Phone format check — only reject if phone is non-empty and clearly wrong
    phone = re.sub(r"[\s\-().]", "", phone_col)
    if phone and not pattern.match(phone):
        return True

    return False


def clean(country: str):
    pattern = COUNTRY_PHONE_RE.get(country.lower())
    if not pattern:
        print(f"No phone pattern defined for country '{country}'. Only filtering by website TLD.")

    creds = service_account.Credentials.from_service_account_file(
        config.GOOGLE_SERVICE_ACCOUNT_JSON, scopes=SCOPES
    )
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    sheet = service.spreadsheets()

    total_removed = 0

    for category in config.CATEGORIES:
        # Read all rows
        try:
            result = retry_with_backoff(
                lambda cat=category: sheet.values().get(
                    spreadsheetId=config.GOOGLE_SHEET_ID,
                    range=f"'{cat}'!A:M",
                ).execute()
            )
        except Exception as e:
            print(f"[Clean] Could not read '{category}': {e}")
            continue

        rows = result.get("values", [])
        if len(rows) <= 1:
            continue  # empty or header only

        header = rows[0]
        data_rows = rows[1:]

        if pattern:
            clean_rows = [r for r in data_rows if not _is_bad_row(r, pattern)]
        else:
            # Only filter by website TLD
            clean_rows = [r for r in data_rows if not _is_bad_row(r, re.compile(r"^$"))]

        removed = len(data_rows) - len(clean_rows)
        if removed == 0:
            print(f"[Clean] '{category}': nothing to remove.")
            continue

        total_removed += removed
        print(f"[Clean] '{category}': removing {removed} non-{country.upper()} rows...")

        # Clear and rewrite
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
        print(f"[Clean] '{category}': {len(clean_rows)} rows kept.")

    print(f"\nDone. Removed {total_removed} non-{country.upper()} leads total.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Remove wrong-country leads from Google Sheets.")
    parser.add_argument("--country", "-c", default="uk", help="Expected country code (default: uk)")
    args = parser.parse_args()
    clean(args.country)
