"""Primary Google Maps scraper using omkarcloud's Google Maps Extractor API.

Free tier: 200 searches/month at omkar.cloud
Extracts 50+ data points including emails and social profiles.
Falls back gracefully so the pipeline can use Outscraper instead.
"""

import requests
from leadgen.scrapers.base import BaseScraper
from leadgen import config


class OmkarMapsScraper(BaseScraper):
    API_URL = "https://www.omkar.cloud/tools/google-maps-extractor-api/api/search"

    def __init__(self):
        self.api_key = config.OMKAR_API_KEY

    def scrape(self, query: str, location: str, limit: int = 100) -> list[dict]:
        if not self.api_key:
            return []

        try:
            resp = requests.post(
                self.API_URL,
                json={"query": f"{query} in {location}", "limit": limit},
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=60,
            )
            resp.raise_for_status()
            raw = resp.json().get("data", [])
        except Exception as e:
            print(f"[OmkarMaps] Error scraping '{query}' in '{location}': {e}")
            return []

        return [self._normalize(item) for item in raw]

    def _normalize(self, item: dict) -> dict:
        social = []
        for field in ("linkedin", "twitter", "facebook", "instagram", "youtube"):
            val = item.get(field)
            if val:
                social.append(val)

        return {
            "name": item.get("name", ""),
            "address": item.get("address", ""),
            "city": item.get("city", "") or item.get("address", ""),
            "country": item.get("country", ""),
            "phone": item.get("phone", "") or item.get("phone_number", ""),
            "email": item.get("email", "") or item.get("emails", [""])[0] if item.get("emails") else "",
            "website": item.get("website", "") or item.get("site", ""),
            "rating": item.get("rating"),
            "review_count": item.get("reviews") or item.get("reviews_count"),
            "category": item.get("category", "") or item.get("type", ""),
            "social_links": social,
            "scraper": "omkarcloud",
        }
