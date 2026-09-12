"""History fill.

The fill runs unattended a chunk per night, so the day-selection has to be
right without anyone watching: it must never re-fetch a day it already has,
never step past the floor, and never crowd out the day's actual filings.
"""
import datetime as dt
from unittest import mock

from pipeline import config, run


def _state(dates, earliest=None):
    s = {"runs": [{"date": d} for d in dates]}
    if earliest:
        s["earliest_processed"] = earliest
    return s


def _fill(state, already=(), floor="2026-01-01", chunk=5):
    with mock.patch.object(config, "HISTORY_FROM", floor), \
         mock.patch.object(config, "HISTORY_CHUNK", chunk):
        return run.days_to_backfill(state, [dt.date.fromisoformat(d) for d in already])


def test_it_walks_back_from_the_oldest_day_held():
    days = _fill(_state(["2026-08-12", "2026-08-13"]))
    assert days[-1] == dt.date(2026, 8, 11)          # the day before the oldest
    assert len(days) == 5                            # bounded by the chunk
    assert days == sorted(days)


def test_it_never_steps_past_the_floor():
    # only three business days sit between the floor and what is held
    days = _fill(_state(["2026-01-06"]), floor="2026-01-01", chunk=50)
    assert days[0] >= dt.date(2026, 1, 1)
    assert days[-1] == dt.date(2026, 1, 5)


def test_nothing_left_to_fill_returns_nothing():
    assert _fill(_state(["2026-01-01"]), floor="2026-01-01") == []


def test_it_is_off_unless_a_floor_is_configured():
    with mock.patch.object(config, "HISTORY_FROM", None):
        assert run.days_to_backfill(_state(["2026-08-12"]), []) == []


def test_days_queued_this_run_count_as_held():
    """Otherwise a run that is catching up forward would pick a fill day it is
    about to process anyway, and fetch it twice."""
    days = _fill(_state(["2026-08-12"]), already=["2026-08-10", "2026-08-11"])
    assert dt.date(2026, 8, 10) not in days
    assert days[-1] == dt.date(2026, 8, 7)           # the Friday before

def test_a_bad_floor_disables_it_rather_than_crashing():
    assert _fill(_state(["2026-08-12"]), floor="not-a-date") == []


def test_the_fill_yields_to_the_budget_but_the_day_s_filings_do_not():
    """A hosted job is killed at a hard limit, and the kill lands before the
    commit step -- so an over-long run discards everything it did. The fill
    must stop in time; catching up on today must not."""
    import time as _time

    forward = [dt.date(2026, 8, 28)]
    fill = [dt.date(2026, 8, 10), dt.date(2026, 8, 7)]
    processed = []

    def fake_process(day):
        processed.append(day)
        return [], {"date": day.isoformat(), "index_rows": 1, "candidates": 0,
                    "operating": 0, "events": 0}

    clock = iter([0.0] + [10_000.0] * 20)        # first call inside budget, then over
    # Every writer run.main reaches has to be stubbed by its real name. This
    # block used to patch a "history.refresh" that does not exist, with
    # create=True silencing the AttributeError that would have said so, and
    # the unmocked history pass then wrote its empty result over the repo's
    # own data/state/history.json - the exact corruption the "Follow-on rates
    # computed" check was added to catch, caused by running the tests.
    with mock.patch.object(config, "require_user_agent", lambda: None), \
         mock.patch.object(config, "HISTORY_FROM", "2026-01-01"), \
         mock.patch.object(config, "HISTORY_BUDGET_SECONDS", 3600), \
         mock.patch.object(run, "days_to_backfill", return_value=fill), \
         mock.patch.object(run, "days_to_process", return_value=forward), \
         mock.patch.object(run, "process_day", side_effect=fake_process), \
         mock.patch.object(_time, "monotonic", lambda: next(clock)), \
         mock.patch.object(run.analyze, "get_analyzer"), \
         mock.patch.object(run.size, "load_or_refresh", return_value={}), \
         mock.patch.object(run.publish, "append_events", return_value=0), \
         mock.patch.object(run.publish, "save_state"), \
         mock.patch.object(run.publish, "load_state", return_value={"runs": []}), \
         mock.patch.object(run.history, "sequence_rates", return_value={}), \
         mock.patch.object(run.publish, "save_history"), \
         mock.patch.object(run.health, "run_checks", return_value={"checks": []}), \
         mock.patch.object(run.publish, "save_health"):
        run.main(["--no-render"])

    assert forward[0] in processed, "the day's own filings must always run"
    assert fill[0] not in processed, "the fill must yield once the budget is spent"


