"""Turn a filing document into text, and isolate the parts that mean something.

The central problem this module solves: the phrase "going concern" appears in
almost every small-cap filing's Risk Factors as standing boilerplate, year
after year, with no accounting conclusion behind it. Searching a whole
document for the phrase produces a false positive on essentially every
speculative issuer.

Verified case: Cyclerion Therapeutics 10-Q (2026-08-04) contains 17 matches
for "going concern"; its prior 10-Q contains 16. The text that matches first
is a Risk Factors bullet. Nothing changed between the filings, so nothing
should be reported.

So we locate the Risk Factors span, exclude it, and only then look for the
ASC 205-40 language that constitutes an actual going-concern conclusion.
"""
from __future__ import annotations

import html
import re
from typing import Dict, List, Optional, Tuple

# --- going-concern state ladder ---------------------------------------------
GC_NONE = "none"
GC_RISK_FACTOR_ONLY = "risk_factor_only"
GC_DOUBT_ALLEVIATED = "doubt_alleviated"
GC_SUBSTANTIAL_DOUBT = "substantial_doubt"

# Ordered from least to most severe. An event is only newsworthy when a filing
# moves between rungs.
GC_LADDER = [GC_NONE, GC_RISK_FACTOR_ONLY, GC_DOUBT_ALLEVIATED, GC_SUBSTANTIAL_DOUBT]

# For *reporting*, "none" and "risk-factor only" are the same thing: the filing
# reached no going-concern conclusion. Moving between them means only that
# boilerplate appeared or moved, which is not an accounting event. Collapsing
# them removes a class of noise (e.g. Heron Therapeutics, Lumentum) without
# losing any real signal.
GC_NO_CONCLUSION = {GC_NONE, GC_RISK_FACTOR_ONLY}


def gc_bucket(state: str) -> str:
    return "no_conclusion" if state in GC_NO_CONCLUSION else state


GC_STATE_LABELS = {
    GC_NONE: "No going-concern disclosure",
    GC_RISK_FACTOR_ONLY: "Risk-factor language only",
    GC_DOUBT_ALLEVIATED: "Substantial doubt raised, alleviated by management's plans",
    GC_SUBSTANTIAL_DOUBT: "Substantial doubt about ability to continue as a going concern",
}

# ASC 205-40 conclusion language. Deliberately narrow: these phrasings signal
# an accounting conclusion, not a generic risk warning.
_GC_CONCLUSION = re.compile(
    r"(?:raise|raises|raised|indicate|indicates)\s+substantial\s+doubt"
    r"|substantial\s+doubt\s+(?:exists\s+)?about\s+(?:the\s+)?(?:compan|entit|its|our|their)"
    r"|substantial\s+doubt\s+about\s+(?:its|our|the)\s+ability\s+to\s+continue"
    r"|ability\s+to\s+continue\s+as\s+a\s+going\s+concern",
    re.I,
)

# Classification must key on the *conclusion sentence*, not on nearby words.
# Every ASC 205-40 note opens with methodology boilerplate that recites both
# outcomes ("...if it concludes substantial doubt exists and it is not
# alleviated by the Company's plans or when its plans alleviate substantial
# doubt..."), so proximity matching returns whichever word happens to be
# closer. Verified against Cyclerion, whose note contains "Management's plans
# to alleviate the conditions" and then concludes the opposite way.
_GC_CONCLUDES_DOUBT = re.compile(
    r"conclude[ds]?\s+(?:that\s+)?substantial\s+doubt\s+exists"
    r"|substantial\s+doubt\s+exists\s+about"
    r"|there\s+is\s+substantial\s+doubt\s+about"
    r"|substantial\s+doubt[^.]{0,140}?(?:has\s+)?not\s+been\s+alleviated"
    r"|do(?:es)?\s+not\s+alleviate\s+(?:the\s+)?substantial\s+doubt"
    r"|(?:is|are)\s+not\s+sufficient\s+to\s+alleviate",
    re.I,
)

