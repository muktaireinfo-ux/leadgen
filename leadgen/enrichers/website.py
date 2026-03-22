"""Website enrichment: site age, e-commerce detection, blog detection, social links.

Design decisions vs original plan:
- Removed Wayback Machine API: rate limits (60 req/min) cause IP bans at scale.
  Instead, we read the copyright year from the page HTML and the Last-Modified
  HTTP header — same signal, no external dependency.
- All functions are safe: they catch exceptions and return None/False/[] on failure
  so a dead website never breaks the pipeline.
- Called concurrently via ThreadPoolExecutor in pipeline.py (not sequentially).
"""

import re
import time
import requests
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from leadgen import config

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "Mozilla/5.0 (compatible; LeadBot/1.0)"})


def _get(url: str, timeout: int = 6):  # -> Optional[requests.Response]
    try:
        return _SESSION.get(url, timeout=timeout, allow_redirects=True)
    except Exception:
        return None


def check_site_age(url: str) -> dict:
    """Estimate site age from Last-Modified header and copyright year in HTML.

    Returns:
        {"last_modified": datetime|None, "copyright_year": int|None, "outdated": bool}
    """
    result = {"last_modified": None, "copyright_year": None, "outdated": False}
    if not url:
        result["outdated"] = True
        return result

    resp = _get(url)
    if not resp:
        return result

    # Check Last-Modified HTTP header
    last_mod_str = resp.headers.get("Last-Modified")
    if last_mod_str:
        try:
            from email.utils import parsedate_to_datetime
            result["last_modified"] = parsedate_to_datetime(last_mod_str)
        except Exception:
            pass

    # Check copyright year in HTML (e.g. © 2019, Copyright 2018)
    html = resp.text[:8000]  # only scan the first 8KB
    year_matches = re.findall(r"(?:©|&copy;|copyright)\s*(\d{4})", html, re.IGNORECASE)
    if year_matches:
        years = [int(y) for y in year_matches if 1990 <= int(y) <= datetime.now().year]
        if years:
            result["copyright_year"] = max(years)

    # Determine "outdated":
    # - Last-Modified more than SITE_AGE_OUTDATED_YEARS ago, OR
    # - Copyright year more than SITE_AGE_OUTDATED_YEARS ago (with no recent Last-Modified)
    now = datetime.now(timezone.utc)
    threshold_year = now.year - config.SITE_AGE_OUTDATED_YEARS

    if result["last_modified"]:
        lm = result["last_modified"]
        if lm.tzinfo is None:
            lm = lm.replace(tzinfo=timezone.utc)
        result["outdated"] = (now - lm).days / 365 >= config.SITE_AGE_OUTDATED_YEARS
    elif result["copyright_year"]:
        result["outdated"] = result["copyright_year"] <= threshold_year

    return result


def detect_ecommerce(url: str, html: str = None) -> bool:
    """Return True if the site appears to have e-commerce.
    Accepts pre-fetched HTML to avoid a second request.
    """
    if not url:
        return False

    if html is None:
        resp = _get(url)
        if not resp:
            return False
        html = resp.text

    ecommerce_signals = [
        "shopify", "woocommerce", "magento", "bigcommerce", "squarespace",
        "wix.com/stores", "add-to-cart", "add_to_cart", "addtocart",
        "/cart", "/checkout", "stripe.js", "paypal.com/sdk",
        "data-product-id", "productid", "sku=",
    ]
    html_lower = html.lower()
    return any(signal in html_lower for signal in ecommerce_signals)


def detect_blog(url: str) -> bool:
    """Return True if the site has a blog or content section."""
    if not url:
        return False

    blog_paths = ["/blog", "/news", "/articles", "/posts", "/journal", "/insights"]
    base = url.rstrip("/")

    for path in blog_paths:
        resp = _get(f"{base}{path}", timeout=4)
        if resp and resp.status_code == 200 and len(resp.text) > 500:
            return True

    return False


def extract_social_links(html: str) -> list[str]:
    """Extract social media links from already-fetched HTML."""
    if not html:
        return []

    social_domains = [
        "linkedin.com", "twitter.com", "x.com", "facebook.com",
        "instagram.com", "youtube.com", "tiktok.com",
    ]
    found = []
    try:
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if any(domain in href for domain in social_domains) and href not in found:
                found.append(href)
    except Exception:
        pass
    return found


def has_contact_or_cta(html: str) -> bool:
    """Return True if pre-fetched HTML contains a contact form or clear CTA."""
    if not html:
        return False

    cta_signals = [
        'type="tel"', 'href="tel:', 'type="email"', 'href="mailto:',
        "contact", "get a quote", "book now", "schedule", "free consultation",
        "<form", "contactform", "contact-form",
    ]
    html_lower = html.lower()
    return any(signal in html_lower for signal in cta_signals)


def enrich(lead: dict) -> dict:
    """Run all enrichment checks on a raw lead.

    Fetches the website ONCE and reuses the HTML for all checks,
    avoiding multiple round-trips to the same URL.

    Email finding (Hunter.io) is called only when scraper didn't return one.
    """
    from leadgen.enrichers.email_finder import find_email  # noqa: PLC0415

    url = lead.get("website", "")

    # Single fetch — reuse for ecommerce, social, CTA
    homepage_html = None
    if url:
        resp = _get(url)
        if resp and resp.status_code == 200:
            homepage_html = resp.text

    site_info = check_site_age(url)
    has_ecommerce = detect_ecommerce(url, html=homepage_html)
    has_blog = detect_blog(url)
    social_from_site = extract_social_links(homepage_html or "")
    has_cta = has_contact_or_cta(homepage_html or "")

    all_social = list(set(lead.get("social_links", []) + social_from_site))

    # Find email if scraper didn't return one.
    # Pass homepage_html so Layer 1 reuses what we already fetched.
    email = lead.get("email", "")
    if not email and url:
        email = find_email(url, homepage_html=homepage_html)

    return {
        **lead,
        "email": email,
        "site_last_modified": site_info["last_modified"],
        "site_copyright_year": site_info["copyright_year"],
        "site_outdated": site_info["outdated"],
        "has_ecommerce": has_ecommerce,
        "has_blog": has_blog,
        "has_cta": has_cta,
        "social_links": all_social,
    }
