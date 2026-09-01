"""Where a quote is allowed to start.

Six quotes from the first fill week began mid-sentence, and each traced to a
boundary the extractors could not see: the decimal point in "$122.9" read as a
full stop, a table with no periods at all, a note opening with its own number,
and a list-style sentence too long to have a boundary in reach. Each shape is
pinned here against the extractor that mishandled it.
"""
import re

from pipeline import sections

OPENS_CLEANLY = re.compile(r'^[A-Z“("•…]')


def test_a_table_before_the_asu_is_not_quoted():
    """Microsoft's ASU 2023-09 adoption sits right after the income-tax table.
    The old window quoted the table and truncated the sentence."""
    t = ("Revenue grew. $ 103,591 $ 69,212 $ 62,886 Foreign 62,343 54,415 "
         "Income before income taxes $ 165,934 73 PART II Item 8 Effective "
         "Tax Rate We adopted Accounting Standards Update ASU 2023-09, "
         "Income Taxes, in fiscal 2026. It requires disaggregation.")
    ctx = sections.extract_asus(t)["2023-09"]["context"]
    assert ctx.startswith("We adopted")
    assert "$" not in ctx


def test_a_decimal_point_is_not_a_sentence_boundary():
    """Spruce Power: the backward scan stopped at the "." in "$122.9" and the
    published quote began right after it."""
    t = ("The Company reported losses including negative working capital of "
         "$122.9 million and other conditions. The Company adopted "
         "ASU 2020-06 in the current period to simplify accounting.")
    ctx = sections.extract_asus(t)["2020-06"]["context"]
    assert ctx.startswith("The Company adopted")


def test_a_note_number_is_not_part_of_the_quote():
    """Alternus: the note text begins "2. Going Concern..." and the number was
    quoted, so the quote opened with a digit."""
    ctx = sections._context("2. Going Concern and Management's Plans As of "
                            "March 31, 2026, conditions raise substantial "
                            "doubt about the Company.", 20)
    assert ctx.startswith("Going Concern")


def test_an_unbounded_sentence_is_marked_as_an_excerpt():
    """A note that is one enormous (i)...(vi) list has no boundary in reach.
    Starting mid-sentence is unavoidable; doing it unmarked is not."""
    body = ("conditions including " + "item and further conditions " * 30
            + "substantial doubt about the ability to continue")
    ctx = sections._context(body, len(body) - 20)
    assert ctx.startswith("…"), ctx[:60]
    assert OPENS_CLEANLY.match(ctx)
