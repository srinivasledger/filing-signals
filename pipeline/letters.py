"""SEC staff comment letters (UPLOAD) and company responses (CORRESP).

The staff writing to a company to question its accounting is published on
EDGAR after a delay of roughly twenty business days, and nobody aggregates it
by topic. This is the closest thing on EDGAR to a regulator saying "we think
this is wrong".

Three things had to be established before this could work:

  * **UPLOAD documents are PDFs**, not HTML. The HTML path returns raw PDF
    bytes as gibberish, so they are extracted with pypdf. CORRESP is HTML.
  * **Most letters are not about accounting.** Of thirty sampled, seventeen
    reviewed registration statements, four were tender offers or mergers, and
    only four reviewed a periodic report. Only the last group is the signal;
    the rest is IPO plumbing.
  * **Substring matching is unusable here.** "lease" matched every letter in
    the first sample, because "please" contains it. Every topic pattern is
    word-bounded.
"""
from __future__ import annotations

import io
import logging
import re
from typing import Dict, List, Optional

from . import compare, config, fetch
from .models import COMMENT_LETTER, DERIVED, SIGNAL_BLURBS, Event

log = logging.getLogger(__name__)

STAFF_LETTER, COMPANY_RESPONSE = "UPLOAD", "CORRESP"
LETTER_FORMS = {STAFF_LETTER, COMPANY_RESPONSE}

# The "Re:" line names what was reviewed. Only periodic reports carry the
# accounting review; registration statements are a different activity.
_RE_LINE = re.compile(r"\bRe:\s*(.{0,220})", re.S)
_PERIODIC_REVIEW = re.compile(
    r"Form\s+10-K|Form\s+10-Q|Form\s+20-F|Form\s+40-F"
    r"|Annual\s+Report|Quarterly\s+Report", re.I)
_REGISTRATION = re.compile(
    r"Registration\s+Statement|Form\s+S-\d|Form\s+F-\d|Form\s+N-\d"
    r"|Schedule\s+(?:13E|TO|14[A-Z])|Preliminary\s+Prox", re.I)

# Routine correspondence that is not a response to accounting comments.
_ROUTINE_CORRESP = re.compile(
    r"acceleration\s+request|Rule\s+461|request(?:s|ing)?\s+that\s+the\s+"
    r"effectiveness|withdraw\w*\s+(?:the\s+)?registration", re.I)

# A staff letter says so explicitly; this separates it from covering notes.
_HAS_COMMENTS = re.compile(
    r"we\s+have\s+reviewed\s+your\s+filing|following\s+comment|our\s+comments?\s+"
    r"(?:are|is)\s+set\s+forth|please\s+respond\s+to\s+this\s+letter", re.I)
_RESPONDS_TO_COMMENTS = re.compile(
    r"staff'?s?\s+comment|comment\s+letter|your\s+letter\s+dated"
    r"|in\s+response\s+to\s+(?:the\s+)?comment", re.I)

# Topic taxonomy. Word-bounded throughout - see the module docstring.
TOPICS: Dict[str, "re.Pattern"] = {
    "Revenue recognition": re.compile(
        r"\brevenue\s+recognition\b|\bASC\s*606\b|\bTopic\s*606\b"
        r"|\bperformance\s+obligation", re.I),
    "Non-GAAP measures": re.compile(
        r"\bnon-?GAAP\b|\badjusted\s+EBITDA\b|\bRegulation\s+G\b"
        r"|\bItem\s+10\(e\)", re.I),
    "Segment reporting": re.compile(
        r"\bsegment\s+(?:report|disclosur|informat)|\bASC\s*280\b"
        r"|\breportable\s+segment|\bchief\s+operating\s+decision\s+maker\b", re.I),
    "Goodwill and impairment": re.compile(
        r"\bgoodwill\b|\bimpairment\b|\bASC\s*350\b|\bASC\s*360\b", re.I),
    "Business combinations": re.compile(
        r"\bbusiness\s+combination|\bASC\s*805\b|\bpurchase\s+price\s+allocation\b"
        r"|\breverse\s+(?:merger|recapitali)", re.I),
    "Internal control": re.compile(
        r"\bmaterial\s+weakness\b|\binternal\s+control\s+over\s+financial\s+reporting\b"
        r"|\bICFR\b|\bdisclosure\s+controls\b|\bItem\s+9A\b", re.I),
    "Going concern": re.compile(r"\bgoing\s+concern\b|\bASC\s*205-40\b", re.I),
    "Income taxes": re.compile(r"\bincome\s+tax|\bvaluation\s+allowance\b|\bASC\s*740\b", re.I),
    "Fair value": re.compile(r"\bfair\s+value\b|\bASC\s*820\b|\bLevel\s+3\b", re.I),
    "Leases": re.compile(r"\bleases?\b|\blessee\b|\blessor\b|\bASC\s*842\b", re.I),
    "Share-based compensation": re.compile(
        r"\bshare-?based\s+compensation\b|\bstock-?based\s+compensation\b|\bASC\s*718\b", re.I),
    "Crypto assets": re.compile(r"\bcrypto\s*(?:currenc|asset)|\bdigital\s+asset|\bbitcoin\b", re.I),
    "Inventory": re.compile(r"\binventor(?:y|ies)\b|\bASC\s*330\b", re.I),
    "MD&A": re.compile(
        r"\bManagement'?s?\s+Discussion\s+and\s+Analysis\b|\bMD&A\b"
        r"|\bresults\s+of\s+operations\b", re.I),
}

