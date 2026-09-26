"""Sequence rates need complete listed records and a mature follow-on window."""
import datetime as dt

import pytest

from pipeline import history
from pipeline.models import AUDITOR_CHANGE, LATE_FILING, RESTATEMENT


def block(*rows):
    """Rows are (accession, date, form, items), like SEC columnar JSON."""
    return {key: [row[i] for row in rows] for i, key in enumerate(
        ("accessionNumber", "filingDate", "form", "items"))}


def submissions(recent, *files):
    return {"filings": {"recent": recent, "files": [{"name": f} for f in files]}}


def event(kind, date):
    return {"kind": kind, "date": date}


def test_archives_can_change_the_first_event_and_the_measured_outcome(monkeypatch):
    """A recent-only precursor makes a later restatement look like a pair;
    the older precursor actually puts it years outside the window."""
    archive = "CIK0000000123-submissions-001.json"
    sub = submissions(block(
        ("new-auditor", "2023-01-01", "8-K", "4.01"),
        ("restatement", "2023-02-01", "8-K", "4.02")), archive)
    old = block(("old-auditor", "2010-01-01", "8-K", "4.01"))
    monkeypatch.setattr(history.compare, "submissions", lambda cik: sub)
    fetched = []

    def fetch(url):
        fetched.append(url)
        return old

    monkeypatch.setattr(history.fetch, "get_json", fetch)
    as_of = dt.date(2026, 9, 24)
    recent_only = history._events_from_submissions(sub)
    full = history.company_history(123)
    assert history._followed_within(recent_only, AUDITOR_CHANGE, RESTATEMENT,
                                   as_of=as_of) is True
    assert history._followed_within(full, AUDITOR_CHANGE, RESTATEMENT,
                                   as_of=as_of) is False
    assert fetched == [f"{history.config.SUBMISSIONS}/{archive}"]
    assert full[0]["date"] == "2010-01-01"


def test_archive_overlap_is_deduplicated_by_accession_and_signal(monkeypatch):
    archive = "CIK0000000123-submissions-001.json"
    rows = block(("same-filing", "2020-01-01", "8-K", "4.01,4.02"))
    monkeypatch.setattr(history.compare, "submissions",
                        lambda cik: submissions(rows, archive, archive))
    fetched = []

    def fetch(url):
        fetched.append(url)
        return rows

    monkeypatch.setattr(history.fetch, "get_json", fetch)
    events = history.company_history(123)
    assert len(fetched) == 1
    assert len(events) == 2
    assert {e["kind"] for e in events} == {AUDITOR_CHANGE, RESTATEMENT}


@pytest.mark.parametrize("missing", [None, {}, {"filings": {"recent": block()}}])
def test_missing_submissions_are_not_a_successful_empty_history(monkeypatch, missing):
    monkeypatch.setattr(history.compare, "submissions", lambda cik: missing)
    with pytest.raises(ValueError):
        history.company_history(123)


@pytest.mark.parametrize("older", [None, {}, {"form": ["8-K"]}])
def test_missing_or_malformed_archive_excludes_the_whole_company(monkeypatch, older):
    sub = submissions(block(("a", "2020-01-01", "8-K", "4.01")),
                      "CIK0000000123-submissions-001.json")
    monkeypatch.setattr(history.compare, "submissions", lambda cik: sub)
    monkeypatch.setattr(history.fetch, "get_json", lambda url: older)
    stats = history.sequence_rates([123], as_of=dt.date(2026, 9, 24))
    assert stats["requested_companies"] == 1
    assert stats["companies"] == 0
    assert stats["omitted_companies"] == 1
    assert stats["omitted_ciks"] == [123]
    assert all(r["eligible"] == 0 and r["rate"] == "—" for r in stats["rows"])


def test_a_valid_empty_history_can_be_counted_as_loaded(monkeypatch):
    monkeypatch.setattr(history.compare, "submissions",
                        lambda cik: submissions(block()))
    stats = history.sequence_rates([123])
    assert stats["companies"] == 1
    assert stats["omitted_companies"] == 0


