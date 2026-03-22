#!/usr/bin/env python3
"""CLI entry point for the lead generation pipeline.

Usage:
    # Google Maps (default)
    python find_leads.py --industry restaurant --country us
    python find_leads.py --industry "law firm" --country uk --limit 30
    python find_leads.py --industry salon --country de --locations 5

    # Instagram
    python find_leads.py --industry restaurant --country us --source instagram
    python find_leads.py --industry boutique --country uk --source instagram --limit 100
"""

import argparse
import sys
from pathlib import Path

# Allow running from the scripts/ directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from leadgen.pipeline import run


def main():
    parser = argparse.ArgumentParser(
        description="Find and classify B2B leads from Google Maps or Instagram."
    )
    parser.add_argument(
        "--industry", "-i",
        required=True,
        help='Industry to search, e.g. "restaurant", "law firm", "hair salon"',
    )
    parser.add_argument(
        "--country", "-c",
        default="us",
        help="Country code: us, uk, de, fr, nl, es, it, pl, se (default: us)",
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=50,
        help="Max leads per location/hashtag (default: 50)",
    )
    parser.add_argument(
        "--locations", "-n",
        type=int,
        default=20,
        help="Max number of zip/postal codes to scan (default: 20)",
    )
    parser.add_argument(
        "--source", "-s",
        default="maps",
        choices=["maps", "instagram"],
        help="Lead source: 'maps' (Google Maps) or 'instagram' (default: maps)",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Skip website enrichment (faster but less accurate classification)",
    )

    args = parser.parse_args()

    summary = run(
        industry=args.industry,
        country=args.country,
        limit_per_location=args.limit,
        max_locations=args.locations,
        skip_enrichment=args.fast,
        source=args.source,
    )

    total = sum(summary.values())
    print(f"Done. {total} leads added to Google Sheets.")


if __name__ == "__main__":
    main()
