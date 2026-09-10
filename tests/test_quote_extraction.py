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


# --- and where a quote is allowed to end -------------------------------------
#
# The opening was pinned from the first week. The closing never was, and ten
# published quotes ended mid-word with nothing to mark the cut, because the
# going-concern note was taken as a flat 4,000 characters instead of being
# bounded at the next note the way every other section here is.
ENDS_CLEANLY = re.compile(r'[.!?;:"”’)\]…]\s*$')


def test_the_going_concern_note_stops_at_the_next_note():
    """Churchill Capital: the quote ran out of the going-concern note, through
    the next heading, and stopped inside "prepare"."""
    text = ("Note 1. Going Concern\n"
            + "The Company has incurred losses and conditions raise "
              "substantial doubt about its ability to continue as a going "
              "concern. " * 6
            + "\nNote 2. Summary of Significant Accounting Policies\n"
            + "The accompanying unaudited condensed consolidated financial "
              "statements have been prepared in accordance with GAAP. " * 20)
    span = sections.find_going_concern_note(text)
    assert span is not None
    body = text[span[0]:span[1]]
    assert "substantial doubt" in body
    assert "Summary of Significant Accounting Policies" not in body


def test_a_quote_never_ends_inside_a_word():
    """A note with no next-note heading is cut by the character budget, which
    lands wherever it lands. It must still end on a whole word."""
    text = ("Going Concern\n"
            + "The Company reported recurring losses and negative cash flows "
              "which raise substantial doubt about its ability to continue as "
              "a going concern for one year. " * 60)
    span = sections.find_going_concern_note(text)
    assert span is not None
    body = text[span[0]:span[1]]
    assert body[-1].isalnum() or body[-1] in ".;:"
    # The cut fell inside the source, so the word it landed in is gone.
    assert text[span[1]] in " \n" or text[span[1]].isalnum() is False


def test_a_trimmed_excerpt_says_so():
    """_context marks its own truncation. The old guard only did that when a
    space fell within the last 60 characters, so a window ending inside a long
    unbroken token was published truncated and unmarked."""
    body = ("Conditions raise substantial doubt about the Company. "
            + "x" * 200 + " tail " + "and further text " * 200)
    ctx = sections._context(body, 0)
    assert ENDS_CLEANLY.search(ctx), repr(ctx[-40:])
    assert ctx.endswith("…")


def test_the_published_quotes_end_cleanly():
    """The property the site actually promises, asserted on the extractor's
    own output rather than on an intermediate."""
    text = ("2. Going Concern and Management's Plans\n"
            + "The Company has an accumulated deficit and expects to require "
              "additional financing, which raises substantial doubt about its "
              "ability to continue as a going concern. " * 12)
    state = sections.going_concern_state(text)
    assert state["quote"]
    assert ENDS_CLEANLY.search(state["quote"]), repr(state["quote"][-40:])


# --- and the fourth path: a staff comment letter -----------------------------
def test_a_letter_quote_stops_before_the_sign_off():
    """Every staff letter ends with who to call and a signature block. One
    published quote reached the page as "...related matters. September 16, 2025
    Page 2 Sincerely, Division of Corporation Finance Office of Life Sciences"
    - the SEC's switchboard, quoted as evidence about a company."""
    from pipeline import letters

    text = ("UPLOAD 1 filename1.htm We have reviewed your filings. "
            "We note the disclosure of your revenue recognition policy and "
            "would like to understand how it applies to your bundled "
            "arrangements in each period presented. "
            "Please revise your future filings accordingly. "
            "Please contact Jenn Do at 202-551-3743 or Kevin Vaughn at "
            "202-551-3494. Sincerely, Division of Corporation Finance")
    quote = letters._first_comment(text)
    assert quote
    for furniture in ("Please contact", "Sincerely", "Corporation Finance",
                      "202-551"):
        assert furniture not in quote, f"{furniture!r} was published as evidence"


def test_a_page_break_does_not_land_inside_the_quote():
    """A letter is paginated and the break falls mid-sentence, so the footer
    was published inside it: "...from the measure and why 2. January 21, 2026
    Page 2 management believes...". Removing it rejoins the sentence."""
    from pipeline import letters

    text = ("UPLOAD 1 filename1.htm We note that your adjusted measure removes "
            "the tax valuation allowance and we would like to understand why. "
            "January 21, 2026 Page 2 Management believes the adjustment is "
            "consistent with the prior periods presented in the filing.")
    quote = letters._first_comment(text)
    assert "Page 2" not in quote
    assert "January 21, 2026" not in quote
    assert "understand why. Management believes" in quote


def test_a_letter_quote_ends_the_way_every_other_quote_does():
    from pipeline import letters

    text = ("UPLOAD 1 filename1.htm We note " + "your disclosure of the "
            "arrangement and the basis for it " * 30)
    quote = letters._first_comment(text)
    assert ENDS_CLEANLY.search(quote), repr(quote[-40:])


def test_a_letter_with_nothing_but_a_sign_off_publishes_no_quote():
    """ADMA Biologics: the only phrase the anchor found was the last comment's
    closing line, and everything after it was the SEC's switchboard. Cutting
    left 46 characters, so the old guard kept all 275 instead. The module's own
    rule applies - furniture that looks like evidence is worse than none."""
    from pipeline import letters

    text = ("UPLOAD 1 filename1.htm We have reviewed your filings. "
            "Please revise your future filings accordingly. "
            "Please contact Jenn Do at 202-551-3743 or Kevin Vaughn at "
            "202-551-3494 if you have questions. Sincerely, "
            "Division of Corporation Finance Office of Life Sciences")
    assert letters._first_comment(text) == ""
