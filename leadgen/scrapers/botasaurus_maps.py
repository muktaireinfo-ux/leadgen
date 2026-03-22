"""Botasaurus-based Google Maps scraper — local, no quota, no API key.

Approach: one search results page → parse ALL data from cards via BeautifulSoup.
  - Name, rating, review count, category, phone, address: parsed from card text
  - Website URL: the "Website" link is directly in the card HTML
  - No navigating to individual place pages — fast and low-detection risk

Requires Chrome (already on Mac). Install: pip install botasaurus
"""

import re
import time
import urllib.parse
from bs4 import BeautifulSoup

from leadgen.scrapers.base import BaseScraper

# Regex to find a phone number in card text
_PHONE_RE = re.compile(r"(\+?[\d\s\-().]{7,20})")
# Regex for rating: "4.6"
_RATING_RE = re.compile(r"^(\d\.\d)$")
# Regex for review count like "(278)" or "278 reviews"
_REVIEWS_RE = re.compile(r"\((\d[\d,]*)\)")


def _parse_card(card) -> dict:
    """Extract all available fields from a search-result card BeautifulSoup element."""
    # Website URL — the explicit "Website" anchor in the card
    website = ""
    for a in card.find_all("a", href=True):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        if "Website" in text and href.startswith("http"):
            website = href
            break

    # Place page URL (for dedup key — not navigated to)
    place_url = ""
    main_link = card.find("a", class_="hfpxzc")
    if main_link:
        place_url = main_link.get("href", "")

    # Name
    name_el = card.find(class_="qBF1Pd")
    name = name_el.get_text(strip=True) if name_el else ""

    # Parse card text tokens for rating, review count, phone, category, address
    text_parts = [t.strip() for t in card.get_text(separator="|").split("|") if t.strip()]

    rating = None
    review_count = None
    phone = ""
    category = ""

    for part in text_parts:
        if _RATING_RE.match(part):
            rating = float(part)
            continue
        m = _REVIEWS_RE.search(part)
        if m and review_count is None:
            review_count = int(m.group(1).replace(",", ""))
            continue
        # Phone: contains a digit sequence that looks like a phone
        if not phone and re.search(r"\+?[\d]{3}[\s\-.][\d]{3}", part):
            phone = part.strip()
            continue

    # Category is usually the first non-name, non-rating text token
    # that doesn't look like an address or phone
    skip = {name, str(rating), "·", "Website", "Directions", ""}
    for part in text_parts:
        if part in skip:
            continue
        if _RATING_RE.match(part):
            continue
        if re.search(r"\d{3}[\s\-.]?\d{4}", part):
            continue
        if re.search(r"\d+\s+\w", part) and len(part) > 6:
            # Looks like a street address — skip
            continue
        if any(c in part.lower() for c in ["open", "close", "am", "pm"]):
            continue
        category = part
        break

    # Address — look for the street number pattern
    address = ""
    for part in text_parts:
        if re.match(r"^\d+\s+\w", part) and len(part) > 5:
            address = part
            break

    return {
        "name": name,
        "address": address,
        "city": "",
        "country": "",
        "phone": phone,
        "email": "",
        "website": website,
        "rating": rating,
        "review_count": review_count,
        "category": category,
        "social_links": [],
        "scraper": "botasaurus",
        "_place_url": place_url,  # internal, stripped before returning
    }


def _parse_single_place(page_text: str) -> dict:
    """Parse a place detail page when Google shows one result directly."""
    lines = [l.strip() for l in page_text.splitlines() if l.strip()]
    if not lines:
        return {}

    name = lines[0] if lines else ""
    rating = None
    review_count = None
    phone = ""
    website = ""
    address = ""

    for line in lines:
        if not rating:
            m = re.match(r"^(\d\.\d)$", line)
            if m:
                rating = float(m.group(1))
                continue
        if not review_count:
            m = _REVIEWS_RE.search(line)
            if m:
                review_count = int(m.group(1).replace(",", ""))
                continue
        if not phone and re.search(r"\+?[\d]{3}[\s\-.][\d]{3}", line):
            phone = line
            continue
        if not website and re.match(r"[\w\-]+\.\w{2,}", line) and " " not in line:
            website = "https://" + line if not line.startswith("http") else line
            continue
        if not address and re.match(r"^\d+\s+\w", line):
            address = line
            continue

    if not name or not rating:
        return {}

    return {
        "name": name,
        "address": address,
        "city": "",
        "country": "",
        "phone": phone,
        "email": "",
        "website": website,
        "rating": rating,
        "review_count": review_count,
        "category": "",
        "social_links": [],
        "scraper": "botasaurus",
    }


class BotasurusMapscraper(BaseScraper):

    def scrape(self, query: str, location: str, limit: int = 100) -> list[dict]:
        try:
            from botasaurus.browser import browser, Driver  # noqa: PLC0415
        except ImportError:
            print("[Botasaurus] Not installed. Run: pip install botasaurus")
            return []

        search_query = f"{query} {location}"

        @browser(headless=True, block_images=True)
        def _run(driver: Driver, data: dict) -> list:
            businesses: list[dict] = []
            seen: set[str] = set()
            target = data["limit"]

            q = urllib.parse.quote_plus(data["query"])
            driver.get(f"https://www.google.com/maps/search/{q}")
            time.sleep(8)

            # If Google shows a single place detail instead of a list, extract it directly
            if driver.count(".Nv2PK") == 0:
                page_text = driver.run_js("return document.body.innerText")
                single = _parse_single_place(page_text or "")
                if single:
                    return [single]

            for _scroll in range(min(target // 5 + 4, 30)):
                feed_html = driver.run_js(
                    "var f=document.querySelector('[role=\"feed\"]');"
                    "return f ? f.innerHTML : '';"
                )
                if not feed_html:
                    break

                soup = BeautifulSoup(feed_html, "lxml")
                cards = soup.find_all(class_="Nv2PK")

                for card in cards:
                    if len(businesses) >= target:
                        break

                    biz = _parse_card(card)
                    name = biz.get("name", "")
                    if not name or name in seen:
                        continue

                    seen.add(name)
                    biz.pop("_place_url", None)
                    businesses.append(biz)
                    print(f"[Botasaurus] ✓ {name} | {biz['rating']} ⭐ | {biz['phone']} | {biz['website'][:40]}")

                if len(businesses) >= target:
                    break

                # Scroll feed for more
                try:
                    driver.run_js(
                        "var f=document.querySelector('[role=\"feed\"]');"
                        "if(f)f.scrollBy(0,2500);"
                    )
                    time.sleep(2.5)
                except Exception:
                    break

            return businesses

        try:
            result = _run({"query": search_query, "limit": limit})
            return result[:limit] if isinstance(result, list) else []
        except Exception as e:
            print(f"[Botasaurus] Scrape error: {e}")
            return []
