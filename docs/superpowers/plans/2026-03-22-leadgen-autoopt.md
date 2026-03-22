# Lead Gen Auto-Optimizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an autonomous optimizer that runs hourly on GitHub Actions, uses Claude to suggest code improvements to the lead generation pipeline, benchmarks seconds/lead before and after, commits improvements, and reverts regressions.

**Architecture:** `autoopt/benchmark.py` runs a fixed small pipeline invocation and returns seconds/lead. `autoopt/optimize.py` orchestrates the loop: baseline benchmark → Claude suggestion → apply changes → re-benchmark → commit or revert → log to results.tsv. A GitHub Actions cron workflow triggers this every hour.

**Tech Stack:** Python 3.11, `anthropic` SDK (already in requirements.txt), `subprocess` + `git` for commit/revert, GitHub Actions with `stefanzweifel/git-auto-commit-action`, `pytest` for tests.

---

## File Map

| File | Status | Responsibility |
|------|--------|----------------|
| `autoopt/__init__.py` | Create | Empty — marks autoopt as a package |
| `autoopt/benchmark.py` | Create | Runs fixed pipeline invocation, returns `float` seconds/lead |
| `autoopt/optimize.py` | Create | Full optimization loop: benchmark → Claude → apply → benchmark → commit/revert → log |
| `tests/test_benchmark.py` | Create | Tests for benchmark.py |
| `tests/test_optimize.py` | Create | Tests for path validation, apply/revert, results logging |
| `results.tsv` | Create | Append-only experiment log with TSV header |
| `.github/workflows/optimize.yml` | Create | Hourly cron workflow |

