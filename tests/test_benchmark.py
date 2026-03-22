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
