"""Two index files, and a refusal to accept an empty business day.

form.idx was the only source, and a truncated one - EDGAR serving a file it
was still writing, or a response cut off part-way - parsed as far as it went
and marked the day processed with whatever filings preceded the cut. Nothing
failed, and the day was never looked at again. master.idx is the same rows in
a different format, generated separately; it is read when form.idx is missing
or implausibly small. And a business day that yields nothing from either file
is left alone rather than recorded as anything.
"""
import datetime as dt

import pytest

from pipeline import ingest

DAY = dt.date(2026, 9, 11)


def _form_rows(n):
    head = ("Description: Daily Index\n\nForm Type   Company Name   CIK   "
            "Date Filed   File Name\n" + "-" * 80 + "\n")
    return head + "".join(
        f"8-K         COMPANY {i:<28}  {1000 + i}  20260911  "
        f"edgar/data/{1000 + i}/0001234567-26-{i:06d}.txt\n" for i in range(n))


def _master_rows(n):
    head = "Description: Daily Index\n\nCIK|Company Name|Form Type|Date Filed|File Name\n" + "-" * 80 + "\n"
    return head + "".join(
        f"{1000 + i}|COMPANY {i}|8-K|20260911|edgar/data/{1000 + i}/0001234567-26-{i:06d}.txt\n"
        for i in range(n))


def _serve(monkeypatch, form=None, master=None):
    """form/master: a body string, None for 'not there' (403 from EDGAR)."""
    def get(url, accept_404=False, **k):
        body = form if "/form." in url else master if "/master." in url else ""
        if body is None:
            raise ingest.fetch.SECBlocked("HTTP 403")
        return body
    monkeypatch.setattr(ingest.fetch, "get", get)
    monkeypatch.setattr(ingest, "sec_reachable", lambda: True)


def test_the_two_parsers_agree_on_the_same_day():
    assert ({f.accession for f in ingest.parse_index(_form_rows(50))}
            == {f.accession for f in ingest.parse_master_index(_master_rows(50))})


def test_a_missing_form_index_falls_back_to_master(monkeypatch):
    _serve(monkeypatch, form=None, master=_master_rows(3000))
    rows = ingest.fetch_day(DAY)
    assert rows is not None and len(rows) == 3000


def test_a_truncated_form_index_is_not_believed(monkeypatch):
    """The failure this exists for: form.idx cut off after 40 rows on a day
    that had 3,000. Before, those 40 were the day."""
    _serve(monkeypatch, form=_form_rows(40), master=_master_rows(3000))
    rows = ingest.fetch_day(DAY)
    assert len(rows) == 3000


def test_a_healthy_form_index_does_not_read_master_at_all(monkeypatch):
    """Two requests a day for the same information would be a cost with no
    benefit, and it is SEC capacity being spent."""
    asked = []
    def get(url, accept_404=False, **k):
        asked.append(url)
        return _form_rows(3000)
    monkeypatch.setattr(ingest.fetch, "get", get)
    ingest.fetch_day(DAY)
    assert len(asked) == 1 and "/form." in asked[0]


def test_an_index_that_exists_but_is_empty_is_left_for_next_time(monkeypatch):
    """Not a holiday, not a day with no filings - a file EDGAR had not
    finished writing. Marking it either way loses the day for good."""
    _serve(monkeypatch, form="Description: Daily Index\n", master="")
    with pytest.raises(ingest.IndexUnusable):
        ingest.fetch_day(DAY)


def test_no_index_at_all_is_still_a_holiday(monkeypatch):
    _serve(monkeypatch, form=None, master=None)
    assert ingest.fetch_day(DAY) is None


def test_a_genuinely_quiet_day_is_accepted_when_both_files_agree(monkeypatch):
    """Small is suspicious, not forbidden: if master says the same, it was a
    quiet day and the rows are real."""
    _serve(monkeypatch, form=_form_rows(120), master=_master_rows(120))
    assert len(ingest.fetch_day(DAY)) == 120


def test_the_run_leaves_an_unusable_day_untouched():
    """Neither processed nor recorded as a non-filing day: the next run must
    ask about it again."""
    from unittest import mock

    from pipeline import config, run

    saved, non_filing = {}, []
    with mock.patch.object(config, "require_user_agent", lambda: None), \
         mock.patch.object(config, "HISTORY_FROM", None), \
         mock.patch.object(run, "days_to_process", return_value=[DAY]), \
         mock.patch.object(run, "days_to_backfill", return_value=[]), \
         mock.patch.object(run, "process_day",
                           side_effect=ingest.IndexUnusable("empty")), \
         mock.patch.object(run.analyze, "get_analyzer"), \
         mock.patch.object(run.size, "load_or_refresh", return_value={}), \
         mock.patch.object(run.history, "sequence_rates", return_value={}), \
         mock.patch.object(run.publish, "save_history"), \
         mock.patch.object(run.publish, "load_state",
                           return_value={"runs": [], "last_processed": "2026-09-10"}), \
         mock.patch.object(run.health, "run_checks", return_value={"checks": []}), \
         mock.patch.object(run.publish, "save_health"), \
         mock.patch.object(run.publish, "record_non_filing_day",
                           side_effect=lambda s, d: non_filing.append(d)), \
         mock.patch.object(run.publish, "save_state",
                           side_effect=lambda s: saved.update(s)):
        run.main(["--no-render"])

    assert non_filing == [], "an empty index was recorded as a holiday"
    assert saved.get("last_processed") == "2026-09-10", "the day was marked processed"