**Do not modify:** `leadgen/config.py`, `leadgen/`, `.env`, `service_account.json` (these are the pipeline files Claude will auto-edit later — we don't touch them manually).

---

## Task 1: Bootstrap autoopt package and results log

**Files:**
- Create: `autoopt/__init__.py`
- Create: `results.tsv`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create the autoopt package**

```bash
touch ~/leadgen/autoopt/__init__.py
touch ~/leadgen/tests/__init__.py
```

- [ ] **Step 2: Create results.tsv with header**

```bash
printf 'timestamp\tcommit\tbaseline_s_per_lead\tnew_s_per_lead\tdelta_pct\tstatus\tdescription\n' > ~/leadgen/results.tsv
```

- [ ] **Step 3: Commit**

```bash
cd ~/leadgen
git add autoopt/__init__.py tests/__init__.py results.tsv
git commit -m "chore: bootstrap autoopt package and results.tsv"
```

---

## Task 2: Write `autoopt/benchmark.py` (TDD)

**Files:**
- Create: `autoopt/benchmark.py`
- Create: `tests/test_benchmark.py`

The benchmark imports `leadgen.pipeline.run` directly (no subprocess), calls it with fixed params, and returns `elapsed / total_leads`. Raises `ValueError` if zero leads returned.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_benchmark.py`:

```python
import pytest
from unittest.mock import patch


def test_returns_seconds_per_lead():
    """benchmark returns a positive float when leads are generated."""
    with patch("autoopt.benchmark.run") as mock_run, \
         patch("autoopt.benchmark.time") as mock_time:
        mock_run.return_value = {"Website / Branding": 3, "Sales / Marketing": 2}
        mock_time.time.side_effect = [0.0, 10.0]  # 10 seconds elapsed
        from autoopt.benchmark import run_benchmark
        result = run_benchmark()
    assert result == pytest.approx(2.0)  # 10s / 5 leads


def test_raises_on_zero_leads():
    """benchmark raises ValueError when pipeline returns no leads."""
    with patch("autoopt.benchmark.run") as mock_run, \
         patch("autoopt.benchmark.time"):
        mock_run.return_value = {}
        from autoopt.benchmark import run_benchmark
        with pytest.raises(ValueError, match="zero leads"):
            run_benchmark()


def test_raises_on_pipeline_crash():
    """benchmark propagates exceptions from pipeline."""
    with patch("autoopt.benchmark.run") as mock_run:
        mock_run.side_effect = RuntimeError("API error")
        from autoopt.benchmark import run_benchmark
        with pytest.raises(RuntimeError):
            run_benchmark()
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd ~/leadgen
python -m pytest tests/test_benchmark.py -v
```

Expected: `ImportError: cannot import name 'run_benchmark'`

- [ ] **Step 3: Implement `autoopt/benchmark.py`**

```python
"""Benchmark the lead generation pipeline on a fixed small test.

Returns seconds-per-lead: total elapsed time divided by number of leads
classified. Raises ValueError if no leads are produced.
"""

import time

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
    t0 = time.time()
    summary = run(**_BENCHMARK_KWARGS)
    elapsed = time.time() - t0

    total_leads = sum(summary.values())
    if total_leads == 0:
        raise ValueError(
            "Benchmark produced zero leads — cannot compute seconds/lead"
        )

    return elapsed / total_leads
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
cd ~/leadgen
python -m pytest tests/test_benchmark.py -v
```

Expected: all 3 tests PASS

- [ ] **Step 5: Commit**

```bash
cd ~/leadgen
git add autoopt/benchmark.py tests/test_benchmark.py
git commit -m "feat: add benchmark.py with seconds/lead metric"
```

---

## Task 3: Write `autoopt/optimize.py` — safe file operations (TDD)

**Files:**
- Create: `autoopt/optimize.py` (partial — path validation + apply/revert + results logging only)
- Create: `tests/test_optimize.py`

Write and test the three safe-file-ops functions first, before adding Claude or git calls.

- [ ] **Step 1: Write failing tests for path validation, apply, revert, and results logging**

Create `tests/test_optimize.py`:

```python
import subprocess
from pathlib import Path
import pytest


# ── path validation ──────────────────────────────────────────────────────────

def test_validate_paths_accepts_leadgen_file():
    from autoopt.optimize import validate_paths
    assert validate_paths([{"file": "leadgen/pipeline.py", "content": "x"}]) is True


def test_validate_paths_rejects_autoopt():
    from autoopt.optimize import validate_paths
    assert validate_paths([{"file": "autoopt/optimize.py", "content": "x"}]) is False


def test_validate_paths_rejects_traversal():
    from autoopt.optimize import validate_paths
    assert validate_paths([{"file": "leadgen/../autoopt/optimize.py", "content": "x"}]) is False


def test_validate_paths_rejects_github_workflows():
    from autoopt.optimize import validate_paths
    assert validate_paths([{"file": ".github/workflows/optimize.yml", "content": "x"}]) is False


# ── apply + revert ───────────────────────────────────────────────────────────

@pytest.fixture
def git_repo(tmp_path):
    """Create a minimal git repo with a leadgen/pipeline.py file."""
    leadgen = tmp_path / "leadgen"
    leadgen.mkdir()
    target = leadgen / "pipeline.py"
    target.write_text("original content")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def test_apply_changes_writes_files(git_repo):
    from autoopt.optimize import apply_changes
    changes = [{"file": "leadgen/pipeline.py", "content": "modified"}]
    apply_changes(changes, git_repo)
    assert (git_repo / "leadgen" / "pipeline.py").read_text() == "modified"


def test_revert_changes_restores_files(git_repo):
    from autoopt.optimize import apply_changes, revert_changes
    changes = [{"file": "leadgen/pipeline.py", "content": "modified"}]
    apply_changes(changes, git_repo)
    revert_changes(git_repo)
    assert (git_repo / "leadgen" / "pipeline.py").read_text() == "original content"


# ── results logging ──────────────────────────────────────────────────────────

def test_append_results_creates_file_if_missing(tmp_path):
    from autoopt.optimize import append_results
    append_results(tmp_path, "abc1234", 5.0, 4.0, "keep", "test change")
    tsv = tmp_path / "results.tsv"
    assert tsv.exists()
    lines = tsv.read_text().splitlines()
    assert lines[0].startswith("timestamp\t")
    assert "keep" in lines[1]
    assert "4.0" in lines[1] or "4.00" in lines[1]


def test_append_results_appends_on_second_call(tmp_path):
    from autoopt.optimize import append_results
    append_results(tmp_path, "abc1234", 5.0, 4.0, "keep", "first")
    append_results(tmp_path, "def5678", 4.0, 4.5, "revert", "second")
    lines = (tmp_path / "results.tsv").read_text().splitlines()
    assert len(lines) == 3  # header + 2 data rows


def test_append_results_handles_crash(tmp_path):
    from autoopt.optimize import append_results
    append_results(tmp_path, "abc1234", 5.0, None, "revert", "crash")
    lines = (tmp_path / "results.tsv").read_text().splitlines()
    assert "crash" in lines[1]
    assert "N/A" in lines[1]
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd ~/leadgen
python -m pytest tests/test_optimize.py -v
```

Expected: `ImportError: cannot import name 'validate_paths'`

- [ ] **Step 3: Implement the safe-file-ops functions in `autoopt/optimize.py`**

Create `autoopt/optimize.py` with just these functions (Claude integration added in Task 4):

```python
"""Lead generation pipeline auto-optimizer.

Each run:
  1. Benchmark current code → baseline seconds/lead
  2. Ask Claude for one code improvement
  3. Apply changes (only under leadgen/)
  4. Benchmark again
  5. Commit if ≥1% faster, else revert
  6. Append row to results.tsv
"""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
LEADGEN_DIR = REPO_ROOT / "leadgen"
RESULTS_TSV = REPO_ROOT / "results.tsv"
RESULTS_HEADER = (
    "timestamp\tcommit\tbaseline_s_per_lead\t"
    "new_s_per_lead\tdelta_pct\tstatus\tdescription\n"
)


# ── path validation ──────────────────────────────────────────────────────────

def validate_paths(changes: list[dict]) -> bool:
    """Return True only if every file path in changes is under leadgen/."""
    for change in changes:
        path = Path(change["file"])
        if not path.parts or path.parts[0] != "leadgen":
            return False
        resolved = (REPO_ROOT / path).resolve()
        if not str(resolved).startswith(str(LEADGEN_DIR.resolve())):
            return False
    return True


# ── apply / revert ───────────────────────────────────────────────────────────

def apply_changes(changes: list[dict], repo_root: Path = REPO_ROOT) -> None:
    """Write each file change to disk."""
    for change in changes:
        target = repo_root / change["file"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(change["content"])


def revert_changes(repo_root: Path = REPO_ROOT) -> None:
    """Restore leadgen/ to HEAD via git checkout."""
    subprocess.run(
        ["git", "checkout", "--", "leadgen/"],
        cwd=repo_root,
        check=True,
    )


# ── results logging ──────────────────────────────────────────────────────────

def append_results(
    repo_root: Path,
    commit: str,
    baseline: float,
    new_metric: float | None,
    status: str,
    description: str,
) -> None:
    """Append one row to results.tsv, creating file with header if needed."""
    tsv = repo_root / "results.tsv"
    if not tsv.exists():
        tsv.write_text(RESULTS_HEADER)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_str = f"{new_metric:.3f}" if new_metric is not None else "crash"
    if new_metric is not None:
        delta = f"{(new_metric - baseline) / baseline * 100:+.1f}%"
    else:
        delta = "N/A"

    with open(tsv, "a") as f:
        f.write(
            f"{timestamp}\t{commit}\t{baseline:.3f}\t"
            f"{new_str}\t{delta}\t{status}\t{description}\n"
        )
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
cd ~/leadgen
python -m pytest tests/test_optimize.py -v
```

Expected: all 11 tests PASS

- [ ] **Step 5: Commit**

```bash
cd ~/leadgen
git add autoopt/optimize.py tests/test_optimize.py
git commit -m "feat: add path validation, apply/revert, and results logging"
```

---

## Task 4: Complete `autoopt/optimize.py` — Claude integration and main loop

**Files:**
- Modify: `autoopt/optimize.py` (add Claude call + git commit + `main()`)

No new tests for Claude integration — the API call is mocked at the boundary. The functions added here are thin wrappers around subprocess and the Anthropic SDK.

- [ ] **Step 1: Append these functions to `autoopt/optimize.py`**

Add after the `append_results` function:

```python
# ── source file reader ───────────────────────────────────────────────────────

def get_source_files() -> dict[str, str]:
    """Return all Python files under leadgen/ as {relative_path: content}."""
    files = {}
    for path in sorted(LEADGEN_DIR.rglob("*.py")):
        rel = str(path.relative_to(REPO_ROOT))
        files[rel] = path.read_text()
    return files


# ── Claude integration ───────────────────────────────────────────────────────

def call_claude(baseline: float, source_files: dict[str, str]) -> list[dict]:
    """Ask Claude for one targeted improvement. Returns list of {file, content}."""
    import anthropic

    files_text = "\n\n".join(
        f"=== {path} ===\n{content}" for path, content in source_files.items()
    )

    prompt = f"""You are optimizing a Python lead generation pipeline for speed (seconds per lead).
Current performance: {baseline:.3f} seconds/lead.

Your task: suggest ONE code change that would make lead generation faster.
Focus on: concurrency, I/O parallelism, caching, reduced API round-trips,
eliminated sleep delays, connection reuse, or faster data structures.

STRICT RULES:
- Only return files under leadgen/ (e.g. "leadgen/pipeline.py")
- Do NOT touch: API key loading, credential files, config.py env reads,
  service_account.json handling, or the benchmark parameters
- Do NOT remove deduplication logic
- Do NOT break the Google Sheets write

Return ONLY a JSON array (no markdown, no explanation) of objects:
[{{"file": "leadgen/pipeline.py", "content": "<full new file content>"}}]

Current source files:
{files_text}"""

    client = anthropic.Anthropic()
    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )

    text = message.content[0].text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]

    start = text.find("[")
    end = text.rfind("]") + 1
    return json.loads(text[start:end])


