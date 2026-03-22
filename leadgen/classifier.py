"""Lead classifier — fully rule-based, no API calls required.

When multiple rules match, a fixed priority order picks the most impactful
service need. This means the classifier works with zero API credits.

Priority order (most → least impactful for outreach):
  1. Website / Branding   — no online presence at all, biggest gap
  2. Google Reviews        — reputation actively hurting them
  3. No E-commerce         — clear revenue they're leaving on the table
  4. Social Media / SEO    — invisible online
  5. Sales / Marketing     — no conversion mechanism
  6. Content / Blog        — weakest signal, easy upsell
"""

from leadgen import config

# Priority order — first match wins when multiple rules fire
CATEGORY_PRIORITY = [
    "Website / Branding",
    "Google Reviews / Reputation",
    "No E-commerce",
    "Social Media / SEO",
    "Sales / Marketing",
    "Content / Blog",
]

RULE_BASED_CATEGORIES = {
    "Website / Branding": lambda lead: (
        not lead.get("website") or
        bool(lead.get("site_outdated"))
    ),
    "Google Reviews / Reputation": lambda lead: (
        (lead.get("rating") is not None and lead.get("rating") < config.REVIEW_LOW_THRESHOLD) or
        (lead.get("review_count") is not None and lead.get("review_count") < config.REVIEW_COUNT_LOW)
    ),
    "No E-commerce": lambda lead: (
        any(kw in (lead.get("category") or "").lower() for kw in config.PRODUCT_SELLER_KEYWORDS)
        and lead.get("has_ecommerce") is False
        and bool(lead.get("website"))
    ),
    "Social Media / SEO": lambda lead: (
        not lead.get("social_links") and
        lead.get("review_count") is not None and
        lead.get("review_count") < config.REVIEW_COUNT_LOW
    ),
    "Sales / Marketing": lambda lead: (
        bool(lead.get("website")) and lead.get("has_cta") is False
    ),
    "Content / Blog": lambda lead: (
        bool(lead.get("website")) and lead.get("has_blog") is False
    ),
}

# Instagram-specific overrides applied before the main rules
_IG_RULES = {
    "Website / Branding": lambda lead: lead.get("instagram_no_website") is True,
    "Social Media / SEO": lambda lead: lead.get("instagram_low_engagement") is True,
}


def classify(lead: dict) -> tuple[str, str, bool]:
    """Classify a lead. Returns (category, evidence, is_inferred)."""

    # Instagram-specific path
    if lead.get("scraper") == "apify_instagram":
        for cat in CATEGORY_PRIORITY:
            rule = _IG_RULES.get(cat)
            if rule:
                try:
                    if rule(lead):
                        return cat, _build_evidence(lead, cat), False
                except Exception:
                    pass
        # Default Instagram leads to Social Media / SEO
        return "Social Media / SEO", "Instagram business account — social media growth opportunity.", True

    # Standard path — run all rules, pick highest-priority match
    matched = []
    for category, rule in RULE_BASED_CATEGORIES.items():
        try:
            if rule(lead):
                matched.append(category)
        except Exception:
            pass

    if not matched:
        # No rule fired — use best available heuristic
        return _heuristic_classify(lead)

    # Pick the highest-priority match
    for cat in CATEGORY_PRIORITY:
        if cat in matched:
            evidence = _build_evidence(lead, cat)
            is_inferred = len(matched) > 1  # mark inferred if multiple rules fired
            return cat, evidence, is_inferred

    # Fallback (should never reach here)
    return matched[0], _build_evidence(lead, matched[0]), True


def _heuristic_classify(lead: dict) -> tuple[str, str, bool]:
    """Fallback when no rule fires — use soft signals."""
    if not lead.get("website"):
        return "Website / Branding", "No website found in Google Maps listing.", False

    if lead.get("rating") and lead.get("rating") < 4.0:
        return (
            "Google Reviews / Reputation",
            f"Rating {lead.get('rating')}/5 — below the 4.0 threshold customers trust.",
            True,
        )

    if not lead.get("social_links"):
        return (
            "Social Media / SEO",
            "No social media presence detected on website or Google Maps listing.",
            True,
        )

    return "Sales / Marketing", "Website exists but no strong conversion signals detected.", True


def _build_evidence(lead: dict, category: str) -> str:
    if category == "Website / Branding":
        if not lead.get("website"):
            return "No website listed on Google Maps."
        return "Website appears outdated — last updated over 5 years ago."
    if category == "Google Reviews / Reputation":
        rating = lead.get("rating")
        count = lead.get("review_count")
        parts = []
        if rating is not None and rating < config.REVIEW_LOW_THRESHOLD:
            parts.append(f"{rating}/5 rating")
        if count is not None and count < config.REVIEW_COUNT_LOW:
            parts.append(f"only {count} reviews")
        return f"Reputation gap: {' and '.join(parts)} — below healthy threshold."
    if category == "No E-commerce":
        return f"Business category '{lead.get('category')}' suggests product sales, but no e-commerce platform detected on site."
    if category == "Content / Blog":
        return "Has a website but no blog, news, or content section found — missing SEO and trust signals."
    if category == "Social Media / SEO":
        ig_rate = lead.get("instagram_engagement_rate")
        if ig_rate is not None:
            return f"Instagram engagement rate {ig_rate:.2%} — below the 0.5% industry benchmark."
        count = lead.get("review_count", 0)
        return f"No social media links found and only {count} Google reviews — low online visibility."
    if category == "Sales / Marketing":
        return "Website has no detectable contact form, phone link, or call-to-action button."
    return ""