def test_an_archive_access_block_aborts_the_refresh(monkeypatch):
    monkeypatch.setattr(history.compare, "submissions", lambda cik: submissions(
        block(), "CIK0000000123-submissions-001.json"))

    def blocked(url):
        raise history.fetch.SECBlocked("blocked")

    monkeypatch.setattr(history.fetch, "get_json", blocked)
    with pytest.raises(history.fetch.SECBlocked):
        history.sequence_rates([123, 456])


def test_archive_names_cannot_fetch_another_company_or_url(monkeypatch):
    monkeypatch.setattr(history.compare, "submissions", lambda cik: submissions(
        block(), "CIK0000000456-submissions-001.json"))
    with pytest.raises(ValueError, match="archive name"):
        history.company_history(123)


def test_immature_successes_and_failures_are_both_excluded(monkeypatch):
    as_of = dt.date(2026, 9, 24)
    recent = (as_of - dt.timedelta(days=100)).isoformat()
    data = {
        1: [event(AUDITOR_CHANGE, "2020-01-01"),
            event(RESTATEMENT, "2020-04-01")],
        2: [event(AUDITOR_CHANGE, "2020-01-01")],
        3: [event(AUDITOR_CHANGE, recent), event(RESTATEMENT, as_of.isoformat())],
        4: [event(AUDITOR_CHANGE, recent)],
        5: [event(LATE_FILING, "2020-01-01")],
        6: [event(AUDITOR_CHANGE, "2030-01-01")],
    }
    monkeypatch.setattr(history, "company_history", lambda cik: data[cik])
    stats = history.sequence_rates([1, 2, 3, 4, 5, 6, 1], as_of=as_of)
    row = stats["rows"][0]
    assert row["eligible"] == 2
    assert row["followed"] == 1
    assert row["rate"] == "50%"
    assert row["with_first"] == 4
    assert row["immature"] == 2
    assert stats["requested_companies"] == 6
    assert stats["as_of"] == "2026-09-24"
    assert stats["methodology_version"] == history.METHODOLOGY_VERSION
    assert stats["total_historical_events"] == 7  # excludes future events


def test_window_boundary_is_inclusive_but_same_day_is_not():
    first = dt.date(2020, 1, 1)
    last = first + dt.timedelta(days=history.WINDOW_DAYS)
    events = [event(AUDITOR_CHANGE, first.isoformat()),
              event(RESTATEMENT, last.isoformat())]
    assert history._followed_within(events, AUDITOR_CHANGE, RESTATEMENT,
                                   as_of=last - dt.timedelta(days=1)) is None
    assert history._followed_within(events, AUDITOR_CHANGE, RESTATEMENT,
                                   as_of=last) is True
    for days in (0, history.WINDOW_DAYS + 1):
        events[1]["date"] = (first + dt.timedelta(days=days)).isoformat()
        assert history._followed_within(events, AUDITOR_CHANGE, RESTATEMENT,
                                       as_of=last + dt.timedelta(days=1)) is False


def test_the_earliest_precursor_is_used_even_when_input_is_not_sorted():
    events = [event(AUDITOR_CHANGE, "2023-01-01"),
              event(RESTATEMENT, "2023-02-01"),
              event(AUDITOR_CHANGE, "2010-01-01")]
    assert history._followed_within(events, AUDITOR_CHANGE, RESTATEMENT,
                                   as_of=dt.date(2026, 9, 24)) is False


def test_precursors_before_current_item_coding_do_not_create_unobservable_windows(monkeypatch):
    """A 1996 late notice cannot be paired with item 4.02, introduced in 2004.
    Measure every signal from the same coding era instead."""
    data = {
        1: [event(LATE_FILING, "1996-01-01"),
            event(LATE_FILING, "2004-08-23"),
            event(RESTATEMENT, "2004-09-01")],
        2: [event(LATE_FILING, "2004-08-22")],
    }
    monkeypatch.setattr(history, "company_history", lambda cik: data[cik])
    stats = history.sequence_rates([1, 2], as_of=dt.date(2026, 9, 24))
    row = next(r for r in stats["rows"]
               if r["first"] == LATE_FILING and r["second"] == RESTATEMENT)
    assert stats["observation_start"] == "2004-08-23"
    assert stats["total_historical_events"] == 2
    assert row["eligible"] == 1
    assert row["followed"] == 1
