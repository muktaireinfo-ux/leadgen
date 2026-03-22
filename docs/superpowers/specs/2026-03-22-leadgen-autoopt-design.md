# Lead Gen Auto-Optimizer Design

**Date:** 2026-03-22
**Status:** Approved

## Overview

Apply the karpathy/autoresearch optimization loop pattern to the lead generation pipeline (`~/leadgen`). An LLM (Claude) iteratively modifies the pipeline code each hour, benchmarks execution speed, and commits improvements — running fully autonomously on GitHub Actions.

**Metric:** seconds per lead (total wall-clock time ÷ leads generated). Lower is better.

---

## Architecture

```
leadgen/  (public GitHub repo)
├── leadgen/           ← existing pipeline (all files editable by optimizer)
├── scripts/
├── autoopt/
│   ├── optimize.py    ← orchestrator: benchmark → Claude → apply → benchmark → commit/revert
│   └── benchmark.py   ← runs a fixed small test and returns seconds/lead
├── results.tsv        ← append-only experiment log (committed each run)
└── .github/workflows/
    └── optimize.yml   ← hourly cron, push-back to repo enabled
```

---

## Components

### 1. `autoopt/benchmark.py`

Runs a fixed, reproducible benchmark invocation of the pipeline and returns `seconds_per_lead`.

**Fixed benchmark config:**
```
--industry restaurant --country us --fast --limit 5 --locations 1
```

- `--fast`: skips website enrichment (deterministic, no external enricher latency variance)
- `--limit 5 --locations 1`: minimal real API calls, low cost (~$0.01/run)
- Times from pipeline start to last lead classified
- Returns `float` seconds_per_lead; raises on crash or zero leads

### 2. `autoopt/optimize.py`

The main loop executed once per GitHub Actions run.

**Steps:**
1. Run `benchmark.py` → `baseline_seconds_per_lead`
2. Read all Python files under `~/leadgen/` recursively
3. Call Claude API with:
   - All pipeline source code
   - Current metric (`baseline_seconds_per_lead`)
   - A prompt asking for ONE targeted code change to reduce seconds/lead
   - The list of files Claude is allowed to modify (everything under `leadgen/`)
4. Claude returns: modified file path(s) + new file content(s)
5. Apply changes (overwrite files)
6. Run `benchmark.py` → `new_seconds_per_lead`
7. **If improved** (new < baseline × 0.99 — at least 1% faster): `git commit` the changes
8. **If not improved or crash**: restore original files via `git checkout`
9. Append one row to `results.tsv` with result
10. `git add results.tsv && git commit && git push`

### 3. `.github/workflows/optimize.yml`

```yaml
on:
  schedule:
    - cron: '0 * * * *'   # every hour
  workflow_dispatch:        # manual trigger for testing

permissions:
  contents: write           # allows push-back to repo

jobs:
  optimize:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r requirements.txt
      - run: python autoopt/optimize.py
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          OUTSCRAPER_API_KEY: ${{ secrets.OUTSCRAPER_API_KEY }}
          GOOGLE_SHEET_ID: ${{ secrets.GOOGLE_SHEET_ID }}
          GOOGLE_SERVICE_ACCOUNT_JSON: ${{ secrets.GOOGLE_SERVICE_ACCOUNT_JSON }}
          APIFY_API_KEY: ${{ secrets.APIFY_API_KEY }}
      # optimize.py handles its own git commit+push for code improvements.
      # This step only catches results.tsv if optimize.py exited before committing it
      # (e.g. on an unhandled exception). optimize.py is the single owner of all commits.
      - uses: stefanzweifel/git-auto-commit-action@v5
        with:
          commit_message: 'autoopt: update results.tsv (fallback)'
```

### 4. `results.tsv`

Tab-separated log. Appended each run, committed. Designed for future Option C (memory-aware optimization).

```
timestamp	commit	seconds_per_lead	delta_pct	status	description
2026-03-22T10:00:00Z	a1b2c3d	4.23	baseline	keep	baseline
2026-03-22T11:00:00Z	b2c3d4e	3.81	-9.9%	keep	increase ThreadPoolExecutor workers 8→16
2026-03-22T12:00:00Z	-	4.51	+6.6%	revert	remove sleep delays (caused rate limit errors)
```

---

## Claude Prompt Design

The optimizer calls Claude with this context:

```
You are optimizing a Python lead generation pipeline for speed (seconds per lead).
Current performance: {baseline} seconds/lead.

Your task: suggest ONE code change that would make lead generation faster.
Focus on: concurrency, I/O parallelism, caching, reduced API round-trips,
eliminated sleep delays, connection reuse, or faster data structures.

Do NOT: change the benchmark parameters, modify scraper auth logic,
remove deduplication, or break the Google Sheets write.

Return: {"file": "<path>", "content": "<full new file content>"}
You may return multiple files as a JSON array.

Current source files:
{all_python_files}
```

---

## Data Flow

```
GitHub Actions (hourly)
    │
    ▼
benchmark.py ──► baseline_seconds_per_lead
    │
    ▼
Claude API ──► suggested file change(s)
    │
    ▼
apply changes
    │
    ▼
benchmark.py ──► new_seconds_per_lead
    │
    ├── improved? ──► git commit change + push
    │
    └── worse/crash? ──► git checkout (restore)
    │
    ▼
append results.tsv ──► git push
```

---

## Constraints & Safety

- **Scope**: Claude may only modify files under `leadgen/` (not `autoopt/`, `.github/`, `results.tsv`). `optimize.py` must validate all returned file paths are under `leadgen/` before writing — do not rely on the prompt alone to enforce this.
- **Auth boundary**: Claude must not touch API key loading or credential files (e.g. `config.py` env reads, `.env` handling, `service_account.json`)
- **Crash guard**: if benchmark crashes (exception or zero leads returned), always revert — never commit a broken state
- **Improvement threshold**: require ≥1% improvement to commit (avoids noisy micro-commits)
- **GitHub Actions minutes**: public repo = unlimited free minutes (secrets remain in GitHub Secrets, never in code)
- **Cost per run**: ~$0.02–0.05 (Claude API call + 5 real scraper leads)

---

## Future: Option C (Memory-Aware)

`results.tsv` is designed for this. In a future iteration, `optimize.py` passes the full `results.tsv` history to Claude before asking for a suggestion, so it avoids re-trying failed ideas and builds on successful ones.