# ── git helpers ──────────────────────────────────────────────────────────────

def get_short_commit(repo_root: Path = REPO_ROOT) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def commit_and_push(
    changes: list[dict], baseline: float, new_metric: float,
    repo_root: Path = REPO_ROOT,
) -> None:
    """Stage changed files + results.tsv, commit, and push."""
    files = [change["file"] for change in changes] + ["results.tsv"]
    subprocess.run(["git", "add"] + files, cwd=repo_root, check=True)
    delta_pct = (new_metric - baseline) / baseline * 100
    msg = f"autoopt: {new_metric:.3f}s/lead ({delta_pct:+.1f}%)"
    subprocess.run(["git", "commit", "-m", msg], cwd=repo_root, check=True)
    subprocess.run(["git", "push"], cwd=repo_root, check=True)


def push_results_only(repo_root: Path = REPO_ROOT) -> None:
    """Commit and push results.tsv alone (revert runs)."""
    subprocess.run(["git", "add", "results.tsv"], cwd=repo_root, check=True)
    subprocess.run(
        ["git", "commit", "-m", "autoopt: log experiment (no improvement)"],
        cwd=repo_root, check=True,
    )
    subprocess.run(["git", "push"], cwd=repo_root, check=True)


# ── main loop ────────────────────────────────────────────────────────────────

