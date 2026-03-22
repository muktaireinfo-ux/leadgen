"""Apify-based Instagram lead scraper.

Two-step process:
  1. Hashtag search — finds posts on niche hashtags, collects unique usernames.
  2. Profile scrape — gets full profile data (followers, bio, website, isBusinessAccount).

Why Apify (not custom Playwright):
  Instagram fingerprints TLS sessions before JS runs. Custom Playwright gets
  banned within hours. Apify rotates residential proxies — built into their
  actor infrastructure. Their free tier gives ~$5/month in platform credits,
  roughly 200-300 profile fetches.

Cost estimate (Apify free tier, ~$5/month):
  - Hashtag scraper: ~100 posts for $0.05
  - Profile scraper:  ~100 profiles for $0.10
  - Net: ~300-400 Instagram leads/month on the free tier

GDPR / ToS:
  - We filter to isBusinessAccount = True (public commercial data only)
  - No DMs, no private accounts, no personal PII collected
"""

import time
from leadgen import config
from leadgen.scrapers.base import BaseScraper

# Apify actor IDs — pinned so updates don't break us
_HASHTAG_ACTOR = "apify/instagram-hashtag-scraper"
_PROFILE_ACTOR = "apify/instagram-profile-scraper"

# Engagement rate below this → flag as Social Media / SEO lead
ENGAGEMENT_RATE_LOW = 0.005  # 0.5%


def _build_hashtags(query: str, location: str) -> list[str]:
    """Build a list of niche hashtags from query + optional location.

    Niche hashtags (< 500k posts) work better than broad ones (#food has
    500M posts — we want struggling local businesses, not viral accounts).

    Args:
        query:    industry search term, e.g. "restaurant"
        location: city or country code, e.g. "nyc" or "london"
    """
    word = query.strip().lower().replace(" ", "")
    loc = location.strip().lower().replace(" ", "")

    tags = [
        f"local{word}",
        f"small{word}",
        f"family{word}",
        f"{word}owner",
        f"{word}business",
    ]
    if loc and loc not in ("us", "uk", "eu", "europe"):
        tags.insert(0, f"{loc}{word}")
        tags.insert(1, f"{word}{loc}")

    return [f"#{t}" for t in tags]


def _get_client():
    try:
        from apify_client import ApifyClient  # noqa: PLC0415
        return ApifyClient(config.APIFY_API_KEY)
    except ImportError:
        raise RuntimeError(
            "apify-client not installed. Run: pip install apify-client"
        )


def _run_hashtag_search(client, hashtags: list[str], posts_limit: int) -> list[str]:
    """Step 1: search hashtags, return list of unique owner usernames."""
    print(f"[Instagram] Searching hashtags: {', '.join(hashtags[:4])}")
    try:
        run = client.actor(_HASHTAG_ACTOR).call(
            run_input={
                "hashtags": [h.lstrip("#") for h in hashtags],
                "resultsType": "posts",
                "resultsLimit": posts_limit,
                "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
            },
            timeout_secs=180,
        )
        usernames: list[str] = []
        seen: set[str] = set()
        dataset_id = run.get("defaultDatasetId") if run else None
        if not dataset_id:
            return []
        for item in client.dataset(dataset_id).iterate_items():
            username = (
                item.get("ownerUsername")
                or (item.get("owner") or {}).get("username", "")
            )
            if username and username not in seen:
                seen.add(username)
                usernames.append(username)
        print(f"[Instagram] Found {len(usernames)} unique accounts from hashtags.")
        return usernames
    except Exception as e:
        print(f"[Instagram] Hashtag search error: {e}")
        return []