def test_an_empty_history_never_overwrites_a_computed_one(tmp_path, monkeypatch):
    """The rates cost one request per company to build. A process that
    computed nothing must not be able to erase them - which is how they were
    lost twice, the last time by running the tests."""
    import json

    from pipeline import publish

    monkeypatch.setattr(publish.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(publish, "HISTORY_FILE", tmp_path / "history.json")

    publish.save_history({"companies": 900, "total_historical_events": 10_299})
    publish.save_history({"companies": 900, "total_historical_events": 0})
    assert json.loads((tmp_path / "history.json").read_text())[
        "total_historical_events"] == 10_299

    # A real result still replaces it.
    publish.save_history({"companies": 950, "total_historical_events": 11_000})
    assert json.loads((tmp_path / "history.json").read_text())[
        "total_historical_events"] == 11_000


# --- a holiday is not the pipeline falling behind ----------------------------
def test_a_day_the_sec_was_closed_is_not_counted_as_behind():
    """Labor Day 2026 fell on Monday 7 September. The dataset was correctly
    current through Friday the 4th, and the check called it two business days
    behind and warned, because it counted weekdays and nothing else."""
    from pipeline import health

    friday, tuesday = dt.date(2026, 9, 4), dt.date(2026, 9, 8)
    assert health._business_days_between(friday, tuesday) == 2
    assert health._business_days_between(friday, tuesday, ["2026-09-07"]) == 1


def test_the_page_says_which_day_the_sec_was_shut():
    """A quiet day has to explain itself, or the reader is left comparing the
    date on the page with a calendar."""
    from pipeline import health

    state = {"last_processed": "2026-09-04", "no_filings": ["2026-09-07"],
             "runs": []}
    checks = health.run_checks([], state, dt.date(2026, 9, 8))["checks"]
    current = next(c for c in checks if c["name"] == "Pipeline is current")
    assert current["status"] == health.OK, current
    assert "2026-09-07" in current["detail"]
    assert "published no index" in current["detail"]


def test_a_genuine_gap_still_warns():
    """The holiday allowance must not become a way to look current while the
    scans are actually failing."""
    from pipeline import health

    state = {"last_processed": "2026-09-01", "no_filings": [], "runs": []}
    current = next(c for c in health.run_checks([], state, dt.date(2026, 9, 8))["checks"]
                   if c["name"] == "Pipeline is current")
    assert current["status"] in (health.WARN, health.FAIL), current


def test_a_day_scanned_only_in_part_says_so():
    """The per-day cap drops periodic reports on a day that is then marked
    processed and never revisited. It was a log line in a run nobody reads."""
    from pipeline import health, models

    # run_checks returns early with no events, so give it one.
    event = models.Event(
        signal_type=models.RESTATEMENT, confidence=models.CONFIRMED,
        company="X", cik=1, form="8-K", filed="2026-09-04",
        accession="0001-26-000001",
        filing_url="https://www.sec.gov/Archives/000126000001-index.htm",
        headline="h", evidence={"source": "SEC 8-K item code"})
    state = {"last_processed": "2026-09-04", "no_filings": ["2026-09-07"],
             "runs": [{"date": "2026-04-28", "index_rows": 1, "candidates": 1,
                       "operating": 1, "events": 0, "periodic_skipped": 39}]}
    checks = health.run_checks([event], state, dt.date(2026, 9, 8))["checks"]
    scanned = next(c for c in checks if c["name"] == "Days scanned in full")
    assert scanned["status"] == health.WARN
    assert "39" in scanned["detail"]

    state["runs"][0].pop("periodic_skipped")
    ok = next(c for c in health.run_checks([event], state, dt.date(2026, 9, 8))["checks"]
              if c["name"] == "Days scanned in full")
    assert ok["status"] == health.OK


# --- the button makes redundant runs a thing people can do -------------------
def test_the_rates_are_not_recomputed_when_nothing_moved(tmp_path, monkeypatch):
    """One SEC request per company, and the company count only grows. A scan
    started by hand on a quiet day was spending over a thousand requests to
    arrive at the answer already on disk."""
    import json

    from pipeline import publish, run

    monkeypatch.setattr(publish.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(publish, "HISTORY_FILE", tmp_path / "history.json")
    today = dt.date.today().isoformat()
    (tmp_path / "history.json").write_text(json.dumps(
        {"companies": 1366, "total_historical_events": 12497, "built_on": today}))

    assert run.history_needs_refresh(0, 1366) is False, "recomputed for nothing"
    assert run.history_needs_refresh(7, 1366) is True, "new events must refresh"
    assert run.history_needs_refresh(0, 1400) is True, "new companies must refresh"


def test_the_rates_are_refreshed_when_they_go_stale(tmp_path, monkeypatch):
    """Skipping is an optimisation, not a way to stop maintaining them: a long
    quiet stretch must not leave the published rates drifting."""
    import json

    from pipeline import publish, run

    monkeypatch.setattr(publish.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(publish, "HISTORY_FILE", tmp_path / "history.json")
    old = (dt.date.today() - dt.timedelta(days=run.HISTORY_MAX_AGE_DAYS)).isoformat()
    (tmp_path / "history.json").write_text(json.dumps(
        {"companies": 1366, "total_historical_events": 12497, "built_on": old}))
    assert run.history_needs_refresh(0, 1366) is True


def test_a_history_written_before_this_existed_is_refreshed_once(tmp_path, monkeypatch):
    """No built_on means it predates the stamp; recompute so it gets one."""
    import json

    from pipeline import publish, run

    monkeypatch.setattr(publish.config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(publish, "HISTORY_FILE", tmp_path / "history.json")
    (tmp_path / "history.json").write_text(json.dumps(
        {"companies": 1366, "total_historical_events": 12497}))
    assert run.history_needs_refresh(0, 1366) is True


def test_the_fill_finishes_when_only_a_holiday_is_left():
    """It reached 2 January 2026 with New Year's Day as the only day between it
    and the floor. A holiday returns no index and so never moves
    earliest_processed, so the fill asked for 1 January on every run
    indefinitely and was never done."""
    with mock.patch.object(config, "HISTORY_FROM", "2026-01-01"):
        state = {"earliest_processed": "2026-01-02", "runs": [],
                 "no_filings": ["2026-01-01"]}
        assert run.days_to_backfill(state, []) == []

        # Without the record it would still, correctly, try the day once.
        state["no_filings"] = []
        assert run.days_to_backfill(state, []) == [dt.date(2026, 1, 1)]
