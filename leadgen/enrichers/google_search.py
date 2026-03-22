"""Yahoo search enrichment — fills missing phone, email, website, address.

Uses Yahoo Search (no API key, no TLS 1.3 requirement, no CAPTCHA).
Runs after the website enricher, only for leads still missing key fields.
"""

import re
import time
import urllib.parse
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-GB,en;q=0.9",
})

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.IGNORECASE)
_PHONE_RE = re.compile(r"\+?[\d][\d\s\-().]{6,18}[\d]")

# Aggregator/directory sites — not the business website
_DIRECTORY_DOMAINS = {
    "yelp.com", "google.com", "facebook.com", "instagram.com",
    "twitter.com", "linkedin.com", "tripadvisor.com", "yellowpages.com",
    "trustpilot.com", "bark.com", "checkatrade.com", "yell.com",
    "indeed.com", "glassdoor.com", "companies-house.gov.uk", "bing.com",
    "wikipedia.org", "mapquest.com", "foursquare.com", "nextdoor.com",
    "zocdoc.com", "healthgrades.com", "nhs.uk", "amazon.com", "ebay.com",
    "apple.com", "microsoft.com", "yahoo.com", "reddit.com", "baidu.com",
    "maps.google.com", "dentistsaround.co.uk", "doctify.com",
    "whatclinic.com", "treatwell.co.uk", "booksy.com",
    "mynextdentist.co.uk", "allinlondon.co.uk", "freemap.co.uk",
}

_DIRECTORY_KEYWORDS = (
    "directory", "listing", "finder", "near-me", "nearme",
    "reviews", "checka", "getmy", "compare", "dentists-",
    "-dentists", "find-a-", "-near-", "locator",
)

_JUNK_EMAIL_DOMAINS = {
    "example.com", "sentry.io", "wix.com", "wordpress.com", "squarespace.com",
    "shopify.com", "google.com", "facebook.com", "schema.org", "w3.org",
    "apple.com", "microsoft.com", "amazonaws.com", "cloudflare.com",
}


def _yahoo_search(query: str, max_results: int = 5) -> list[dict]:
    """Fetch Yahoo search results and return list of {href, title, body}."""
    url = f"https://search.yahoo.com/search?p={urllib.parse.quote_plus(query)}&n={max_results}"
    try:
        resp = _SESSION.get(url, timeout=10)
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "lxml")
        results = []
        for div in soup.select("div.algo"):
            a = div.find("a", href=True)
            desc = div.find("p") or div.find("div", class_="compText")
            if not a:
                continue
            # Yahoo wraps links through r.search.yahoo.com — extract real URL
            href = a["href"]
            m = re.search(r"RU=([^/&]+)", href)
            real_url = urllib.parse.unquote(m.group(1)) if m else href

            results.append({
                "href": real_url,
                "title": a.get_text(strip=True),
                "body": desc.get_text(strip=True) if desc else "",
            })
            if len(results) >= max_results:
                break
        return results
    except Exception:
        return []


def _is_directory(url: str) -> bool:
    try:
        domain = urlparse(url).netloc.replace("www.", "").lower()
        if any(d in domain for d in _DIRECTORY_DOMAINS):
            return True
        if any(kw in domain or kw in url.lower() for kw in _DIRECTORY_KEYWORDS):
            return True
        return False
    except Exception:
        return True


def _domain_matches_name(url: str, business_name: str) -> bool:
    """Return True if the URL domain plausibly belongs to this business."""
    try:
        domain = urlparse(url).netloc.replace("www.", "").lower()
        domain_str = re.sub(r"[\.\-_]", " ", domain)
        name_words = [w.lower() for w in re.split(r"\W+", business_name) if len(w) > 3]
        stopwords = {
            "dental", "clinic", "care", "health", "practice", "surgery", "centre",
            "center", "group", "house", "services", "solutions", "limited", "ltd",
        }
        meaningful = [w for w in name_words if w not in stopwords]
        if not meaningful:
            meaningful = name_words
        return any(w in domain_str for w in meaningful)
    except Exception:
        return False


