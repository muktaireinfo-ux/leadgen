from abc import ABC, abstractmethod


class BaseScraper(ABC):
    """All scrapers return a list of raw lead dicts with a standard set of fields.

    Required keys in each returned dict:
        name (str), address (str), city (str), country (str),
        phone (str), email (str), website (str),
        rating (float|None), review_count (int|None),
        category (str), social_links (list[str])
    """

    @abstractmethod
    def scrape(self, query: str, location: str, limit: int = 100) -> list[dict]:
        """Scrape businesses matching query in location.

        Args:
            query:    industry search term, e.g. "restaurant"
            location: zip code, postal code, or city name
            limit:    max results to return
        Returns:
            list of raw lead dicts
        """
