"""Email finding — layered approach, no Hunter.io.

Why not Hunter/Snov/Apollo for our use case:
  Those tools index emails from large corporations and SaaS companies.
  Our targets are local SMBs (restaurants, salons, dentists) — rarely in their databases.

Our approach (two layers, both essentially free):
  Layer 1: Regex scrape of HTML we already have (homepage + /contact page).
           Free, unlimited, catches ~30-50% of local business emails.
  Layer 2: Outscraper Emails & Contacts Scraper.
           Separate free tier of 500 domains/month (independent of Maps quota).
           Actively crawls the site + pulls Facebook/LinkedIn/Google signals.
           Catches another ~20-30% that Layer 1 misses.
"""

import re
import requests
from leadgen import config

# RFC-5322 simplified email regex — catches most real-world emails
_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)

# Domains to ignore — these are not business emails
_IGNORE_DOMAINS = {
    "example.com", "sentry.io", "wix.com", "wordpress.com", "squarespace.com",
    "shopify.com", "google.com", "facebook.com", "instagram.com", "twitter.com",
    "linkedin.com", "youtube.com", "adobe.com", "jquery.com", "schema.org",
    "w3.org", "apple.com", "microsoft.com", "amazonaws.com", "cloudflare.com",
}

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "Mozilla/5.0 (compatible; LeadBot/1.0)"})


def _extract_from_html(html: str, domain: str) -> str:
    """Pull the best email from raw HTML using regex.

    Prefers emails matching the business domain. Falls back to any found email
    that isn't from a known platform/CDN domain.
    """
    if not html:
        return ""

    candidates = _EMAIL_RE.findall(html)
    if not candidates:
        return ""

    # Deduplicate preserving order
    seen = set()
    unique = []
    for email in candidates:
        email_lower = email.lower()
        if email_lower not in seen:
            seen.add(email_lower)
            unique.append(email_lower)

    # Filter out noise
    filtered = [
        e for e in unique
        if not any(e.endswith("@" + d) or ("@" + d + ".") in e for d in _IGNORE_DOMAINS)
        and not e.endswith(".png") and not e.endswith(".jpg")
        and len(e) < 80  # sanity length check
    ]

    if not filtered:
        return ""

    # Prefer an email that matches the business domain
    if domain:
        domain_clean = domain.replace("www.", "").split("/")[0]
        for e in filtered:
            if domain_clean in e:
                return e

    return filtered[0]


def _scrape_contact_page(base_url: str) -> str:
    """Try /contact, /contact-us, /about paths and extract email from HTML."""
    paths = ["/contact", "/contact-us", "/contactus", "/about", "/about-us", "/reach-us"]
    domain = base_url.replace("https://", "").replace("http://", "").split("/")[0]

    for path in paths:
        try:
            resp = _SESSION.get(f"{base_url.rstrip('/')}{path}", timeout=5)
            if resp.status_code == 200:
                email = _extract_from_html(resp.text, domain)
                if email:
                    return email
        except Exception:
            continue

    return ""


def find_email_direct(website_url: str, homepage_html: str = None) -> str:
    """Layer 1: Extract email from HTML we already have + contact page probe.

    Args:
        website_url:   full URL of the business website
        homepage_html: HTML already fetched during enrichment (avoids re-fetch)
    Returns:
        email string or ""
    """
    if not website_url:
        return ""

    domain = website_url.replace("https://", "").replace("http://", "").split("/")[0]

    # Try homepage HTML first (already fetched — free)
    if homepage_html:
        email = _extract_from_html(homepage_html, domain)
        if email:
            return email

    # Try contact/about pages
    return _scrape_contact_page(website_url)


def find_email_outscraper(website_url: str) -> str:
    """Layer 2: Use Outscraper's Emails & Contacts Scraper (500 free domains/month).

    Separate free quota from the Maps scraper — independent 500/month.
    Falls back silently if no API key or quota exceeded.
    """
    if not config.OUTSCRAPER_API_KEY or not website_url:
        return ""

    domain = website_url.replace("https://", "").replace("http://", "").split("/")[0]
    if not domain:
        return ""

    try:
        from outscraper import ApiClient
        client = ApiClient(api_key=config.OUTSCRAPER_API_KEY)
        results = client.emails_and_contacts([domain])

        if not results or not results[0]:
            return ""

        first = results[0][0] if isinstance(results[0], list) else results[0]
        emails = first.get("emails", [])
        if emails:
            # emails is a list of dicts with "value" key, or plain strings
            if isinstance(emails[0], dict):
                return emails[0].get("value", "")
            return emails[0]
    except Exception as e:
        # Quota exceeded, network error, etc. — fail silently
        if "quota" not in str(e).lower() and "429" not in str(e):
            print(f"[EmailFinder] Outscraper error for {domain}: {e}")

    return ""


def find_email(website_url: str, homepage_html: str = None) -> str:
    """Try Layer 1 (direct HTML scraping) then Layer 2 (Outscraper) for an email.

    Returns the first non-empty email found, or "" if both fail.
    """
    # Layer 1 — free, unlimited
    email = find_email_direct(website_url, homepage_html)
    if email:
        return email

    # Layer 2 — 500 free/month via Outscraper
    return find_email_outscraper(website_url)
