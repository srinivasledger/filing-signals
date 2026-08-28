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
