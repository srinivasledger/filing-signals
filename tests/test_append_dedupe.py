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