# Where a comment points: "Note 2 ... Revenue Recognition, page 60".
_CITATION = re.compile(
    r"((?:Note\s+\d+[^,;]{0,80}|Item\s+\d+[A-Z]?\.?[^,;]{0,60}))\s*,?\s*page\s+(\d+)", re.I)

MIN_TEXT = 400


def _pdf_text(url: str) -> str:
    """Extract a PDF comment letter. Imported lazily so the dependency stays
    optional for anyone not enabling this signal."""
    from pypdf import PdfReader

    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": config.require_user_agent()})
    raw = urllib.request.urlopen(req, timeout=config.SEC_TIMEOUT).read()
    reader = PdfReader(io.BytesIO(raw))
    return " ".join(" ".join((p.extract_text() or "") for p in reader.pages).split())


def load_letter(url: str) -> Optional[str]:
    try:
        if url.lower().endswith(".pdf"):
            return _pdf_text(url)
        text = compare.load_text(url)
        return " ".join(text.split()) if text else None
    except fetch.SECBlocked:
        raise
    except Exception as exc:                     # noqa: BLE001
        log.warning("could not read letter %s: %s", url, exc)
        return None


def subject_line(text: str) -> str:
    m = _RE_LINE.search(text)
    return " ".join(m.group(1).split()) if m else ""


def reviews_a_periodic_report(subject: str) -> bool:
    """True only for letters about an annual or quarterly report."""
    if not subject:
        return False
    if _REGISTRATION.search(subject) and not _PERIODIC_REVIEW.search(subject):
        return False
    return bool(_PERIODIC_REVIEW.search(subject))


def classify_topics(text: str) -> List[str]:
    return [name for name, pat in TOPICS.items() if pat.search(text)]


def citations(text: str, limit: int = 4) -> List[str]:
    out, seen = [], set()
    for m in _CITATION.finditer(text):
        label = " ".join(m.group(1).split())
        if len(label) < 4 or label.lower() in seen:
            continue
        seen.add(label.lower())
        out.append(f"{label}, page {m.group(2)}")
        if len(out) >= limit:
            break
    return out


def analyse_letter(filing) -> List[Event]:
    """Build an event from a staff comment letter or a company response."""
    form = filing.form.upper()
    url = compare.current_document(filing.cik, filing.accession)
    if not url:
        return []
    text = load_letter(url)
    if not text or len(text) < MIN_TEXT:
        return []

    subject = subject_line(text)
    if not reviews_a_periodic_report(subject):
        return []

    is_staff = form == STAFF_LETTER
    if is_staff and not _HAS_COMMENTS.search(text):
        return []
    if not is_staff:
        if _ROUTINE_CORRESP.search(text) or not _RESPONDS_TO_COMMENTS.search(text):
            return []

    topics = classify_topics(text)
    if not topics:
        return []

    ticker = ""
    sub = compare.submissions(filing.cik)
    if sub and sub.get("tickers"):
        ticker = sub["tickers"][0]

    headline = (
        f"SEC staff questioned {filing.company}'s accounting for "
        f"{topics[0].lower()}" if is_staff else
        f"{filing.company} responded to SEC staff comments on {topics[0].lower()}")

    return [Event(
        signal_type=COMMENT_LETTER,
        confidence=DERIVED,
        company=filing.company,
        cik=filing.cik,
        ticker=ticker,
        form=filing.form,
        filed=filing.filed,
        accession=filing.accession,
        filing_url=filing.index_url,
        headline=headline,
        sic_desc=filing.sic_desc,
        evidence={
            "source": ("SEC staff comment letter (UPLOAD)" if is_staff
                       else "Company response to SEC staff (CORRESP)"),
            "direction": "staff to company" if is_staff else "company to staff",
            "reviewing": subject[:160],
            "topics": topics,
            "cited_sections": citations(text),
            "why": SIGNAL_BLURBS[COMMENT_LETTER],
        },
        quote=_first_comment(text),
        beta=True,
    )]


def _first_comment(text: str) -> str:
    """The opening of the first substantive comment, for quotation."""
    from . import sections

    anchor = re.search(r"We\s+note\s+|We\s+have\s+reviewed|Please\s+(?:tell|revise|explain)", text)
    if not anchor:
        return sections.truncate_words(text[:600], 420)
    return sections.truncate_words(text[anchor.start(): anchor.start() + 700], 420)