_GC_CONCLUDES_ALLEVIATED = re.compile(
    r"alleviate[sd]?\s+(?:the\s+)?substantial\s+doubt"
    r"|substantial\s+doubt[^.]{0,140}?(?:has|have)\s+been\s+alleviated"
    r"|no\s+longer\s+(?:any\s+)?substantial\s+doubt"
    r"|substantial\s+doubt[^.]{0,80}?(?:is|was)\s+alleviated",
    re.I,
)


def _last(pattern: "re.Pattern", text: str) -> int:
    """Position of the final match, or -1. Conclusions come last in a note, so
    when both readings appear the later one is the operative one."""
    pos = -1
    for m in pattern.finditer(text):
        pos = m.start()
    return pos


def classify_going_concern(note: str) -> str:
    """Map a going-concern note onto the ladder using its conclusion."""
    doubt = _last(_GC_CONCLUDES_DOUBT, note)
    alleviated = _last(_GC_CONCLUDES_ALLEVIATED, note)

    if doubt >= 0 and doubt > alleviated:
        return GC_SUBSTANTIAL_DOUBT
    if alleviated >= 0 and alleviated > doubt:
        return GC_DOUBT_ALLEVIATED
    if doubt >= 0:
        return GC_SUBSTANTIAL_DOUBT
    # Doubt was raised but the filing states no explicit resolution. Reporting
    # the more severe reading would overstate; ASC 205-40 requires an explicit
    # alleviation statement, so its absence means doubt stands.
    return GC_SUBSTANTIAL_DOUBT


# --- policy sections ---------------------------------------------------------
_POLICY_HEADING = re.compile(
    r"(?:summary\s+of\s+)?significant\s+accounting\s+policies"
    r"|basis\s+of\s+presentation\s+and\s+significant\s+accounting\s+policies"
    r"|summary\s+of\s+accounting\s+policies",
    re.I,
)
_REVENUE_HEADING = re.compile(
    r"revenue\s+(?:from\s+contracts\s+with\s+customers|recognition)"
    r"|disaggregation\s+of\s+revenue",
    re.I,
)

# Regions that discuss revenue at length but contain no accounting policy.
# Without excluding them the extractor lifted MD&A performance commentary
# ("The increase in 2026 was due principally to higher professional fees") and
# the auditor's critical-audit-matter paragraph, and reported both as policy
# changes.
_MDA_HEADING = re.compile(r"Management[\u2019']s\s+Discussion\s+and\s+Analysis", re.I)
_AUDIT_REPORT_HEADING = re.compile(
    r"Report\s+of\s+Independent\s+Registered\s+Public\s+Accounting\s+Firm", re.I)

# A genuine ASC 606 policy note uses this vocabulary. Requiring several
# distinct markers separates a policy note from any passage mentioning revenue.
_ASC606_MARKERS = [
    re.compile(r"performance obligation", re.I),
    re.compile(r"transaction price", re.I),
    re.compile(r"(?:ASC|Topic)\s*606", re.I),
    re.compile(r"standalone selling price", re.I),
    re.compile(r"contracts?\s+with\s+customers?", re.I),
    re.compile(r"(?:recognize[sd]?|recognition of)\s+revenue", re.I),
    re.compile(r"control\s+(?:of|over)[^.]{0,80}transfer", re.I),
]
MIN_ASC606_MARKERS = 3


def _bounded_span(text: str, start: int, terminator: str, default: int):
    nxt = re.search(terminator, text[start:], re.I)
    return start + (nxt.start() if nxt else default)


def excluded_regions(text: str) -> List[Tuple[int, int]]:
    """Character ranges that must not supply an accounting-policy note."""
    spans: List[Tuple[int, int]] = []
    rf = find_risk_factor_span(text)
    if rf:
        spans.append(rf)
    for m in _MDA_HEADING.finditer(text):
        spans.append((m.start(), min(len(text),
                     _bounded_span(text, m.end(), r"\n\s*Item\s*\d", 80_000))))
    for m in _AUDIT_REPORT_HEADING.finditer(text):
        spans.append((m.start(), min(len(text), _bounded_span(
            text, m.end(),
            r"\n\s*(?:Note\s+\d|Item\s*\d|CONSOLIDATED\s+(?:BALANCE|STATEMENT))",
            25_000))))
    return spans


