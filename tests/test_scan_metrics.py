"""Published counts must cover matching scan days without duplicating signals."""
import datetime as dt
import json
from types import SimpleNamespace

from pipeline import publish
from pipeline.render import _scan_metrics


def test_counts_filings_once_and_uses_only_covered_dates():
    state = {"runs": [
        {"date": "2026-09-01", "index_rows": 100, "candidates": 20},
        {"date": "2026-09-01", "index_rows": 100, "candidates": 20},
        {"date": "2026-09-02", "index_rows": 100, "candidates": 20,
         "blocked": True},
    ]}
    events = [SimpleNamespace(accession=a, filed=d) for a, d in [
        ("one", "2026-09-01"), ("one", "2026-09-01"),
        ("two", "2026-09-01"), ("three", "2026-08-31"),
        ("four", "2026-09-02")]]
    metrics = _scan_metrics(events, state)
    assert metrics["flagged_filings"] == 2
    assert metrics["candidates_scanned"] == 20
    assert metrics["scan_days_covered"] == 1


def test_coverage_survives_run_log_pruning(tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "STATE_FILE", tmp_path / "pipeline.json")
    monkeypatch.setattr(publish.config, "STATE_DIR", tmp_path)
    start = dt.date(2026, 1, 1)
    state = {"runs": [
        {"date": (start + dt.timedelta(days=i)).isoformat(),
         "index_rows": 100, "candidates": 20, "operating": 10}
        for i in range(70)]}
    publish.save_state(state)
    saved = json.loads(publish.STATE_FILE.read_text())
    assert len(saved["runs"]) == 60
    assert len(saved["scan_days"]) == 70
    publish.save_state(saved)
    assert len(publish.load_state()["scan_days"]) == 70
    assert _scan_metrics([], saved)["candidates_scanned"] == 1400


def test_missing_coverage_is_explicit():
    assert _scan_metrics([], {})["scan_days_covered"] == 0
    assert _scan_metrics([], {})["scan_first"] == ""
