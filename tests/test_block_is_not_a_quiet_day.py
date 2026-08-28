"""A refusal must never be recorded as a day with nothing in it.

SECBlocked subclasses RuntimeError, so every `except Exception` in the fetch
path swallowed it. A block part-way through a day then looked exactly like a
quiet day: no filings readable, no events, the day marked processed, and --
because earliest_processed only moves earlier -- never fetched again. With the
history fill writing ~155 days unattended, that would have left permanent holes
nobody would notice.
"""
from unittest import mock

import pytest

from pipeline import compare, enrich, fetch, triage


def _blocked(*a, **k):
    raise fetch.SECBlocked("403 from SEC")


def test_submissions_lets_a_block_through():
    with mock.patch.object(fetch, "get_json", _blocked), \
         pytest.raises(fetch.SECBlocked):
        compare.submissions(320193)


def test_load_text_lets_a_block_through():
    with mock.patch.object(fetch, "get", _blocked), \
         pytest.raises(fetch.SECBlocked):
        compare.load_text("https://www.sec.gov/anything")


def test_header_enrichment_lets_a_block_through():
    filing = mock.Mock(cik=320193, accession="0000320193-26-000001")
    with mock.patch.object(fetch, "get", _blocked), \
         pytest.raises(fetch.SECBlocked):
        enrich.enrich(filing)


def test_a_missing_document_is_still_swallowed():
    """The guard must not turn ordinary 404s and parse failures into crashes --
    those really do mean "skip this filing"."""
    with mock.patch.object(fetch, "get", side_effect=RuntimeError("gone")):
        assert compare.load_text("https://www.sec.gov/missing") is None
    filing = mock.Mock(cik=1, accession="x")
    with mock.patch.object(fetch, "get", side_effect=RuntimeError("gone")):
        assert enrich.enrich(filing) is False


@pytest.mark.parametrize("fn", ["_subclassify", "_officer_event"])
def test_triage_document_reads_let_a_block_through(fn):
    filing = mock.Mock(cik=1, accession="a", form="8-K")
    with mock.patch.object(compare, "current_document", _blocked), \
         pytest.raises(fetch.SECBlocked):
        getattr(triage, fn)("restatement", filing) if fn == "_subclassify" \
            else getattr(triage, fn)(filing)


def test_an_amendment_that_omits_the_note_is_not_a_downgrade():
    """Artelo and Global Arena both amended within eight days of the original
    and were published as no longer disclosing substantial doubt. The note was
    absent because it was not re-filed, not because the conclusion changed."""
    from pipeline import sections
    from pipeline.compare import amendment_without_the_section as skip

    assert skip("10-Q/A", sections.GC_NONE, "") is True
    assert skip("10-K/A", sections.GC_NONE, None) is True


def test_the_guard_does_not_swallow_amendments_that_say_something():
    """Three material-weakness amendments state a real conclusion for the
    period and are correct. Suppressing amendments wholesale would lose them."""
    from pipeline import sections
    from pipeline.compare import amendment_without_the_section as skip

    assert skip("10-Q/A", sections.GC_SUBSTANTIAL_DOUBT, "substantial doubt exists") is False
    assert skip("10-Q/A", sections.GC_NONE, "no going-concern conditions were identified") is False
    assert skip("10-Q", sections.GC_NONE, "") is False        # not an amendment
