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
    if end:
        body = tail[:end.start()]
    else:
        # No Part IV to stop at, so the cut is the character budget and lands
        # wherever it lands. The word it landed in is not a word.
        body = tail[:4000]
        if len(tail) > 4000:
            space = body.rfind(" ")
            if space > 0:
                body = body[:space]

    # Collapse newlines first. html_to_text preserves single line breaks, so
    # the form's instruction text arrives as "State below\nin reasonable\ndetail"
    # and every boilerplate pattern silently fails to match across the breaks.
    # Collapse newlines first. html_to_text preserves single line breaks, so
    # the form's instruction text arrives as "State below\nin reasonable\ndetail"
    # and every boilerplate pattern silently fails to match across the breaks.
    body = " ".join(body.split())
    body = re.sub(rf"[{_BOX}]", " ", body)
    body = re.sub(r"\(\s*[a-d]\s*\)", " ", body)
    # The Part III heading reads "PART III - NARRATIVE"; the dash and the word
    # survive the heading match and were being quoted as part of the reason.
    body = re.sub(r"^\s*[\u2013\u2014-]?\s*NARRATIVE\b[:\s-]*", " ", body, flags=re.I)

    pieces = [x.strip() for x in re.split(r"(?<=[.;])\s+", body) if x.strip()]
    # Company names end in abbreviations, so a naive split cuts
    # "GridAI Technologies Corp. is unable to file" in two and leaves a
    # fragment starting with a verb. Anything not starting like a sentence
    # belongs to the piece before it.
    sentences = []
    for piece in pieces:
        if sentences and not re.match(r'[A-Z\u201c("]', piece):
            sentences[-1] = sentences[-1] + " " + piece
        else:
            sentences.append(piece)

    kept = [x for x in sentences if len(x) > 45 and not _BOILERPLATE.search(x)]
    # "PART III" also occurs mid-sentence ("the disclosure in Part III is framed
    # around..."), so the body can genuinely begin part-way through a sentence.
    # A leading fragment has nothing to attach to; drop it rather than quote it.
    while kept and not re.match(r'[A-Z\u201c("]', kept[0]):
        kept.pop(0)
    return sections.close_quote(sections.truncate_words(" ".join(kept), 700))


# EDGAR's <PERIOD> header is not the report period on an NT filing: for some
# filers it carries the notification date instead (PLAYSTUDIOS' NT 10-Q reports
# 2026-08-11, its own filing date), which produced due dates in the future and
# "days past due" values as low as -45. The form states the period itself, so
# read it from the document and treat the header as a fallback.
_PERIOD_ON_FORM = re.compile(
    r"[Ff]or\s+(?:the\s+)?[Pp]eriod\s+[Ee]nded[:\s]*"
    r"([A-Z][a-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})")

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)}


def period_on_form(text: str) -> str:
    """The period end the notice itself states, as YYYY-MM-DD, or ""."""
    import datetime as _dt
    m = _PERIOD_ON_FORM.search(text)
    if not m:
        return ""
    raw = m.group(1).strip().rstrip(",")
    try:
        if "-" in raw:
            return _dt.date.fromisoformat(raw).isoformat()
        if "/" in raw:
            a, b, c = (int(x) for x in raw.split("/"))
            c += 2000 if c < 100 else 0
            return _dt.date(c, a, b).isoformat()
        month, day, year = raw.replace(",", "").split()
        return _dt.date(int(year), _MONTHS[month.lower()], int(day)).isoformat()
    except (ValueError, KeyError):
        return ""


def parse_form(text: str) -> Dict:
    return {
        "period_on_form": period_on_form(text),
        "reason": extract_reason(text),
        "anticipates_significant_change": _answer_near(text, _ANTICIPATED_Q),
        "other_reports_filed": _answer_near(text, _OTHER_Q),
    }


# Statutory windows after the period end, by SEC filer category (Rule 12b-25
# extends these, but the original due date is what makes a notice routine).
_DUE_DAYS = {
    "quarterly": {"large": 40, "mid": 40, "small": 45},
    "annual": {"large": 60, "mid": 75, "small": 90},
}

