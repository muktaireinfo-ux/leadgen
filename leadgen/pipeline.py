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

# Full country names appended to location strings so Google Maps returns local results
COUNTRY_DISPLAY_NAMES = {
    "uk": "United Kingdom",
    "de": "Germany",
    "fr": "France",
    "nl": "Netherlands",
    "es": "Spain",
    "it": "Italy",
    "pl": "Poland",
    "se": "Sweden",
}

# Expected phone prefix patterns per country — used to filter wrong-country results
_COUNTRY_PHONE_RE = {
    "uk": re.compile(r"^\+44|^0[1-9]"),
    "de": re.compile(r"^\+49|^0[1-9]"),
    "fr": re.compile(r"^\+33|^0[1-9]"),
    "nl": re.compile(r"^\+31|^0[1-9]"),
    "es": re.compile(r"^\+34|^[6-9]\d"),
    "it": re.compile(r"^\+39|^0[1-9]"),
    "pl": re.compile(r"^\+48|^0[1-9]"),
    "se": re.compile(r"^\+46|^0[1-9]"),
}


def _is_local_country(lead: dict, country: str) -> bool:
    """Return False if the lead's phone/website clearly belongs to a different country."""
    pattern = _COUNTRY_PHONE_RE.get(country)
    if not pattern:
        return True  # no filter for US or unknown countries

    phone = re.sub(r"[\s\-().]", "", lead.get("phone") or "")
    website = lead.get("website") or ""

    # Hard reject: website TLD is .in (India)
    from urllib.parse import urlparse  # noqa: PLC0415
    domain = urlparse(website).netloc if website else ""
    if domain.endswith(".in") or domain.endswith(".co.in"):
        return False

    # Soft reject: phone is set and doesn't match expected country format
    if phone and not pattern.match(phone):
        return False

    return True

EUROPE_FALLBACKS = {
    "uk": ["London", "Manchester", "Birmingham", "Leeds", "Glasgow", "Bristol", "Edinburgh"],
    "de": ["Berlin", "Hamburg", "München", "Köln", "Frankfurt", "Stuttgart", "Düsseldorf"],
    "fr": ["Paris", "Lyon", "Marseille", "Toulouse", "Nice", "Nantes", "Strasbourg"],
    "nl": ["Amsterdam", "Rotterdam", "Den Haag", "Utrecht", "Eindhoven"],
    "es": ["Madrid", "Barcelona", "Valencia", "Sevilla", "Zaragoza", "Málaga"],
    "it": ["Roma", "Milano", "Napoli", "Torino", "Palermo", "Genova"],
    "pl": ["Warszawa", "Kraków", "Łódź", "Wrocław", "Poznań"],
    "se": ["Stockholm", "Göteborg", "Malmö", "Uppsala", "Västerås"],
}

US_FALLBACKS = [
    "New York NY", "Los Angeles CA", "Chicago IL", "Houston TX", "Phoenix AZ",
    "Philadelphia PA", "San Antonio TX", "San Diego CA", "Dallas TX", "Austin TX",
    "Jacksonville FL", "Fort Worth TX", "Columbus OH", "Charlotte NC", "Indianapolis IN",
    "San Francisco CA", "Seattle WA", "Denver CO", "Nashville TN", "Oklahoma City OK",
]


