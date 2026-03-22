import concurrent.futures

class Pipeline:
    def __init__(self, industry: str, country: str, limit_per_location: int = 50,
                 max_locations: int = 20, skip_enrichment: bool = False,
                 enrich_workers: int = 8, source: str = "maps"):
        self.industry = industry
        self.country = country
        self.limit_per_location = limit_per_location
        self.max_locations = max_locations
        self.skip_enrichment = skip_enrichment
        self.enrich_workers = enrich_workers
        self.source = source

    def run(self) -> dict:
        # ... (rest of the class remains the same)

    def _run_maps(self, location: str) -> list[tuple[dict, str, str, bool]]:
        primary, fallback_type = _get_maps_scraper()
        leads = _scrape_maps_with_fallback(primary, fallback_type, self.industry, location, self.limit_per_location)
        # ... (rest of the method remains the same)

    def _run_instagram(self, location: str) -> list[tuple[dict, str, str, bool]]:
        scraper = ApifyInstagramScraper()
        leads = scraper.scrape(self.industry, location, limit=self.limit_per_location)
        # ... (rest of the method remains the same)

    def _enrich_lead(self, lead: dict) -> dict:
        try:
            lead = enricher.enrich(lead)
        except Exception as e:
            print(f"[Enricher] Error for '{lead.get('name')}': {e}")
        try:
            lead = search_enricher.enrich_via_search(lead)
        except Exception as e:
            print(f"[SearchEnricher] Error for '{lead.get('name')}': {e}")
        return lead

    def _scrape_maps_with_fallback(self, primary, fallback_type, industry: str, location: str, limit: int) -> list[dict]:
        leads = retry_with_backoff(
            lambda: primary.scrape(industry, location, limit=limit),
            retries=2,
        )
        if leads:
            return leads
        # ... (rest of the method remains the same)
