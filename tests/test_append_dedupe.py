"""append_events must not write a duplicate it can see in its own batch.

A duplicate used to be written, counted in the run total, and removed later by
the repair sweep -- so the number reported for the day was wrong, and staying
correct depended on a separate pass running afterwards.
"""
import pathlib
import tempfile
from unittest import mock

from pipeline import config, publish
from pipeline.models import Event


def _event(accession="0000000000-26-000001", signal="restatement"):
    return Event(signal_type=signal, confidence="confirmed", company="Example Corp",
                 cik=1, form="8-K", filed="2026-01-05", accession=accession,
                 filing_url="https://www.sec.gov/x", headline="something happened")


def _write(events):
    with tempfile.TemporaryDirectory() as d:
        with mock.patch.object(config, "EVENTS_DIR", pathlib.Path(d)):
            n = publish.append_events("2026-01-05", events)
            rows = (pathlib.Path(d) / "2026-01-05.jsonl").read_text().strip().splitlines()
            return n, len(rows)


def test_the_same_event_twice_in_one_batch_is_written_once():
    reported, on_disk = _write([_event(), _event()])
    assert on_disk == 1
    assert reported == 1          # the count must match what landed


def test_distinct_events_all_survive():
    reported, on_disk = _write(
        [_event(), _event(signal="auditor_change"), _event(accession="0000000000-26-000002")])
    assert (reported, on_disk) == (3, 3)


def test_rerunning_a_day_adds_nothing():
    with tempfile.TemporaryDirectory() as d:
        with mock.patch.object(config, "EVENTS_DIR", pathlib.Path(d)):
            publish.append_events("2026-01-05", [_event()])
            assert publish.append_events("2026-01-05", [_event()]) == 0
            rows = (pathlib.Path(d) / "2026-01-05.jsonl").read_text().strip().splitlines()
            assert len(rows) == 1


# --- the same filing arriving under two different days -----------------------
def test_a_filing_relisted_on_a_later_day_is_recorded_once(tmp_path, monkeypatch):
    """EDGAR re-lists a filing in a later daily index. Four did between 31
    March and 3 April 2026 - Cyclerion's 10-K among them. The key is
    (accession, signal_type) with no day in it, but the append only compared
    against the day being written, so each was stored twice and the integrity
    check stopped the run. It could not clear either: the next run wrote the
    second copy again."""
    monkeypatch.setattr(publish.config, "EVENTS_DIR", tmp_path)

    def relisted():
        return Event(
            signal_type="restatement", confidence="confirmed",
            company="Cyclerion Therapeutics, Inc.", cik=1755237, form="10-K",
            filed="2026-03-31", accession="0001193125-26-132193",
            filing_url="u", headline="h")

    assert publish.append_events("2026-03-31", [relisted()]) == 1
    assert publish.append_events("2026-04-03", [relisted()]) == 0

    rows = sum(len([l for l in p.read_text().splitlines() if l.strip()])
               for p in tmp_path.glob("*.jsonl"))
    assert rows == 1, "the same filing was stored under both days"
    assert not (tmp_path / "2026-04-03.jsonl").read_text().strip() \
        if (tmp_path / "2026-04-03.jsonl").exists() else True


def test_the_check_that_caught_it_still_catches_it(tmp_path, monkeypatch):
    """Guarding the guard: if a duplicate does reach disk by another route,
    the run must still stop rather than publish it."""
    monkeypatch.setattr(publish.config, "EVENTS_DIR", tmp_path)
    row = ('{"accession": "0001-26-000001", "cik": 1, "company": "X", '
           '"confidence": "confirmed", "evidence": {}, "filed": "2026-03-31", '
           '"filing_url": "u", "form": "10-K", "headline": "h", '
           '"id": "deadbeefdeadbeef", "signal_type": "restatement"}\n')
    (tmp_path / "2026-03-31.jsonl").write_text(row)
    (tmp_path / "2026-04-03.jsonl").write_text(row)
    assert publish.raw_row_count() == 2
    assert len({e.id for e in publish.load_all_events()}) == 1
