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


def test_validate_paths_rejects_bare_directory():
    from autoopt.optimize import validate_paths
    assert validate_paths([{"file": "leadgen", "content": "x"}]) is False


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
    assert "revert" in lines[1]
    assert "N/A" in lines[1]
    cols = lines[1].split('\t')
    assert cols[3] == "N/A"   # new_s_per_lead column
    assert cols[4] == "N/A"   # delta_pct column
