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
import os
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
        if len(path.parts) < 2:
            return False
        if not path.parts or path.parts[0] != "leadgen":
            return False
        resolved = (REPO_ROOT / path).resolve()
        leadgen_root = str(LEADGEN_DIR.resolve()) + os.sep
        if not str(resolved).startswith(leadgen_root):
            return False
    return True


# ── apply / revert ───────────────────────────────────────────────────────────

# Public names that must be present in each file — LLMs sometimes emit placeholder
# comments ("# ... rest of code") instead of full content, silently deleting functions.
_REQUIRED_NAMES: dict[str, list[str]] = {
    "leadgen/pipeline.py":              ["run"],
    "leadgen/writer.py":                ["SheetsWriter"],
    "leadgen/utils.py":                 ["retry_with_backoff"],
    "leadgen/classifier.py":            ["classify"],
    "leadgen/enrichers/website.py":     ["enrich"],
    "leadgen/enrichers/google_search.py": ["enrich_via_search"],
}


def _check_required_names(file: str, content: str) -> None:
    """Raise ValueError if any required top-level name is missing from content."""
    import ast
    required = _REQUIRED_NAMES.get(file)
    if not required:
        return
    tree = ast.parse(content)
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and isinstance(getattr(node, "col_offset", 1), int)
        and node.col_offset == 0  # top-level only
    }
    missing = [name for name in required if name not in defined]
    if missing:
        raise ValueError(
            f"{file} is missing required top-level names: {missing}. "
            "LLM likely used placeholder comments instead of full file content."
        )


def apply_changes(changes: list[dict], repo_root: Path = REPO_ROOT) -> None:
    """Write each file change to disk. Raises SyntaxError or ValueError if invalid."""
    import ast
    for change in changes:
        content = change["content"]
        if change["file"].endswith(".py"):
            ast.parse(content)              # raises SyntaxError before touching disk
            _check_required_names(change["file"], content)  # raises ValueError if names missing
        target = repo_root / change["file"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


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
    new_str = f"{new_metric:.3f}" if new_metric is not None else "N/A"
    if new_metric is not None:
        delta = f"{(new_metric - baseline) / baseline * 100:+.1f}%"
    else:
        delta = "N/A"

    with open(tsv, "a") as f:
        f.write(
            f"{timestamp}\t{commit}\t{baseline:.3f}\t"
            f"{new_str}\t{delta}\t{status}\t{description}\n"
        )


# ── source file reader ───────────────────────────────────────────────────────

def get_source_files() -> dict[str, str]:
    """Return optimizable Python files under leadgen/ as {relative_path: content}.

    Excludes files with nothing to optimize:
    - scrapers/   : external API wrappers, latency is network-bound
    - config.py   : env var loading only
    - writer.py   : Google Sheets API calls, latency is network-bound
    - __init__.py : empty package markers

    Keeping only the files that have real optimization potential also keeps
    the prompt within Groq's free-tier TPM limit.
    """
    _SKIP_NAMES = {"config.py", "writer.py", "__init__.py"}
    files = {}
    for path in sorted(LEADGEN_DIR.rglob("*.py")):
        if "scrapers" in path.parts or path.name in _SKIP_NAMES:
            continue
        rel = str(path.relative_to(REPO_ROOT))
        files[rel] = path.read_text()
    return files


# ── Claude integration ───────────────────────────────────────────────────────

def call_claude(baseline: float, source_files: dict) -> list[dict]:
    """Ask an LLM for one targeted improvement. Returns list of {file, content}."""
    import os
    from openai import OpenAI

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

    client = OpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=os.environ["GROQ_API_KEY"],
    )
    message = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )

    text = message.choices[0].message.content.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]

    start = text.find("[")
    if start == -1:
        raise ValueError("No JSON array found in LLM response")

    # LLMs sometimes emit literal control characters inside JSON strings.
    # Fix them before parsing, then use raw_decode so trailing text is ignored.
    fixed = _fix_json_control_chars(text)
    result, _ = json.JSONDecoder().raw_decode(fixed, start)
    return result


def _fix_json_control_chars(s: str) -> str:
    """Escape unescaped control characters that appear inside JSON strings."""
    _ESC = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
    result = []
    in_string = False
    escaped = False
    for ch in s:
        if escaped:
            result.append(ch)
            escaped = False
        elif ch == "\\":
            result.append(ch)
            escaped = True
        elif ch == '"':
            result.append(ch)
            in_string = not in_string
        elif in_string and ord(ch) < 0x20:
            result.append(_ESC.get(ch, " "))
        else:
            result.append(ch)
    return "".join(result)


# ── git helpers ──────────────────────────────────────────────────────────────

def get_short_commit(repo_root: Path = REPO_ROOT) -> str:
    """Return the short (7-char) SHA of the current HEAD commit."""
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
    # Check if there's actually something to commit (avoids CalledProcessError)
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=repo_root,
    )
    if result.returncode == 0:
        # Nothing staged — results.tsv already committed or unchanged
        subprocess.run(["git", "push"], cwd=repo_root, check=True)
        return
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
        commit = get_short_commit()
        append_results(REPO_ROOT, commit, baseline, None, "skip", f"Claude error: {e}")
        push_results_only()
        return
    print(f"      Claude suggested changes to: {[c['file'] for c in changes]}")

    if not changes:
        print("Claude returned no changes. Skipping.")
        commit = get_short_commit()
        append_results(REPO_ROOT, commit, baseline, None, "skip", "Claude returned empty changes")
        push_results_only()
        return

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
    try:
        apply_changes(changes)
    except (SyntaxError, ValueError) as e:
        print(f"LLM change rejected: {e}. Skipping.")
        commit = get_short_commit()
        append_results(REPO_ROOT, commit, baseline, None, "skip", f"invalid change: {e}")
        push_results_only()
        return
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
