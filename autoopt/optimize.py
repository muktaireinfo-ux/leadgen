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
from typing import Optional

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
    new_metric: Optional[float],
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
