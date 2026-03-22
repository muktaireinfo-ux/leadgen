import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OUTSCRAPER_API_KEY = os.getenv("OUTSCRAPER_API_KEY", "")
HUNTER_API_KEY = os.getenv("HUNTER_API_KEY", "")  # hunter.io — 50 free domain searches/mo
APIFY_API_KEY = os.getenv("APIFY_API_KEY", "")     # apify.com — free tier ~$5/month credits
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_JSON",
    str(Path(__file__).parent.parent / "service_account.json"),
)

ZIP_CODES_DIR = Path(__file__).parent / "zip_codes"

# Google Sheet tab names — one per lead category
CATEGORIES = [
    "Website / Branding",
    "Sales / Marketing",
    "Social Media / SEO",
    "Google Reviews / Reputation",
    "No E-commerce",
    "Content / Blog",
    "Inferred",
]

# Column headers written to each sheet tab
SHEET_COLUMNS = [
    "Business Name",
    "City / Country",
    "Phone",
    "Email",
    "Website URL",
    "Google Rating",
    "Review Count",
    "Social Links",
    "Category",
    "Evidence",
    "Inferred",
    "Source",
    "Date Added",
]

# Industries known to sell physical products (used to detect missing e-commerce)
PRODUCT_SELLER_KEYWORDS = [
    "shop", "store", "boutique", "retail", "clothing", "furniture",
    "jewelry", "jewellery", "gift", "florist", "bakery", "deli",
    "hardware", "electronics", "toy", "book", "pet supply",
]

# Thresholds for automatic rule-based signals
REVIEW_LOW_THRESHOLD = 3.5       # rating below this → reputation issue
REVIEW_COUNT_LOW = 10            # fewer than this → reputation/social issue
SITE_AGE_OUTDATED_YEARS = 5      # site not updated in this many years → website issue
