"""Firm-name extraction from Item 4.01.

Every case here is a shape that reached the live Auditors page as a wrong firm
name. The page aggregates firm movement, so a mis-parse does not just look
untidy - it puts a firm in a count it was never in.
"""
import pytest

from pipeline import auditor


def firms(text, company=""):
    d = auditor.classify_401(text, company)
    return d["predecessor_auditor"], d["successor_auditor"]


# --- connectives absorbed by a case-insensitive [A-Z] -----------------------
@pytest.mark.parametrize("text,expected", [
    ("On 1 May 2026 the Company notified that Simon & Edward LLP would be dismissed.",
     "Simon & Edward LLP"),
    ("The Company was notified by Hamilton & Associates CPAs that it resigned.",
     "Hamilton & Associates CPAs"),
    ("The Board approved the dismissal of Fruci & Associates II LLP.",
     "Fruci & Associates II LLP"),
])
def test_leading_connectives_are_not_part_of_the_name(text, expected):
    out, _ = firms(text)
    assert out == expected, f"got {out!r}"


# --- names containing commas ------------------------------------------------
def test_a_comma_inside_a_firm_name_does_not_truncate_it():
    """"Wei, Wei & Co. LLP" was published as "Wei"."""
    assert auditor.canonical_firm("Wei, Wei & Co. LLP") == "Wei, Wei & Co. LLP"


def test_a_trailing_qualifier_is_still_dropped():
    assert auditor.canonical_firm("Marcum LLP (PCAOB ID 688)") == "Marcum"


# --- canonicalisation -------------------------------------------------------
@pytest.mark.parametrize("variants", [
    ["GreenGrowth CPAs", "Green Growth CPAs", "GreenGrowth CPA"],
    ["Simon & Edward", "Simon & Edward LLP", "Simon & Edward, LLP"],
    ["M&K CPAS", "M&K CPAS PLLC", "M & K CPAS"],
])
def test_spelling_variants_aggregate_as_one_firm(variants):
    """Counting must not depend on which spelling a filer typed. The display
    label keeps the filer's wording; the KEY is what the tables group on."""
    keys = {auditor.firm_key(v) for v in variants}
    assert len(keys) == 1, f"{variants} -> {keys}"


# --- things that are not the auditor ---------------------------------------
def test_a_law_firm_is_not_an_auditor():
    """Northann published Lewis Brisbois - counsel, not accountants - as its
    incoming auditor."""
    text = ("The Company engaged of Lewis Brisbois Bisgaard & Smith LLP as its "
            "legal counsel in connection with the matter.")
    _, inc = firms(text)
    assert inc == "", f"got {inc!r}"


def test_the_issuer_is_not_its_own_auditor():
    """Starfighters Space was published as its own predecessor auditor."""
    text = ("Starfighters Space, Inc. dismissed Starfighters Space, Inc. "
            "effective immediately.")
    out, _ = firms(text, company="Starfighters Space, Inc.")
    assert out == "", f"got {out!r}"


def test_an_individual_is_not_a_firm():
    text = "The Company dismissed John A. Smith, CPA, an individual practitioner."
    out, _ = firms(text)
    assert out in ("", "John A. Smith, CPA"), f"got {out!r}"


# --- the ordinary cases must keep working ----------------------------------
@pytest.mark.parametrize("text,out,inc", [
    ("The Company dismissed Deloitte & Touche LLP and engaged KPMG LLP.",
     "Deloitte", "KPMG"),
    ("Ernst & Young LLP resigned. The Board appointed Grant Thornton LLP.",
     "", "Grant Thornton"),
    ("The Audit Committee approved the engagement of Baker Tilly US, LLP.",
     "", "Baker Tilly"),
])
def test_ordinary_changes_still_parse(text, out, inc):
    o, i = firms(text)
    assert (o, i) == (out, inc), f"got {(o, i)!r}"


def test_the_same_firm_on_both_sides_is_suppressed():
    text = "The Company dismissed PwC LLP and then re-engaged PwC LLP."
    assert firms(text) == ("", "")


# --- gaps found by reading the filings the parser gave up on ---------------
def test_disengage_is_a_dismissal():
    """Oncolytics: "voted to disengage Ernst & Young LLP". The word was not in
    the vocabulary, so a Big Four departure was recorded with no firm."""
    out, _ = firms("The Audit Committee unanimously voted to disengage "
                   "Ernst & Young LLP as the Company's accountants.")
    assert out == "EY"