def _inside(pos: int, spans: List[Tuple[int, int]]) -> bool:
    return any(a <= pos < b for a, b in spans)


def _line_at(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start: end if end != -1 else len(text)].strip()


def _asc606_score(body: str) -> int:
    return sum(1 for pat in _ASC606_MARKERS if pat.search(body))

# Notes to financial statements are numbered, and the next number is where the
# current note ends. Without this the extractor runs past the policy note into
# income-tax and revenue tables, and the "change" it measures is just different
# dollar figures in a different note.
_NOTE_BOUNDARY = re.compile(
    r"^[ \t]*(?:NOTE|Note)\s+(?:\d{1,2}|[A-Z])\s*[.:\u2013\u2014-]"
    r"|^[ \t]*\d{1,2}\.\s+[A-Z][A-Za-z][^\n]{2,60}$",
    re.M,
)

_BLOCK_TAGS = re.compile(
    r"</?(?:p|div|tr|td|th|br|h[1-6]|li|table|section|article)\b[^>]*>", re.I
)


def html_to_text(raw: str) -> str:
    """Flatten a filing document to plain text.

    Filings are inline-XBRL HTML; we drop markup but keep block boundaries as
    newlines so headings stay detectable.
    """
    raw = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<!--.*?-->", " ", raw)
    raw = _BLOCK_TAGS.sub("\n", raw)
    # Inline tags collapse to nothing, not to a space. Filings routinely wrap
    # fragments of words in <span>/inline-XBRL tags, so substituting a space
    # splits words: a real 10-Q heading flattens to "Item 1A. Ri sk Factors".
    # That silently breaks every downstream text match.
    raw = re.sub(r"(?s)<[^>]+>", "", raw)
    text = html.unescape(raw)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def find_risk_factor_span(text: str) -> Optional[Tuple[int, int]]:
    """Locate the Risk Factors section so it can be excluded.

    Returns (start, end) or None. The end is the next "Item N" heading, or a
    bounded window if no such heading follows.
    """
    start_m = None
    for m in re.finditer(r"item\s*1A\.?\s*[-–—:]?\s*risk\s+factors", text, re.I):
        # Skip table-of-contents entries: those are followed almost
        # immediately by a page number and another "Item".
        tail = text[m.end(): m.end() + 200]
        if re.match(r"\s*\.{2,}|\s*\d{1,3}\s*\n", tail):
            continue
        start_m = m
        break
    if start_m is None:
        return None

    start = start_m.start()
    nxt = re.search(
        r"\nitem\s*(?:1B|2|3|4|5|6|7|7A|8)\b", text[start_m.end():], re.I
    )
    end = start_m.end() + nxt.start() if nxt else min(len(text), start + 250_000)
    return (start, end)


def strip_risk_factors(text: str) -> str:
    span = find_risk_factor_span(text)
    if not span:
        return text
    return text[:span[0]] + "\n" + text[span[1]:]


# A real going-concern conclusion lives under its own note/section heading
# ("Going Concern", "Liquidity and Going Concern", "Basis of Presentation and
# Going Concern"). Headings are short standalone lines; the boilerplate that
# causes false positives is always mid-sentence in a long paragraph.
_MAX_HEADING_LEN = 70

# Forward-looking-statements sections recite going-concern risk in prose and
# are excluded alongside Risk Factors on the fallback path.
_FLS_HEADING = re.compile(
    r"(?:cautionary|special)\s+note\s+regarding\s+forward[- ]looking"
    r"|forward[- ]looking\s+statements",
    re.I,
)


def _looks_like_heading(line: str) -> bool:
    """Distinguish a note heading from a fragment of running text.

    Length alone is not enough. A Flux Power 10-K broke on the bullet fragment
    "ability to continue as a going concern;" - 39 characters, so it passed a
    length test - while the real heading, "Doubt About the Company's Ability to
    Continue as a Going Concern", sat 286,000 characters further down.
    Headings do not begin in lower case and do not end in a comma or semicolon.
    """
    if not line or len(line) > _MAX_HEADING_LEN:
        return False
    if line[-1] in ",;:":
        return False
    if line[0].islower():
        return False
    return True


