"""Outscraper API — primary Google Maps scraper.

Free tier: 500 records/month. Paid: $3 per 1,000 leads.
Sign up at outscraper.com to get your API key.

Why primary: documented REST API, reliable, 500 free records/month is enough to start,
handles anti-bot and proxy rotation internally.
"""

from leadgen.scrapers.base import BaseScraper
from leadgen import config


class OutscraperMapsScraper(BaseScraper):

    def __init__(self):
        self.api_key = config.OUTSCRAPER_API_KEY
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from outscraper import ApiClient
                self._client = ApiClient(api_key=self.api_key)
            except ImportError:
                raise RuntimeError("outscraper package not installed. Run: pip install outscraper")
        return self._client

    def scrape(self, query: str, location: str, limit: int = 100) -> list[dict]:
        if not self.api_key:
            print("[Outscraper] No API key set — returning empty. Set OUTSCRAPER_API_KEY in .env")
            return []

        try:
            client = self._get_client()
            results = client.google_maps_search(
                f"{query} {location}",
                limit=limit,
                language="en",
            )
            raw = results[0] if results and isinstance(results[0], list) else results
        except Exception as e:
            print(f"[Outscraper] Error scraping '{query}' in '{location}': {e}")
            return []

        return [self._normalize(item) for item in raw]

    def _normalize(self, item: dict) -> dict:
        social = []
        for field in ("linkedin", "twitter", "facebook", "instagram"):
            val = item.get(field)
            if val:
                social.append(val)

        return {
            "name": item.get("name", ""),
            "address": item.get("full_address", item.get("address", "")),
            "city": item.get("city", ""),
            "country": item.get("country", ""),
            "phone": item.get("phone", ""),
            "email": item.get("email", ""),
            "website": item.get("site", ""),
            "rating": item.get("rating"),
            "review_count": item.get("reviews"),
            "category": item.get("category", ""),
            "social_links": social,
            "scraper": "outscraper",
        }