def main() -> None:
    from autoopt.benchmark import run_benchmark

    print("=" * 60)
    print("Lead Gen Auto-Optimizer")
    print("=" * 60)

    # 1. Baseline
    print("\n[1/5] Running baseline benchmark...")
    try:
        baseline = run_benchmark()
    except Exception as e:
        print(f"Baseline benchmark failed: {e}")
        return
    print(f"      Baseline: {baseline:.3f}s/lead")

    # 2. Get suggestion
    print("\n[2/5] Asking Claude for an improvement...")
    source_files = get_source_files()
    try:
        changes = call_claude(baseline, source_files)
    except Exception as e:
        print(f"Claude call failed: {e}")
        return
    print(f"      Claude suggested changes to: {[c['file'] for c in changes]}")

    # 3. Validate paths
    print("\n[3/5] Validating file paths...")
    if not validate_paths(changes):
        print("ERROR: Claude returned paths outside leadgen/. Aborting.")
        commit = get_short_commit()
        append_results(REPO_ROOT, commit, baseline, None, "skip", "invalid paths from Claude")
        push_results_only()
        return

    # 4. Apply + benchmark
    print("\n[4/5] Applying changes and re-benchmarking...")
    apply_changes(changes)
    try:
        new_metric = run_benchmark()
    except Exception as e:
        print(f"Benchmark after changes failed: {e}")
        revert_changes()
        commit = get_short_commit()
        append_results(REPO_ROOT, commit, baseline, None, "revert", f"crash: {e}")
        push_results_only()
        return

    delta_pct = (new_metric - baseline) / baseline * 100
    print(f"      New metric: {new_metric:.3f}s/lead ({delta_pct:+.1f}%)")

    # 5. Keep or revert
    commit = get_short_commit()
    if new_metric < baseline * 0.99:
        print("\n[5/5] Improved! Committing and pushing...")
        description = f"changed {[c['file'] for c in changes]}"
        append_results(REPO_ROOT, commit, baseline, new_metric, "keep", description)
        commit_and_push(changes, baseline, new_metric)
        print("      Done. Improvement committed.")
    else:
        print("\n[5/5] No improvement. Reverting...")
        revert_changes()
        append_results(REPO_ROOT, commit, baseline, new_metric, "revert", "no improvement")
        push_results_only()
        print("      Done. Changes reverted.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify existing tests still pass**

```bash
cd ~/leadgen
python -m pytest tests/ -v
```

Expected: all tests PASS (new functions are not unit-tested — they're thin I/O wrappers)

- [ ] **Step 3: Commit**

```bash
cd ~/leadgen
git add autoopt/optimize.py
git commit -m "feat: add Claude integration and main optimization loop"
```

---

## Task 5: Write GitHub Actions workflow

**Files:**
- Create: `.github/workflows/optimize.yml`

- [ ] **Step 1: Create the workflows directory**

```bash
mkdir -p ~/leadgen/.github/workflows
```

- [ ] **Step 2: Create `.github/workflows/optimize.yml`**

```yaml
name: Lead Gen Auto-Optimizer

on:
  schedule:
    - cron: '0 * * * *'   # every hour at :00
  workflow_dispatch:        # allow manual runs for testing

permissions:
  contents: write           # required to push commits back to repo

jobs:
  optimize:
    runs-on: ubuntu-latest
    timeout-minutes: 30     # kill if something hangs

    steps:
      - name: Checkout repo
        uses: actions/checkout@v4
        with:
          token: ${{ secrets.GITHUB_TOKEN }}

      - name: Set up Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Configure git identity
        run: |
          git config user.email "autoopt@leadgen.bot"
          git config user.name "Lead Gen Optimizer"

      - name: Run optimizer
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          OUTSCRAPER_API_KEY: ${{ secrets.OUTSCRAPER_API_KEY }}
          GOOGLE_SHEET_ID: ${{ secrets.GOOGLE_SHEET_ID }}
          GOOGLE_SERVICE_ACCOUNT_JSON: ${{ secrets.GOOGLE_SERVICE_ACCOUNT_JSON }}
          APIFY_API_KEY: ${{ secrets.APIFY_API_KEY }}
        run: python autoopt/optimize.py

      # Fallback: push results.tsv if optimize.py crashed before its own push
      - name: Push results.tsv (fallback)
        if: always()
        uses: stefanzweifel/git-auto-commit-action@v5
        with:
          commit_message: 'autoopt: update results.tsv (fallback push)'
          file_pattern: results.tsv
```

- [ ] **Step 3: Commit**

```bash
cd ~/leadgen
git add .github/workflows/optimize.yml
git commit -m "feat: add hourly GitHub Actions optimizer workflow"
```

---

## Task 6: Push to GitHub and configure secrets

- [ ] **Step 1: Create a new public GitHub repo**

Go to github.com → New repository → name it `leadgen` → set to **Public** → do NOT initialize with README (we'll push existing code).

- [ ] **Step 2: Add all existing leadgen files and push**

```bash
cd ~/leadgen

# Stage everything (excluding .env and service_account.json)
echo ".env" >> .gitignore
echo "service_account.json" >> .gitignore
echo "__pycache__/" >> .gitignore
echo "*.pyc" >> .gitignore
echo "error_logs/" >> .gitignore
echo "output/" >> .gitignore

git add .gitignore leadgen/ scripts/ autoopt/ tests/ requirements.txt results.tsv .github/
git commit -m "chore: initial commit of full leadgen codebase"

git remote add origin https://github.com/YOUR_GITHUB_USERNAME/leadgen.git
# ↑ Replace YOUR_GITHUB_USERNAME before running this line
git branch -M main
git push -u origin main
```

Replace `YOUR_USERNAME` with your actual GitHub username.

- [ ] **Step 3: Add GitHub Secrets**

Go to your repo → Settings → Secrets and variables → Actions → New repository secret. Add each:

| Secret name | Value |
|-------------|-------|
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `OUTSCRAPER_API_KEY` | Your Outscraper key (or leave empty if using Botasaurus) |
| `GOOGLE_SHEET_ID` | Your Google Sheet ID |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | The full path or JSON content of service_account.json |
| `APIFY_API_KEY` | Your Apify key (only needed for Instagram source) |

- [ ] **Step 4: Trigger a manual test run**

Go to your repo → Actions → Lead Gen Auto-Optimizer → Run workflow → Run workflow.

Watch the run complete. Verify:
- The optimizer prints baseline, suggestion, and result
- `results.tsv` gets a new row
- If improvement found, a commit appears in the repo

- [ ] **Step 5: Verify cron is active**

GitHub Actions cron jobs run on GitHub's schedule. After the first manual run succeeds, the hourly schedule will activate automatically. You can verify by checking Actions → scheduled runs the next hour.

---

## Definition of Done

- [ ] `python -m pytest tests/ -v` passes locally
- [ ] Manual GitHub Actions run completes without errors
- [ ] `results.tsv` has at least one row after first run
- [ ] Hourly cron is firing (check Actions tab after 1 hour)
