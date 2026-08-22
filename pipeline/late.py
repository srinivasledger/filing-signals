"""Late-filing notifications (Form 12b-25, filed as NT 10-K / NT 10-Q / NT 20-F).

A company that cannot file on time is often the earliest public signal of
trouble, and it usually *precedes* a restatement rather than following it.

The form is unusually machine-readable. Beyond the fact of lateness it asks two
questions whose answers are rendered as literal checkboxes:

  Part III  - the narrative reason the report could not be filed on time.
  Part IV(3) - whether a significant change in results of operations is
               anticipated. A "Yes" here is the company pre-announcing that the
               late numbers will look materially different.

Parsing the checkbox turns a routine administrative filing into a graded
signal.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

from . import compare, sections
from .models import DERIVED, LATE_FILING, SIGNAL_BLURBS, Event

log = logging.getLogger(__name__)

# NT filings for periodic reports. Fund equivalents (NT NPORT-P, NT N-CEN) are
# deliberately absent - they carry no operating-company signal.
LATE_FORMS = {
    "NT 10-K", "NT 10-K/A", "NT 10-Q", "NT 10-Q/A",
    "NT 20-F", "NT 20-F/A", "NT 11-K",
}
ANNUAL_LATE = {"NT 10-K", "NT 10-K/A", "NT 20-F", "NT 20-F/A"}

# Filers use several glyphs, and - importantly - two different orders:
#   "Yes \u2610 No \u2612"   (box after the label)
#   "\u2610 Yes \u2612 No"   (box before the label)
# Handling only one order silently returned "unknown" for half the population.
_TICKED = "\u2612\u2611\u25fc\u25a0\u2327"        # x-box, check-box, filled squares
_EMPTY = "\u2610\u25fb\u25a1"                        # empty boxes
_BOX = _TICKED + _EMPTY

# "PART III" also appears mid-sentence as "the reason described in Part III of
# this form", inside Part II. Requiring a heading shape avoids matching that.
_PART3 = re.compile(r"PART\s+III\b(?!\s+of\b)", re.I)
_PART4 = re.compile(r"PART\s+IV\b(?!\s+of\b)", re.I)

_ANTICIPATED_Q = r"significant change in results of operations"
_OTHER_Q = r"Have all other periodic reports"

# Boilerplate that appears on every blank form and is never a real reason.
_BOILERPLATE = re.compile(
    r"State below in reasonable detail|If the subject report could not be filed"
    r"|could not be eliminated without unreasonable effort"
    r"|The subject annual report, semi-annual report|will be filed on or before"
    r"|Rule 12b-25|Check box if appropriate|attach an explanation|Attach extra [Ss]heets", re.I)


def _answer_near(text: str, question: str) -> Optional[bool]:
    """Read a Yes/No checkbox pair following a question, in either order."""
    q = re.search(question, text, re.I)
    if not q:
        return None
    window = text[q.end(): q.end() + 400]

    before = re.search(rf"([{_BOX}])\s*Yes\b.{{0,40}}?([{_BOX}])\s*No\b", window, re.S)
    after = re.search(rf"Yes\s*([{_BOX}]).{{0,40}}?No\s*([{_BOX}])", window, re.S)
    m = before or after
    if not m:
        return None

    yes_ticked = m.group(1) in _TICKED
    no_ticked = m.group(2) in _TICKED
    if yes_ticked == no_ticked:      # both or neither ticked - unusable
        return None
    return yes_ticked


def extract_reason(text: str) -> str:
    """The narrative from Part III, minus the form's own instructions."""
    start = _PART3.search(text)
    if not start:
        return ""
    tail = text[start.end():]
    end = _PART4.search(tail)
    body = tail[:end.start()] if end else tail[:4000]

    # Collapse newlines first. html_to_text preserves single line breaks, so
    # the form's instruction text arrives as "State below\nin reasonable\ndetail"
    # and every boilerplate pattern silently fails to match across the breaks.
    body = " ".join(body.split())
    body = re.sub(rf"[{_BOX}]", " ", body)
    body = re.sub(r"\(\s*[a-d]\s*\)", " ", body)
    sentences = [x.strip() for x in re.split(r"(?<=[.;])\s+", body) if x.strip()]
    kept = [x for x in sentences if len(x) > 45 and not _BOILERPLATE.search(x)]
    return " ".join(" ".join(kept).split())[:700]


def parse_form(text: str) -> Dict:
    return {
        "reason": extract_reason(text),
        "anticipates_significant_change": _answer_near(text, _ANTICIPATED_Q),
        "other_reports_filed": _answer_near(text, _OTHER_Q),
    }


def _severity(form: str, parsed: Dict) -> str:
    if parsed.get("anticipates_significant_change") is True:
        return "high"
    if form.upper() in ANNUAL_LATE:
        return "high"
    if parsed.get("other_reports_filed") is False:
        return "high"
    return "normal"


def _headline(company: str, form: str, parsed: Dict) -> str:
    report = "annual report" if form.upper() in ANNUAL_LATE else "quarterly report"
    if parsed.get("anticipates_significant_change") is True:
        return (f"{company} filed its {report} late and said it expects a "
                "significant change in results")
    return f"{company} told the SEC it could not file its {report} on time"


def analyse_late_filing(filing) -> List[Event]:
    """Build a late-filing event from a Form 12b-25."""
    url = compare.current_document(filing.cik, filing.accession)
    if not url:
        return []
    raw = compare.load_text(url)
    if not raw:
        return []

    parsed = parse_form(raw)
    severity = _severity(filing.form, parsed)

    ticker = ""
    sub = compare.submissions(filing.cik)
    if sub and sub.get("tickers"):
        ticker = sub["tickers"][0]

    evidence = {
        "source": "Form 12b-25 (notification of late filing)",
        "severity": severity,
        "anticipates_significant_change": parsed["anticipates_significant_change"],
        "other_periodic_reports_filed": parsed["other_reports_filed"],
        "why": SIGNAL_BLURBS[LATE_FILING],
    }
    if parsed["reason"]:
        evidence["stated_reason"] = parsed["reason"]

    return [Event(
        signal_type=LATE_FILING,
        confidence=DERIVED,
        company=filing.company,
        cik=filing.cik,
        ticker=ticker,
        form=filing.form,
        filed=filing.filed,
        accession=filing.accession,
        filing_url=filing.index_url,
        headline=_headline(filing.company, filing.form, parsed),
        sic_desc=filing.sic_desc,
        evidence=evidence,
        quote=parsed["reason"],
    )]


def merge_same_day(events: List[Event]) -> List[Event]:
    """Collapse multiple late notices from one company on one day into one.

    A company catching up on overdue filings files several Form 12b-25s at
    once - one issuer filed seven on a single day - and each was becoming its
    own card. That is one story told seven times. The most severe notice is
    kept and the rest are recorded on it.
    """
    groups: Dict[tuple, List[Event]] = {}
    passthrough: List[Event] = []
    for e in events:
        if e.signal_type != LATE_FILING:
            passthrough.append(e)
            continue
        groups.setdefault((e.cik, e.filed), []).append(e)

    merged: List[Event] = []
    for (_, _), group in groups.items():
        if len(group) == 1:
            merged.append(group[0])
            continue
        group.sort(key=lambda e: (e.evidence.get("severity") != "high", e.form))
        lead = group[0]
        others = group[1:]
        lead.evidence["filings_in_batch"] = len(group)
        lead.evidence["other_forms"] = sorted({e.form for e in others})
        lead.headline = (
            f"{lead.company} told the SEC it could not file {len(group)} "
            "periodic reports on time")
        merged.append(lead)

    return passthrough + merged