def _run_profile_scrape(client, usernames: list[str]) -> list[dict]:
    """Step 2: scrape full profiles for the given usernames."""
    if not usernames:
        return []
    print(f"[Instagram] Fetching {len(usernames)} profiles...")
    try:
        run = client.actor(_PROFILE_ACTOR).call(
            run_input={
                "usernames": usernames,
                "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
            },
            timeout_secs=300,
        )
        dataset_id = run.get("defaultDatasetId") if run else None
        if not dataset_id:
            return []
        profiles = list(client.dataset(dataset_id).iterate_items())
        print(f"[Instagram] Got {len(profiles)} profiles.")
        return profiles
    except Exception as e:
        print(f"[Instagram] Profile scrape error: {e}")
        return []


def _calc_engagement_rate(profile: dict) -> float:
    """Rough engagement rate: avg likes+comments per post / followers."""
    followers = profile.get("followersCount") or 0
    if followers == 0:
        return 0.0
    # Apify may return latestPosts with individual likes/comments
    latest = profile.get("latestPosts") or []
    if latest:
        total_eng = sum(
            (p.get("likesCount") or 0) + (p.get("commentsCount") or 0)
            for p in latest
        )
        avg_eng = total_eng / len(latest)
    else:
        # Fall back to 0 — will be flagged as low engagement
        avg_eng = 0
    return avg_eng / followers


def _normalize(profile: dict) -> dict:
    """Map Apify profile dict to our standard lead schema."""
    username = profile.get("username") or profile.get("userName") or ""
    followers = profile.get("followersCount") or 0
    posts_count = profile.get("postsCount") or 0
    website = (profile.get("externalUrl") or "").strip()
    engagement_rate = _calc_engagement_rate(profile)

    return {
        "name": (profile.get("fullName") or f"@{username}").strip(),
        "address": "",
        "city": "",
        "country": "",
        "phone": "",
        "email": "",
        "website": website,
        # Map followers as a proxy for "review_count" so the classifier
        # thresholds (REVIEW_COUNT_LOW = 10) don't incorrectly trigger
        "rating": None,
        "review_count": None,
        "category": profile.get("businessCategoryName") or "",
        "social_links": [f"https://instagram.com/{username}"] if username else [],
        "scraper": "apify_instagram",
        # Instagram-specific fields — used by classifier prompt
        "instagram_username": username,
        "instagram_followers": followers,
        "instagram_posts": posts_count,
        "instagram_bio": (profile.get("biography") or "").strip(),
        "instagram_engagement_rate": round(engagement_rate, 4),
        "instagram_low_engagement": engagement_rate < ENGAGEMENT_RATE_LOW,
        "instagram_no_website": not website,
    }


class ApifyInstagramScraper(BaseScraper):
    """Find Instagram business accounts via hashtag + profile scraping."""

    def scrape(self, query: str, location: str, limit: int = 100) -> list[dict]:
        if not config.APIFY_API_KEY:
            print(
                "[Instagram] APIFY_API_KEY not set — skipping. "
                "Get a free key at apify.com."
            )
            return []

        try:
            client = _get_client()
        except RuntimeError as e:
            print(f"[Instagram] {e}")
            return []

        hashtags = _build_hashtags(query, location)

        # Step 1: collect usernames from hashtag search
        # Fetch 5× the limit in posts to get enough unique accounts after dedup
        usernames = _run_hashtag_search(client, hashtags, posts_limit=limit * 5)

        if not usernames:
            return []

        # Batch into chunks of 50 (Apify actor limit per call)
        all_profiles: list[dict] = []
        for i in range(0, min(len(usernames), limit * 2), 50):
            batch = usernames[i : i + 50]
            profiles = _run_profile_scrape(client, batch)
            all_profiles.extend(profiles)
            if len(all_profiles) >= limit * 2:
                break
            if i + 50 < len(usernames):
                time.sleep(2)  # polite delay between actor calls

        # Filter: business accounts only (GDPR — public commercial data)
        business_profiles = [
            p for p in all_profiles
            if p.get("isBusinessAccount") or p.get("isBusiness")
        ]
        print(
            f"[Instagram] {len(business_profiles)} business accounts "
            f"(filtered from {len(all_profiles)} total)."
        )

        leads = [_normalize(p) for p in business_profiles[:limit]]
        return leads