def find_going_concern_note(text: str) -> Optional[Tuple[int, int]]:
    """Locate the going-concern note by its heading. Returns (start, end)."""
    best: Optional[Tuple[int, int]] = None
    for m in re.finditer(r"^[^\n]*going\s+concern[^\n]*$", text, re.I | re.M):
        line = m.group(0).strip()
        if not _looks_like_heading(line):
            continue
        if re.match(r"\s*\.{2,}|\s*\d{1,3}\s*$", text[m.end(): m.end() + 40]):
            continue          # table-of-contents row
        body_end = min(len(text), m.end() + 4000)
        body = text[m.end(): body_end]
        if len(body) < 200:
            continue
        # Prefer the note that actually reaches an ASC 205-40 conclusion.
        if _GC_CONCLUSION.search(body) and best is None:
            best = (m.start(), body_end)
    return best


def _strip_forward_looking(text: str) -> str:
    m = _FLS_HEADING.search(text)
    if not m:
        return text
    end = m.end() + 12_000
    nxt = re.search(r"\n\s*(?:PART\s+I|Item\s*\d)", text[m.end():], re.I)
    if nxt:
        end = m.end() + nxt.start()
    return text[:m.start()] + "\n" + text[min(end, len(text)):]


def _context(text: str, pos: int, before: int = 400, after: int = 900) -> str:
    """A readable excerpt around a match.

    Slicing on raw offsets starts quotes mid-word ("'s results as the
    Company's..."), which looks broken on a page whose whole point is quoting
    filings accurately. Snap to a sentence boundary where one is nearby, and to
    a word boundary otherwise.
    """
    start = max(0, pos - before)
    end = min(len(text), pos + after)
    window = text[start:end]

    if start > 0:
        sentence = re.search(r"(?<=[.;])\s+[A-Z(\u201c\"]", window[:before])
        if sentence:
            window = window[sentence.end() - 1:]
        else:
            space = window.find(" ")
            if 0 <= space < 60:
                window = window[space + 1:]
    if end < len(text):
        cut = window.rfind(" ")
        if cut > len(window) - 60:
            window = window[:cut] + "\u2026"
    return window.strip()


def going_concern_state(text: str) -> Dict[str, object]:
    """Classify a filing onto the going-concern ladder.

    Strategy, in order of reliability:
      1. Read the note under an explicit "Going Concern" heading. This is the
         accounting conclusion and is what we want.
      2. Failing that, search the document with Risk Factors and
         Forward-Looking Statements removed.
      3. If the phrase only survives inside those excluded sections, it is
         boilerplate, and we record it as such rather than as a conclusion.
    """
    note = find_going_concern_note(text)
    if note:
        body = text[note[0]:note[1]]
        match = _GC_CONCLUSION.search(body)
        ctx = _context(body, match.start()) if match else body[:900]
        state = classify_going_concern(body)
        return {
            "state": state,
            "quote": " ".join(ctx.split())[:600],
            "source": "going-concern note",
        }

    substantive = _strip_forward_looking(strip_risk_factors(text))
    match = _GC_CONCLUSION.search(substantive)
    if match:
        ctx = _context(substantive, match.start())
        state = classify_going_concern(ctx)
        return {
            "state": state,
            "quote": " ".join(ctx.split())[:600],
            "source": "filing body",
        }

    if _GC_CONCLUSION.search(text):
        m2 = _GC_CONCLUSION.search(text)
        return {
            "state": GC_RISK_FACTOR_ONLY,
            "quote": " ".join(_context(text, m2.start()).split())[:600],
            "source": "risk factors / forward-looking statements only",
        }

    return {"state": GC_NONE, "quote": "", "source": ""}


def _bound_to_note(chunk: str, min_chars: int = 600) -> str:
    """Cut a note at the start of the next numbered note."""
    m = _NOTE_BOUNDARY.search(chunk, min_chars)
    return chunk[:m.start()] if m else chunk


