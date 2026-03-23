
"""Main pipeline: scrape → enrich (concurrent) → classify → batch write.

Key design decisions:
- source="maps"      → Outscraper primary, Botasaurus fallback (local browser)
- source="instagram" → Apify hashtag + profile scraper
- Enrichment is concurrent (ThreadPoolExecutor, 8 workers) — 10x faster
- Sheets writes are batched — one API call per category instead of one per lead
- Retry logic on scraper calls via retry_with_backoff
"""

import csv
import re
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from leadgen import config
from leadgen.enrichers import website as enricher
from leadgen.enrichers import google_search as search_enricher
from leadgen import classifier
from leadgen.writer import SheetsWriter
from leadgen.utils import retry_with_backoff

# ... (rest of the code remains the same)

def _enrich_lead(lead: dict) -> dict:
    """Enrich one lead — runs in thread pool.

    Step 1: Website enricher (CTA, blog, ecommerce, social links, email from HTML)
    Step 2: DuckDuckGo search enricher (fills any remaining blanks: website, phone, email, address)
    """
    # Instagram leads already have their key data; still try website enrichment
    # if there's a website in the bio.
    try:
        lead = enricher.enrich(lead)
    except Exception as e:
        print(f"[Enricher] Error for '{lead.get('name')}': {e}")

    try:
        lead = search_enricher.enrich_via_search(lead)
    except Exception as e:
        print(f"[SearchEnricher] Error for '{lead.get('name')}': {e}")

    return lead

# ... (rest of the code remains the same)

class ThreadWithResult(Thread):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self_result = None
        self.exception = None

    def run(self):
        try:
            self.result = self._target(*self._args, **self._kwargs)
        except Exception as e:
            self.exception = e

    def join(self):
        super().join()
        if self.exception:
            raise self.exception
        return self.result

# Replace ThreadPoolExecutor with threads to avoid GIL limitation in concurrency
def run(
    industry: str,
    country: str,
    limit_per_location: int = 50,
    max_locations: int = 20,
    skip_enrichment: bool = False,
    source: str = "maps",  # "maps" or "instagram"
) -> dict:
    """Run the full pipeline for one industry + country combination.

    Args:
        source: "maps" uses Google Maps (Outscraper → Botasaurus fallback).
                "instagram" uses Apify hashtag + profile scraper.

    Returns:
        summary dict: {category: count_added}
    """
    print(f"\n{'='*60}")
    print(f"Lead Gen Pipeline: '{industry}' in '{country.upper()}' [{source}]")
    print(f"{'='*60}")

    writer = SheetsWriter()
    summary: dict[str, int] = {cat: 0 for cat in config.CATEGORIES}

    if source == "instagram":
        all_classified = _run_instagram(
            industry, country, limit_per_location, max_locations,
            skip_enrichment,
        )
    else:
        all_classified = _run_maps(
            industry, country, limit_per_location, max_locations,
            skip_enrichment,
        )

    for _, category, _, _ in all_classified:
        summary[category] = summary.get(category, 0) + 1

    print(f"\n[Pipeline] Writing {sum(summary.values())} leads to Google Sheets...")
    writer.write_batch(all_classified)

    print(f"\n{'='*60}")
    print("Pipeline complete. Leads added per category:")
    for cat, count in summary.items():
        if count > 0:
            print(f"  {cat}: {count}")
    print(f"{'='*60}\n")

    return summary