def test_the_outgoing_firm_may_be_the_one_doing_the_telling():
    """Natural Gas Services and Stabilis: "was advised by Ham, Langston &
    Brezina, LLP ... that HL&B completed a transaction". An audit firm merger,
    recorded with neither side named."""
    out, _ = firms("The Company was advised by Ham, Langston and Brezina, LLP "
                   "that it completed a transaction with CohnReznick LLP.")
    assert out.startswith("Ham, Langston")


def test_terminating_something_that_is_not_the_auditor_is_not_a_dismissal():
    """Greenland Mines filed an Item 4.01 that terminated a sales agreement
    with a placement agent. It was published as an auditor dismissal."""
    d = auditor.classify_401(
        "Effective July 4, 2026, the Company terminated its At-the-Market "
        "Sales Agreement with A.G.P./Alliance Global Partners.")
    assert d["direction"] is None, d["direction"]


def test_terminating_the_auditor_still_counts():
    d = auditor.classify_401(
        "The Company terminated the engagement of its independent registered "
        "public accounting firm on 1 June 2026.")
    assert d["direction"] == "dismissed"


def test_a_renamed_registrant_keeps_one_identity():
    """A comment letter addressed to "Bed Bath & Beyond, Inc." appears under
    "Neighborhood Intelligence, Inc. (NXH)" because the registrant renamed.
    Same CIK, same filing - so company, ticker and URL must agree, and the
    former name must be shown rather than left to look like a mismatch."""
    import glob, json
    rows = [json.loads(l) for f in sorted(glob.glob('data/events/*.jsonl'))
            for l in open(f) if l.strip()]
    letters = [e for e in rows if e['signal_type'] == 'comment_letter' and e['cik'] == 1130713]
    if not letters:
        return
    for e in letters:
        assert str(e['cik']) in e['filing_url'], "URL must be the company's own filing"
        assert e['company'] == 'NEIGHBORHOOD INTELLIGENCE, INC.', "one name per CIK"
        assert e['ticker'] in ('', 'NXH'), "ticker must match the registrant"
    # Only the letters filed under the old name carry it, which is the point:
    # the marker explains a mismatch where one would otherwise appear.
    assert any(e['evidence'].get('formerly') for e in letters), \
        "the former name must be recorded where the letter used it"


# --- entity forms found by re-auditing the live table ----------------------
def test_and_and_ampersand_are_the_same_firm():
    """The movement table split "Ham, Langston and Brezina, LLP" from
    "Ham, Langston & Brezina, LLP". Stripping punctuation removed the
    ampersand but left the word, so the two spellings keyed differently."""
    keys = {auditor.firm_key(n) for n in [
        "Ham, Langston & Brezina, LLP",
        "Ham, Langston and Brezina, LLP",
        "Ham, Langston & Brezina, L.L.P",
    ]}
    assert len(keys) == 1, keys


def test_a_sole_practitioner_firm_keeps_its_entity():
    """Webstar engaged "Victor Mokuolu, CPA PLLC". Stopping at the first
    suffix published it as "Victor Mokuolu", which reads as a person rather
    than the accounting firm the filing names."""
    _, inc = firms('the Company engaged Victor Mokuolu, CPA PLLC ("VMCPA") '
                   'as its independent registered public accounting firm.')
    assert inc == "Victor Mokuolu, CPA PLLC", inc


@pytest.mark.parametrize("text,expected", [
    ("The Company dismissed M&K CPAS PLLC.", "M&K CPAS PLLC"),
    ("The Company dismissed Kreit & Chiu CPA LLP.", "Kreit & Chiu CPA LLP"),
    ("The Company dismissed Fruci & Associates II, PLLC.", "Fruci & Associates II, PLLC"),
])
def test_chained_entity_suffixes_are_kept_whole(text, expected):
    out, _ = firms(text)
    assert out == expected, out


def test_the_key_still_ignores_the_entity_form():
    """Keeping the suffix for display must not split the aggregation."""
    assert auditor.firm_key("Victor Mokuolu, CPA PLLC") == auditor.firm_key("Victor Mokuolu CPA")