# Substantive reasons. A notice saying the accounts need more time is routine;
# one disclosing an error, a restatement or a departure is not. Cambium Networks
# and Infleqtion were both "high" on checkbox fields alone, though one recited
# boilerplate and the other disclosed an identified revenue-recognition error.
_SUBSTANTIVE_REASON = re.compile(
    r"\brestat\w+|\berror(?:s)?\b|material\s+weakness|significant\s+deficienc"
    r"|non-?reliance|resign\w+|dismiss\w+|terminat\w+"
    r"|internal\s+(?:review|investigation)|independent\s+(?:review|investigation)"
    r"|audit\s+committee\s+(?:review|investigation)|forensic"
    r"|bankrupt\w+|chapter\s+(?:7|11)|delist\w+|going\s+concern"
    r"|revenue\s+recognition|impairment|fraud|misstat\w+"
    r"|departure|resignation\s+of|chief\s+(?:executive|financial)",
    re.I,
)


def deadline_for(period_end, form: str, tier: str):
    """The original statutory due date, or None when it cannot be determined."""
    import datetime as _dt
    if not period_end:
        return None
    kind = "annual" if form.upper() in ANNUAL_LATE else "quarterly"
    bucket = {"mega": "large", "large": "large", "mid": "mid"}.get(tier, "small")
    try:
        end = _dt.date.fromisoformat(period_end)
    except (TypeError, ValueError):
        return None
    return end + _dt.timedelta(days=_DUE_DAYS[kind][bucket])


# How overdue a report has to be before lateness alone decides the grade.
_VERY_OVERDUE_DAYS = 90
_OVERDUE_DAYS = 30


def _severity(form: str, parsed: Dict, days_late: Optional[int] = None) -> str:
    """Grade the notice.

    Content first, then how late the report actually is. Grading on content
    alone left a 730-day delinquency at "normal" while a four-day slip that
    mentioned an internal review was "high" - the tier was uncorrelated with
    the thing a reader cares about, and in the worst cases inverted.
    """
    reason = parsed.get("reason") or ""

    # Content decides "high". The checkboxes are inputs, not the rule: most
    # filers tick "significant change" - it was True for Cambium's boilerplate
    # notice and Infleqtion's disclosed revenue-recognition error alike - so on
    # its own it separates nothing and leaves the field carrying no information.
    if _SUBSTANTIVE_REASON.search(reason):
        return "high"

    # A report months past its statutory date is a serious fact on its own,
    # whatever the notice says about why.
    if days_late is not None and days_late >= _VERY_OVERDUE_DAYS:
        return "high"

    if (days_late is not None and days_late >= _OVERDUE_DAYS
            or parsed.get("anticipates_significant_change") is True
            or parsed.get("other_reports_filed") is False
            or form.upper() in ANNUAL_LATE):
        return "elevated"
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

    ticker = ""
    sub = compare.submissions(filing.cik)
    if sub and sub.get("tickers"):
        ticker = sub["tickers"][0]

    # The form's own statement of the period beats the SGML header, which on an
    # NT filing sometimes carries the notification date. A period that is not
    # comfortably before the filing date cannot be the one being reported late,
    # so it is discarded rather than used to compute a due date in the future.
    import datetime as _dt
    period = parsed.get("period_on_form") or getattr(filing, "period", "")
    if period:
        try:
            if (_dt.date.fromisoformat(filing.filed)
                    - _dt.date.fromisoformat(period)).days < 20:
                period = ""
        except (TypeError, ValueError):
            period = ""
    due = deadline_for(period, filing.form,
                       getattr(filing, "size_tier", "") or "small")
    days_late = None
    if due:
        try:
            days_late = (_dt.date.fromisoformat(filing.filed) - due).days
        except (TypeError, ValueError):
            days_late = None

    severity = _severity(filing.form, parsed, days_late)

    # A notice filed on or around the statutory due date is the filing
    # calendar, not distress: 101 of 251 events landed on 14 August 2026, the
    # 10-Q deadline for the June quarter. One filed well past the date, or by a
    # company that was late last period too, is the actual signal.
    routine = (severity == "normal"
               and days_late is not None and days_late <= 3)

    evidence = {
        "source": "Form 12b-25 (notification of late filing)",
        "severity": severity,
        "routine": routine,
        "anticipates_significant_change": parsed["anticipates_significant_change"],
        "other_periodic_reports_filed": parsed["other_reports_filed"],
        "why": SIGNAL_BLURBS[LATE_FILING],
    }
    if days_late is not None:
        evidence["days_past_due_date"] = days_late
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
        routine=routine,
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
