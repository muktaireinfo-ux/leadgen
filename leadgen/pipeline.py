
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

EUROPE_COUNTRY_CODES = {"uk", "de", "fr", "nl", "es", "it", "pl", "se"}


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


def _run_maps(
    industry: str,
    country: str,
    limit_per_location: int,
    max_locations: int,
    skip_enrichment: bool,
    enrich_workers: int,
) -> list[tuple]:
    """Inner run for Google Maps source."""
    primary, fallback_type = _get_maps_scraper()
    locations = _load_locations(country, max_locations)
    print(f"[Pipeline] Targeting {len(locations)} locations\n")


    all_classified: list[tuple[dict, str, str, bool]] = []


    for location in locations:
        print(f"[Pipeline] Scraping Maps: {location}")


        # Append full country name so Google Maps returns results from the right country
        country_suffix = COUNTRY_DISPLAY_NAMES.get(country.lower(), "")
        search_location = f"{location} {country_suffix}".strip() if country_suffix else location


        leads = _scrape_maps_with_fallback(primary, fallback_type, industry, search_location, limit_per_location)


        # Drop results that clearly belong to a different country (wrong phone format / .in domain)
        before = len(leads)
        leads = [lead for lead in leads if _is_local_country(lead, country.lower())]
        dropped = before - len(leads)
        if dropped:
            print(f"[Pipeline] Filtered {dropped} leads with non-{country.upper()} phone/domain")


        if not leads:
            print(f"[Pipeline] No results for {location}")
            continue


        print(f"[Pipeline] Got {len(leads)} leads — enriching...")


        if skip_enrichment:
            enriched_leads = leads
        else:
            with ThreadPoolExecutor(max_workers=enrich_workers, thread_name_prefix='enrich') as pool:
                futures = {pool.submit(_enrich_lead, lead): lead for lead in leads}
                for future in as_completed(futures):
                    enriched_leads = future.result()


        for enriched in [enriched_leads]:
            try:
                category, evidence, is_inferred = classifier.classify(enriched)
                all_classified.append((enriched, category, evidence, is_inferred))
            except Exception as e:
                print(f"[Classifier] Error for '{enriched.get('name')}': {e}")


        time.sleep(random.uniform(1.0, 2.5))


    return all_classified


# ... (rest of the code remains the same)
