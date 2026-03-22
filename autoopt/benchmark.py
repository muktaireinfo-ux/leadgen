"""Benchmark the lead generation pipeline on a fixed small test.

Returns seconds-per-lead: total elapsed time divided by number of leads
classified. Raises ValueError if no leads are produced.

SheetsWriter is mocked out so Sheets latency and credential errors don't
affect the timing measurement — we're measuring scrape/classify speed only.
"""

import time
from unittest.mock import MagicMock, patch

from leadgen.pipeline import run

# Fixed benchmark parameters — never change these between runs
_BENCHMARK_KWARGS = dict(
    industry="restaurant",
    country="us",
    limit_per_location=5,
    max_locations=1,
    skip_enrichment=True,
    source="maps",
)


def run_benchmark() -> float:
    """Run the pipeline with fixed params and return seconds per lead."""
    mock_writer = MagicMock()
    mock_writer.write_batch.return_value = None

    with patch("leadgen.pipeline.SheetsWriter", return_value=mock_writer):
        t0 = time.time()
        summary = run(**_BENCHMARK_KWARGS)
        elapsed = time.time() - t0

    total_leads = sum(summary.values())
    if total_leads == 0:
        raise ValueError(
            "Benchmark produced zero leads — cannot compute seconds/lead"
        )

    return elapsed / total_leads
