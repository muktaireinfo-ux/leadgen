"""Google Sheets writer — batch writes classified leads to the correct tabs.

Design decisions vs v1:
- write_batch() collects ALL leads and writes per-category in ONE API call each,
  avoiding the 60 writes/minute quota limit that individual append() calls would hit.
- Exponential backoff via retry_with_backoff() on all API calls.
- Uses google-api-python-client directly (not gspread, which is unmaintained).
- Auth: Google Service Account JSON (no browser popup, works in cron/background).

Setup:
  1. Enable Google Sheets API at console.cloud.google.com
  2. Create Service Account → download JSON → save path in .env
  3. Share your Google Sheet with the service account email
"""

from datetime import datetime, timezone
from pathlib import Path
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from leadgen import config
from leadgen.utils import retry_with_backoff

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetsWriter:
    def __init__(self):
        self._service = None
        self._existing_keys: dict[str, set] = {}

    def _get_service(self):
        if self._service is None:
            creds_path = Path(config.GOOGLE_SERVICE_ACCOUNT_JSON)
            if not creds_path.exists():
                raise FileNotFoundError(
                    f"Service account JSON not found at {creds_path}\n"
                    "See .env.example for setup instructions."
                )
            creds = service_account.Credentials.from_service_account_file(
                str(creds_path), scopes=SCOPES
            )
            self._service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        return self._service

    def _ensure_tabs(self, categories: list[str]):
        """Create any missing tabs and write header rows."""
        service = self._get_service()
        sheet = service.spreadsheets()

        meta = retry_with_backoff(
            lambda: sheet.get(spreadsheetId=config.GOOGLE_SHEET_ID).execute()
        )
        existing = {s["properties"]["title"] for s in meta["sheets"]}
        missing = [c for c in categories if c not in existing]

        if missing:
            requests_body = [
                {"addSheet": {"properties": {"title": name}}} for name in missing
            ]
            retry_with_backoff(
                lambda: sheet.batchUpdate(
                    spreadsheetId=config.GOOGLE_SHEET_ID,
                    body={"requests": requests_body},
                ).execute()
            )
            # Write header rows for new tabs
            header_data = [
                {
                    "range": f"'{name}'!A1",
                    "values": [config.SHEET_COLUMNS],
                }
                for name in missing
            ]
            retry_with_backoff(
                lambda: sheet.values().batchUpdate(
                    spreadsheetId=config.GOOGLE_SHEET_ID,
                    body={"valueInputOption": "RAW", "data": header_data},
                ).execute()
            )

    def _load_existing_keys(self):
        """Load all existing business name|phone pairs to prevent duplicates."""
        service = self._get_service()
        for category in config.CATEGORIES:
            try:
                result = retry_with_backoff(
                    lambda cat=category: service.spreadsheets()
                    .values()
                    .get(
                        spreadsheetId=config.GOOGLE_SHEET_ID,
                        range=f"'{cat}'!A:C",
                    )
                    .execute()
                )
                rows = result.get("values", [])
                self._existing_keys[category] = {
                    f"{row[0]}|{row[2] if len(row) > 2 else ''}"
                    for row in rows[1:]
                }
            except HttpError:
                self._existing_keys[category] = set()

    def write_batch(self, classified_leads: list[tuple[dict, str, str, bool]]):
        """Write all leads in one batched API call per category.

        Args:
            classified_leads: list of (lead_dict, category, evidence, is_inferred)
        """
        if not classified_leads:
            return

        if not config.GOOGLE_SHEET_ID:
            print("[Writer] GOOGLE_SHEET_ID not set — skipping Sheets write.")
            for lead, category, evidence, is_inferred in classified_leads:
                flag = "[INFERRED] " if is_inferred else ""
                print(f"  {flag}{lead.get('name')} → {category}: {evidence}")
            return

        categories_used = list({cat for _, cat, _, _ in classified_leads})
        self._ensure_tabs(config.CATEGORIES)  # create all tabs upfront to avoid read errors
        self._load_existing_keys()

        # Group rows by category
        rows_by_category: dict[str, list[list]] = {cat: [] for cat in categories_used}
        skipped = 0

        for lead, category, evidence, is_inferred in classified_leads:
            dedup_key = f"{lead.get('name', '')}|{lead.get('phone', '')}"
            if dedup_key in self._existing_keys.get(category, set()):
                skipped += 1
                continue

            row = [
                lead.get("name", ""),
                f"{lead.get('city', '')} / {lead.get('country', '')}",
                lead.get("phone", ""),
                lead.get("email", ""),
                lead.get("website", ""),
                str(lead.get("rating", "")),
                str(lead.get("review_count", "")),
                ", ".join(lead.get("social_links", [])[:3]),  # cap at 3 links
                category,
                evidence,
                "YES" if is_inferred else "NO",
                "Instagram" if lead.get("scraper") == "apify_instagram" else "Google Maps",
                datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            ]
            rows_by_category[category].append(row)

        if skipped:
            print(f"[Writer] Skipped {skipped} duplicates.")

        # One append call per category (not per lead)
        service = self._get_service()
        total_written = 0
        for category, rows in rows_by_category.items():
            if not rows:
                continue
            retry_with_backoff(
                lambda cat=category, r=rows: service.spreadsheets()
                .values()
                .append(
                    spreadsheetId=config.GOOGLE_SHEET_ID,
                    range=f"'{cat}'!A1",
                    valueInputOption="RAW",
                    insertDataOption="INSERT_ROWS",
                    body={"values": r},
                )
                .execute()
            )
            total_written += len(rows)
            print(f"[Writer] {category}: {len(rows)} leads written.")

        print(f"[Writer] Done. {total_written} leads written to Google Sheets.")
