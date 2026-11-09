"""A suspect new interpretation is held without hiding published defects."""
import dataclasses
import datetime as dt
import json

import pytest

from pipeline import analyze, config, health, models, publish, review, run, sections


@pytest.fixture
def storage(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setattr(config, "STATE_DIR", state_dir)
    monkeypatch.setattr(config, "EVENTS_DIR", tmp_path / "events")
    monkeypatch.setattr(publish, "STATE_FILE", state_dir / "pipeline.json")
    monkeypatch.setattr(publish, "HEALTH_FILE", state_dir / "health.json")
    monkeypatch.setattr(publish, "CORRECTIONS_FILE", tmp_path / "corrections.jsonl")
    return tmp_path


def candidate(*, contradictory=True, accession="0000000001-26-000001"):
    return models.Event(
        signal_type=models.MATERIAL_WEAKNESS, confidence=models.DERIVED,
        company="Example", cik=1, form="10-K", filed="2026-11-06",
        accession=accession,
        filing_url=f"https://www.sec.gov/Archives/edgar/data/1/{accession.replace('-', '')}/report.htm",
        headline="A claim that must never enter review metadata",
        evidence={"current_state": sections.ICFR_EFFECTIVE},
        quote=("Management cannot conclude that internal control over financial reporting is effective."
               if contradictory else
               "Management concluded that internal control over financial reporting is effective."),
    )


def test_only_new_contradictions_are_held_as_metadata(storage, caplog):
    bad = candidate()
    good = candidate(contradictory=False, accession="0000000001-26-000002")
    accepted, count = review.screen_incoming([bad, good], "2026-11-06")
    assert accepted == [good] and count == 1
    pending = review.load_pending()
    assert set(pending) == {bad.id}
    assert set(pending[bad.id]) == {
        "accession", "signal_type", "filed", "scan_day", "source_url", "reason"}
    saved = (config.STATE_DIR / "review_queue.json").read_text()
    assert bad.quote not in saved and bad.headline not in saved
    assert bad.id in caplog.text and review.REASON in caplog.text
    assert bad.quote not in caplog.text and bad.headline not in caplog.text


def test_relisted_or_absent_candidate_remains_one_unresolved_hold(storage):
    bad = candidate()
    review.screen_incoming([bad], "2026-11-06")
    before = review.load_pending()
    assert review.screen_incoming([bad, bad], "2026-11-09") == ([], 1)
    assert review.load_pending() == before
    assert review.screen_incoming([], "2026-11-10") == ([], 0)
    review.reconcile_published()
    assert review.load_pending() == before


def test_conflicting_copies_hold_the_whole_new_event_id(storage):
    bad = candidate()
    good = candidate(contradictory=False)
    assert good.id == bad.id
    assert review.screen_incoming([good, bad], "2026-11-06") == ([], 1)
    assert review.screen_incoming([bad, good], "2026-11-06") == ([], 1)


def test_valid_other_signal_from_same_filing_is_not_held(storage):
    bad = candidate()
    other = dataclasses.replace(bad, signal_type=models.AUDITOR_CHANGE,
                                quote="", evidence={"item_code": "4.01"})
    assert review.screen_incoming([bad, other], "2026-11-06") == ([other], 1)


def test_published_contradiction_still_reaches_the_hard_gate(storage):
    bad = candidate()
    publish.append_events(bad.filed, [bad])
    assert review.screen_incoming([bad], "2026-11-09") == ([], 0)
    assert review.load_pending() == {}
    recorded = publish.load_all_events()
    assert health._known_quote_contradictions(recorded) == recorded
    report = health.run_checks(recorded, {"last_processed": bad.filed}, dt.date(2026, 11, 8))
    check = next(c for c in report["checks"] if c["name"] == "Known quote contradictions")
    assert check["status"] == health.FAIL


def test_valid_rescan_releases_hold_only_after_actual_append(storage):
    bad = candidate()
    review.screen_incoming([bad], "2026-11-06")
    good = candidate(contradictory=False)
    accepted, held = review.screen_incoming([good], "2026-11-06")
    assert accepted == [good] and held == 0
    review.reconcile_published()  # append failed or was not attempted
    assert bad.id in review.load_pending()
    assert publish.append_events(good.filed, accepted) == 1
    review.reconcile_published()
    assert review.load_pending() == {}
    assert review.screen_incoming([bad], "2026-11-09") == ([], 0)
    assert len(publish.load_all_events()) == 1


def test_withdrawal_tombstone_prevents_requeue_and_resolves_hold(storage):
    bad = candidate()
    review.screen_incoming([bad], "2026-11-06")
    publish.CORRECTIONS_FILE.write_text(json.dumps({"event_id": bad.id, "action": "withdraw"}) + "\n")
    assert review.screen_incoming([bad], "2026-11-09") == ([], 0)
    assert review.load_pending() == {}


def test_review_warning_exists_even_when_every_candidate_is_held(storage):
    review.screen_incoming([candidate()], "2026-11-06")
    report = health.run_checks([], {"last_processed": "2026-11-06"}, dt.date(2026, 11, 8))
    check = next(c for c in report["checks"] if c["name"] == "Incoming candidates awaiting review")
    assert check["status"] == health.WARN
    assert "1 candidate(s)" in check["detail"]
    assert "excluded from published event totals" in check["detail"]


def test_corrupt_queue_is_not_discarded_or_downgraded_to_warning(storage):
    config.STATE_DIR.mkdir()
    (config.STATE_DIR / "review_queue.json").write_text("{broken")
    with pytest.raises(ValueError):
        review.screen_incoming([candidate()], "2026-11-06")
    report = health.run_checks([], {"last_processed": "2026-11-06"}, dt.date(2026, 11, 8))
    check = next(c for c in report["checks"] if c["name"] == "Incoming candidates awaiting review")
    assert check["status"] == health.FAIL


def setup_scan(monkeypatch, events):
    monkeypatch.setattr(config, "require_user_agent", lambda: None)
    monkeypatch.setattr(run.size, "load_or_refresh", lambda *args: {})
    monkeypatch.setattr(analyze, "get_analyzer", analyze.NullAnalyzer)
    monkeypatch.setattr(run, "history_needs_refresh", lambda *args: False)
    monkeypatch.setattr(run, "process_day", lambda day: (events, {
        "date": day.isoformat(), "index_rows": 12, "candidates": 2,
        "operating": 2, "events": len(events)}))
    # Other status checks are covered separately; keep this integration local.
    monkeypatch.setattr(health, "run_checks", lambda *args, **kwargs: {
        "checks": [], "summary": {"overall": "ok", "ok": 0, "warn": 0, "fail": 0}})


def test_scan_publishes_valid_events_and_records_individual_review_hold(storage, monkeypatch):
    bad = candidate()
    good = candidate(contradictory=False, accession="0000000001-26-000002")
    setup_scan(monkeypatch, [bad, good])
    assert run.main(["--date", "2026-11-06", "--no-render"]) == 0
    assert [event.id for event in publish.load_all_events()] == [good.id]
    assert set(review.load_pending()) == {bad.id}
    state = publish.load_state()
    assert state["last_processed"] == "2026-11-06"
    assert not state.get("pending_dates")
    assert state["runs"][-1]["events_written"] == 1
    assert state["runs"][-1]["events_held_for_review"] == 1
    assert run.main(["--date", "2026-11-06", "--no-render"]) == 0
    assert publish.load_state()["runs"][-1]["events_written"] == 0
    assert len(review.load_pending()) == 1


def test_queue_write_failure_stops_scan_before_append_or_advancing_day(storage, monkeypatch):
    setup_scan(monkeypatch, [candidate()])
    def cannot_save(_):
        raise OSError("review storage unavailable")
    monkeypatch.setattr(review, "_save", cannot_save)
    with pytest.raises(OSError, match="review storage unavailable"):
        run.main(["--date", "2026-11-06", "--no-render"])
    assert publish.load_all_events() == []
    assert publish.load_state()["last_processed"] is None


def test_quiet_run_resolves_explicit_withdrawal(storage, monkeypatch):
    bad = candidate()
    review.screen_incoming([bad], "2026-11-06")
    publish.CORRECTIONS_FILE.write_text(json.dumps({"event_id": bad.id, "action": "withdraw"}) + "\n")
    setup_scan(monkeypatch, [])
    monkeypatch.setattr(run, "days_to_process", lambda *args, **kwargs: [])
    monkeypatch.setattr(run, "days_to_backfill", lambda *args: [])
    assert run.main(["--no-render"]) == 0
    assert review.load_pending() == {}


def test_quiet_run_fails_hard_on_corrupt_queue(storage, monkeypatch):
    config.STATE_DIR.mkdir()
    (config.STATE_DIR / "review_queue.json").write_text("{broken")
    setup_scan(monkeypatch, [])
    monkeypatch.setattr(run, "days_to_process", lambda *args, **kwargs: [])
    monkeypatch.setattr(run, "days_to_backfill", lambda *args: [])
    with pytest.raises(ValueError):
        run.main(["--no-render"])