def _load_locations(country: str, max_locations: int) -> list[str]:
    country = country.lower()

    if country == "us":
        path = config.ZIP_CODES_DIR / "us_zips.csv"
        if path.exists():
            zips = []
            with open(path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    z = row.get("zip", row.get("zipcode", "")).strip()
                    if z:
                        zips.append(z)
            if zips:
                return zips[:max_locations]
        return US_FALLBACKS[:max_locations]

    if country in EUROPE_COUNTRY_CODES:
        path = config.ZIP_CODES_DIR / "europe_postcodes.csv"
        if path.exists():
            codes = []
            with open(path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if row.get("country_code", "").lower() == country:
                        pc = row.get("postcode", row.get("postal_code", "")).strip()
                        if pc:
                            codes.append(pc)
            if codes:
                return codes[:max_locations]
        return EUROPE_FALLBACKS.get(country, [country])[:max_locations]

    # Treat as city name / custom location
    return [country]


def _get_maps_scraper():
    """Return primary scraper. Outscraper if key set, otherwise Botasaurus."""
    if config.OUTSCRAPER_API_KEY:
        from leadgen.scrapers.outscraper import OutscraperMapsScraper  # noqa: PLC0415
        return OutscraperMapsScraper(), "botasaurus"  # (primary, fallback_type)

    print(
        "[Pipeline] OUTSCRAPER_API_KEY not set — using Botasaurus (local browser).\n"
        "           This is slower but has no monthly quota."
    )
    from leadgen.scrapers.botasaurus_maps import BotasurusMapscraper  # noqa: PLC0415
    return BotasurusMapscraper(), None  # no fallback when Botasaurus is already primary


def _scrape_maps_with_fallback(primary, fallback_type, industry: str, location: str, limit: int) -> list[dict]:
    """Try primary scraper; fall back to Botasaurus only if Outscraper was primary."""
    leads = retry_with_backoff(
        lambda: primary.scrape(industry, location, limit=limit),
        retries=2,
    )
    if leads:
        return leads

    if fallback_type == "botasaurus":
        try:
            from leadgen.scrapers.botasaurus_maps import BotasurusMapscraper  # noqa: PLC0415
            print("[Pipeline] Outscraper returned empty — trying Botasaurus fallback...")
            bot = BotasurusMapscraper()
            return bot.scrape(industry, location, limit=limit)
        except Exception as e:
            print(f"[Pipeline] Botasaurus fallback failed: {e}")

    return []


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
            enriched_leads = []
            with ThreadPoolExecutor(max_workers=enrich_workers) as pool:
                futures = {pool.submit(_enrich_lead, lead): lead for lead in leads}
                for future in as_completed(futures):
                    enriched_leads.append(future.result())

        for enriched in enriched_leads:
            try:
                category, evidence, is_inferred = classifier.classify(enriched)
                all_classified.append((enriched, category, evidence, is_inferred))
            except Exception as e:
                print(f"[Classifier] Error for '{enriched.get('name')}': {e}")

        time.sleep(random.uniform(1.0, 2.5))

    return all_classified


def _run_instagram(
    industry: str,
    country: str,
    limit_per_location: int,
    max_locations: int,
    skip_enrichment: bool,
    enrich_workers: int,
) -> list[tuple]:
    """Inner run for Instagram source."""
    from leadgen.scrapers.apify_instagram import ApifyInstagramScraper  # noqa: PLC0415

    scraper = ApifyInstagramScraper()

    # For Instagram, "location" is used to build location-aware hashtags.
    # We search once per location (or just once if location is generic).
    locations = _load_locations(country, max_locations)
    # For Instagram, a handful of representative locations is enough —
    # hashtags aren't strictly geo-bound. Cap at 3.
    ig_locations = locations[:3] if len(locations) > 3 else locations

    all_classified: list[tuple[dict, str, str, bool]] = []
    seen_usernames: set[str] = set()

    for location in ig_locations:
        print(f"[Pipeline] Scraping Instagram: {industry} near {location}")

        leads = scraper.scrape(industry, location, limit=limit_per_location)

        # Dedup across location iterations by Instagram username
        new_leads = []
        for lead in leads:
            uname = lead.get("instagram_username", "")
            if uname and uname not in seen_usernames:
                seen_usernames.add(uname)
                new_leads.append(lead)
            elif not uname:
                new_leads.append(lead)
        leads = new_leads

        if not leads:
            print(f"[Pipeline] No Instagram results for {location}")
            continue

        print(f"[Pipeline] Got {len(leads)} Instagram leads — enriching websites...")

        # Enrich website if bio has one (same enricher, skips leads without website)
        if skip_enrichment:
            enriched_leads = leads
        else:
            enriched_leads = []
            with ThreadPoolExecutor(max_workers=enrich_workers) as pool:
                futures = {pool.submit(_enrich_lead, lead): lead for lead in leads}
                for future in as_completed(futures):
                    enriched_leads.append(future.result())

        for enriched in enriched_leads:
            try:
                category, evidence, is_inferred = classifier.classify(enriched)
                all_classified.append((enriched, category, evidence, is_inferred))
            except Exception as e:
                print(f"[Classifier] Error for '{enriched.get('name')}': {e}")

        time.sleep(random.uniform(2.0, 4.0))

    return all_classified


def run(
    industry: str,
    country: str,
    limit_per_location: int = 50,
    max_locations: int = 20,
    skip_enrichment: bool = False,
    enrich_workers: int = 8,
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
            skip_enrichment, enrich_workers,
        )
    else:
        all_classified = _run_maps(
            industry, country, limit_per_location, max_locations,
            skip_enrichment, enrich_workers,
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