def _section_after(text: str, heading: re.Pattern, max_chars: int = 30_000) -> str:
    """Body following a heading, bounded at the next note. Table-of-contents
    hits are skipped: they are followed by dots or a page number, not prose."""
    best = ""
    for m in heading.finditer(text):
        chunk = text[m.end(): m.end() + max_chars]
        if len(chunk) < 500:
            continue
        if re.match(r"\s*\.{2,}|\s*\d{1,3}\s*\n", chunk):
            continue
        chunk = _bound_to_note(chunk)
        if len(chunk) > len(best):
            best = chunk
    return best.strip()


REVENUE_MAX_CHARS = 20_000


def revenue_section(text: str) -> str:
    """Isolate the ASC 606 revenue policy note, or return nothing.

    Three gates, all required. Any passage can mention revenue; only a policy
    note satisfies all three:
      1. The match sits on a line that reads like a heading, not mid-paragraph.
      2. It is outside MD&A, the auditor's report and Risk Factors.
      3. Its body carries at least MIN_ASC606_MARKERS distinct ASC 606 terms.

    Returning "" is the normal outcome for filings whose note we cannot isolate
    confidently, and is strongly preferred to reporting the wrong passage.
    """
    spans = excluded_regions(text)
    best, best_score = "", 0
    for m in _REVENUE_HEADING.finditer(text):
        if _inside(m.start(), spans):
            continue
        if not _looks_like_heading(_line_at(text, m.start())):
            continue
        body = _bound_to_note(text[m.end(): m.end() + REVENUE_MAX_CHARS])
        if len(body) < 400:
            continue
        score = _asc606_score(body)
        if score < MIN_ASC606_MARKERS:
            continue
        if score > best_score or (score == best_score and len(body) > len(best)):
            best, best_score = body, score
    return best.strip()


# --- accounting standard adoptions -------------------------------------------
# Accounting Standards Updates are referenced by a structured identifier
# ("ASU 2023-09", "ASU No. 2016-13"). Diffing that *set* between two filings is
# far more precise than diffing policy prose: the identifier either appears or
# it does not, so there is nothing to threshold and nothing to mis-extract.
_ASU = re.compile(r"ASU\s*(?:No\.\s*)?(\d{4}-\d{2})", re.I)

# "The FASB issued ASU X" is not an accounting change - every filer recites the
# standards the FASB has published, whether or not they apply to them. Only an
# actual adoption changes how the company accounts for anything, so the test
# for adoption is deliberately narrow and pending language always wins.
_ADOPTED_HINT = re.compile(
    r"\b(?:the\s+(?:company|group|partnership)|we|registrant)\s+(?:early[- ]?)?adopted\b"
    r"|\badopted\s+(?:this\s+|the\s+)?(?:asu|standard|guidance|amendments|update)\b"
    r"|\bupon\s+adoption\s+of\b"
    r"|\b(?:was|were|has\s+been|have\s+been)\s+adopted\b"
    r"|\badoption\s+of\s+(?:this\s+)?(?:asu|standard)[^.]{0,40}\bdid\s+not\b",
    re.I,
)
_PENDING_HINT = re.compile(
    r"\bnot\s+yet\s+(?:adopted|effective)\b|\bwill\s+adopt\b|\bplans?\s+to\s+adopt\b"
    r"|\b(?:is|are)\s+(?:currently\s+)?evaluating\b|\bexpects?\s+to\s+adopt\b"
    r"|\bfasb\s+issued\b|\bdoes\s+not\s+expect\b|\bis\s+effective\s+for\b",
    re.I,
)


def extract_asus(text: str) -> Dict[str, Dict[str, str]]:
    """Map each referenced ASU to the sentence that mentions it."""
    out: Dict[str, Dict[str, str]] = {}
    for m in _ASU.finditer(text):
        code = m.group(1)
        if code in out:
            continue
        lo = text.rfind(".", max(0, m.start() - 400), m.start())
        hi = text.find(".", m.end())
        sentence = text[(lo + 1) if lo != -1 else max(0, m.start() - 200):
                        hi + 1 if hi != -1 else m.end() + 300]
        sentence = " ".join(sentence.split())
        status = "pending"
        if _PENDING_HINT.search(sentence):
            status = "pending"
        elif _ADOPTED_HINT.search(sentence):
            status = "adopted"
        out[code] = {"context": sentence[:400], "status": status}
    return out