def _title_matches_name(title: str, business_name: str) -> bool:
    """Return True if the result title contains key words from the business name."""
    try:
        title_lower = title.lower()
        name_words = [w.lower() for w in re.split(r"\W+", business_name) if len(w) > 3]
        stopwords = {
            "dental", "clinic", "care", "health", "practice", "surgery", "centre",
            "center", "group", "house", "services", "solutions", "limited", "ltd",
        }
        meaningful = [w for w in name_words if w not in stopwords]
        if not meaningful:
            meaningful = name_words
        # Title must contain at least 2 meaningful words, or 1 if only 1 exists
        threshold = min(2, len(meaningful))
        matches = sum(1 for w in meaningful if w in title_lower)
        return matches >= threshold
    except Exception:
        return False


def _extract_email(text: str) -> str:
    for e in _EMAIL_RE.findall(text):
        e = e.lower()
        if any(e.endswith("@" + d) for d in _JUNK_EMAIL_DOMAINS):
            continue
        if e.endswith((".png", ".jpg", ".gif", ".svg")):
            continue
        if len(e) > 80:
            continue
        return e
    return ""


def _extract_phone(text: str) -> str:
    for m in _PHONE_RE.finditer(text):
        candidate = m.group().strip()
        if 7 <= sum(c.isdigit() for c in candidate) <= 15:
            return candidate
    return ""


def _scrape_contact(url: str) -> tuple[str, str]:
    """Quick scrape of /contact and /about pages for email + phone."""
    email, phone = "", ""
    base = url.rstrip("/")
    for path in ["/contact", "/contact-us", "/about", "/about-us"]:
        try:
            resp = _SESSION.get(f"{base}{path}", timeout=5)
            if resp.status_code == 200 and len(resp.text) > 200:
                if not email:
                    email = _extract_email(resp.text)
                if not phone:
                    phone = _extract_phone(resp.text)
                if email and phone:
                    break
        except Exception:
            continue
    return email, phone


def enrich_via_search(lead: dict) -> dict:
    """Fill in missing fields by searching Yahoo for the business name.

    Fields filled (only when currently empty):
      website, phone, email, address
    """
    needs_website = not lead.get("website")
    needs_phone = not lead.get("phone")
    needs_email = not lead.get("email")
    needs_address = not lead.get("address")

    if not any([needs_website, needs_phone, needs_email, needs_address]):
        return lead

    name = (lead.get("name") or "").strip()
    if not name:
        return lead

    location_hint = (lead.get("address") or lead.get("city") or "").strip()
    query = f'"{name}"' + (f" {location_hint}" if location_hint else "")

    results = _yahoo_search(query, max_results=5)
    updates = {}

    for r in results:
        href = r.get("href", "")
        snippet = f"{r.get('title', '')} {r.get('body', '')}"

        # Website: first non-directory URL that either matches the domain name
        # OR whose result title contains key words from the business name
        if needs_website and href and not _is_directory(href):
            title = r.get("title", "")
            if _domain_matches_name(href, name) or _title_matches_name(title, name):
                updates["website"] = href
                needs_website = False

        if needs_email:
            email = _extract_email(snippet)
            if email:
                updates["email"] = email
                needs_email = False

        if needs_phone:
            phone = _extract_phone(snippet)
            if phone:
                updates["phone"] = phone
                needs_phone = False

        if needs_address and r.get("body"):
            m = re.search(r"\d+\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}", r["body"])
            if m:
                updates["address"] = m.group().strip()
                needs_address = False

        if not any([needs_website, needs_phone, needs_email, needs_address]):
            break

    # If a website was found/known and email/phone still missing, scrape contact page
    found_website = updates.get("website") or lead.get("website")
    if found_website and (needs_email or needs_phone):
        email, phone = _scrape_contact(found_website)
        if needs_email and email:
            updates["email"] = email
        if needs_phone and phone:
            updates["phone"] = phone

    if updates:
        print(f"[SearchEnricher] '{name}': filled {list(updates.keys())}")

    time.sleep(0.3)  # polite rate limit

    return {**lead, **updates}
