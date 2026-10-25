"""Deterministic signal detection - no AI, no interpretation, no cost.

These rules read the SEC's *own* structured item labels, so a hit is a fact
about what the filer disclosed rather than an inference. That is why these
events carry CONFIRMED confidence while the text-comparison signals in
compare.py carry DERIVED.
"""
from __future__ import annotations

import re
import unicodedata
from typing import List, Optional

from . import auditor as auditor_mod
from . import fetch
from . import officers as officers_mod
from .enrich import ITEM_TITLES
from .models import (AUDITOR_CHANGE, CONFIRMED, DERIVED, OFFICER_DEPARTURE,
                     RESTATEMENT, SIGNAL_BLURBS, Event)

# We match the SEC's numeric item codes, taken from the filing's SGML header.
# Item 4.02 = Non-Reliance on Previously Issued Financial Statements.
# Item 4.01 = Changes in Registrant's Certifying Accountant.
# Matching codes rather than English titles avoids depending on filer wording.
ITEM_RESTATEMENT = "4.02"
ITEM_AUDITOR = "4.01"
ITEM_OFFICERS = "5.02"

_SIGNAL_BY_ITEM = {
    ITEM_RESTATEMENT: RESTATEMENT,
    ITEM_AUDITOR: AUDITOR_CHANGE,
}


def classify_items(items: List[str]) -> List[str]:
    """Map an 8-K's item codes onto the signal types we publish."""
    signals, seen = [], set()
    for code in items:
        code = code.strip()
        signal = _SIGNAL_BY_ITEM.get(code)
        if signal and signal not in seen:
            seen.add(signal)
            signals.append(signal)
    return signals


def _headline(signal: str, company: str, form: str, detail: dict) -> str:
    """Headline reflects the sub-classification, which is the real signal."""
    if signal == RESTATEMENT:
        if detail.get("limb") == "b":
            return (f"{company}'s auditor told it that previously issued financial "
                    "statements should no longer be relied upon")
        return (f"{company} said previously issued financial statements should no "
                "longer be relied upon")

    if signal == AUDITOR_CHANGE:
        direction = detail.get("direction")
        if direction == "disclosure_withdrawn":
            return f"{company} withdrew its previous auditor-change disclosure"
        outgoing = detail.get("predecessor_auditor") or "its auditor"
        incoming = detail.get("successor_auditor")
        if detail.get("disagreements_disclosed") is True:
            return f"{company} disclosed a disagreement with {outgoing}"
        named = detail.get("predecessor_auditor")
        if direction == "resigned":
            return (f"{named} resigned as {company}'s auditor" if named
                    else f"{company}'s auditor resigned")
        if direction == "declined_reappointment":
            return (f"{named} declined to stand for reappointment at {company}" if named
                    else f"{company}'s auditor declined to stand for reappointment")
        if detail.get("tier_downgrade") and incoming:
            return f"{company} moved from {outgoing} to {incoming}"
        if incoming:
            return f"{company} dismissed {outgoing} and engaged {incoming}"
        return f"{company} reported a change in its independent accounting firm"

    return f"{company} filed {form}"


def _auditor_evidence(detail: dict) -> dict:
    """Build the 4.01 evidence explicitly.

    A dict comprehension that filtered falsey values silently deleted
    `disagreements_disclosed: False` - which is the informative common case,
    since a formal "there were no disagreements" statement is exactly what a
    reader wants to see stated rather than omitted.
    """
    out: dict = {}
    if detail.get("direction"):
        out["direction"] = detail["direction"]
        out["direction_label"] = auditor_mod.DIRECTION_LABELS.get(detail["direction"], "")
    if detail.get("disagreements_disclosed") is not None:
        out["disagreements_disclosed"] = detail["disagreements_disclosed"]
    for key in ("predecessor_auditor", "successor_auditor",
                "predecessor_tier", "successor_tier"):
        if detail.get(key):
            out[key] = detail[key]
    if detail.get("tier_downgrade"):
        out["tier_downgrade"] = True
    return out


def _amendment_explanation(text: str) -> str:
    note = re.search(r"\bexplanatory\s+note\b", text, re.I)
    scope = text[note.end():] if note else text
    if note:
        heading = re.search(r"^\s*(?:Item\s+)?4\.01\b", scope, re.I | re.M)
        if heading:
            scope = scope[:heading.start()]
    return " ".join(unicodedata.normalize("NFKC", scope).split())[:12_000]


def signature_name_only_amendment(text: str, form: str) -> bool:
    """Exclude only an explicitly sole registrant-name signature correction."""
    if (form or "").strip().upper() != "8-K/A" or not text:
        return False
    scope = _amendment_explanation(text)
    purpose = re.search(
        r"\bsolely\s+(?:for\s+the\s+purpose\s+of\s+|to\s+)correct(?:ing)?\s+"
        r"(?:an?\s+)?(?:scrivener['’]s\s+error\s+(?:regarding|in)\s+)?"
        r"(?:the\s+)?name\s+of\s+the\s+(?:registrant|company)\s+"
        r"(?:included\s+)?on\s+the\s+signature\s+page\b", scope, re.I)
    unchanged = re.search(r"\ball\s+other\b.{0,150}?\bremain\s+unchanged\b", scope, re.I)
    additional_change = re.search(
        r"\b(?:and|also)\s+(?:to\s+)?(?:correct|update|amend|report|disclose)\w*\b"
        r"|\b(?:additional|new)\s+(?:information|disclosures?|details?)\b", scope, re.I)
    return bool(purpose and unchanged and not additional_change)


def auditor_letter_only_amendment(text: str, form: str) -> bool:
    """An explicitly administrative Exhibit 16 amendment is not a new change.

    Item 4.01 is repeated when the outgoing auditor's response arrives later.
    Suppress only a stated letter-filing purpose, with either an explicit
    "solely" restriction or a statement that no other disclosures changed.
    Amendments correcting dates, adding disagreements, or changing an auditor
    appointment remain reportable. Missing text is never proof of duplication.
    """
    if (form or "").strip().upper() != "8-K/A" or not text:
        return False
    scope = _amendment_explanation(text)
    if not (re.search(r"\bletter\b", scope, re.I) and
            re.search(r"\bexhibit\s+16(?:\s*\.\s*1)?\b", text, re.I)):
        return False
    purpose = re.search(
        r"\b(?:is|are)\s+(?:being\s+)?filed\s+"
        r"(?:(solely|only)\s+)?(?:to|for\s+the\s+purpose\s+of)\s+"
        r"(?:file|filing|include|including|furnish|furnishing|provide|providing|"
        r"submit|submitting|disclose|disclosing|update|updating)\b",
        scope, re.I)
    if not purpose:
        return False
    explanation = scope[purpose.start():]
    purpose_sentence = re.split(r"\.\s+(?=[A-Z])", explanation, maxsplit=1)[0]
    if not re.search(r"\bletter\b|\bexhibit\s+16(?:\s*\.\s*1)?\b",
                     purpose_sentence, re.I):
        return False
    if re.search(r"\b(?:appoint\w*|engag\w*|resign\w*|dismiss\w*|terminat\w*|"
                 r"disagreement\w*)\b", purpose_sentence, re.I):
        return False
    # "File the letter AND provide supplemental disclosure" is substantive,
    # even when a later boilerplate sentence says all other items are unchanged.
    if re.search(r"\b(?:correct\w*|clarif\w*|supplement\w*)\b"
                 r"|\b(?:additional|new)\s+(?:information|disclosures?|details?)\b"
                 r"|\b(?:and|also)\s+(?:to\s+)?(?:report|disclose|describe)\b",
                 explanation, re.I):
        return False
    no_other_changes = re.search(
        r"\bno\s+other\s+changes?\s+(?:(?:have|has)\s+been\s+|were\s+|are\s+)?(?:made\b|to\b)"
        r"|\bdoes\s+not\s+(?:otherwise\s+)?(?:modify|amend|update)\b"
        r".{0,90}?\b(?:any\s+)?other\s+(?:information|disclosures?)\b"
        r"|\ball\s+other\b.{0,100}?\bremain\s+unchanged\b",
        explanation, re.I)
    return bool(purpose.group(1) or no_other_changes)


def _subclassify(signal: str, filing) -> dict:
    """Read the 8-K itself. The item code says an event happened; only the
    document says which kind, and the kind is what matters."""
    from . import compare                       # local import avoids a cycle
    try:
        url = compare.current_document(filing.cik, filing.accession)
        text = compare.load_text(url) if url else None
    except (fetch.SECBlocked, fetch.TransientFetchError):
        raise                                    # see compare.load_text
    except Exception:                            # noqa: BLE001
        text = None
    if not text:
        return {}
    if signal == RESTATEMENT:
        return auditor_mod.classify_402(text)
    if auditor_letter_only_amendment(text, filing.form):
        return {"letter_only_amendment": True}
    if signature_name_only_amendment(text, filing.form):
        return {"signature_name_only_amendment": True}
    return auditor_mod.classify_401(text, getattr(filing, "company", ""))


def _officer_event(filing) -> List[Event]:
    """Item 5.02 covers every director and officer change and is one of the
    commonest 8-K items, so the code alone is noise. Only a finance chief
    leaving qualifies, and the filing has to be read to know."""
    from . import compare

    try:
        url = compare.current_document(filing.cik, filing.accession)
        text = compare.load_text(url) if url else None
    except (fetch.SECBlocked, fetch.TransientFetchError):
        raise                                    # see compare.load_text
    except Exception:                            # noqa: BLE001
        return []
    if not text:
        return []
    if auditor_mod.item_disclosure_withdrawn(text, ITEM_OFFICERS):
        return []

    detail = officers_mod.classify(" ".join(text.split()))
    if not detail.get("is_finance_departure"):
        return []

    ticker = ""
    sub = compare.submissions(filing.cik)
    if sub and sub.get("tickers"):
        ticker = sub["tickers"][0]

    return [Event(
        signal_type=OFFICER_DEPARTURE,
        confidence=DERIVED,
        company=filing.company,
        cik=filing.cik,
        ticker=ticker,
        form=filing.form,
        filed=filing.filed,
        accession=filing.accession,
        filing_url=filing.index_url,
        headline=officers_mod.headline(filing.company, detail),
        sic_desc=filing.sic_desc,
        evidence={
            "source": "SEC 8-K item code",
            "item_code": ITEM_OFFICERS,
            "item_title": "Departure of Directors or Certain Officers",
            "severity": officers_mod.severity(detail),
            "role": officers_mod.ROLE_LABELS.get(detail.get("role", ""), ""),
            "successor_named": detail.get("successor_named"),
            "interim_only": detail.get("interim_only"),
            **({"adverse_language": detail["adverse_language"]}
               if detail.get("adverse_language") else {}),
            "why": SIGNAL_BLURBS[OFFICER_DEPARTURE],
        },
    )]


def events_from_filing(filing) -> List[Event]:
    """Build events from an 8-K's item codes. Returns [] for everything else."""
    if not filing.items:
        return []

    events: List[Event] = []
    if ITEM_OFFICERS in [c.strip() for c in filing.items]:
        events.extend(_officer_event(filing))

    for signal in classify_items(filing.items):
        code = ITEM_RESTATEMENT if signal == RESTATEMENT else ITEM_AUDITOR
        detail = _subclassify(signal, filing)
        if signal == AUDITOR_CHANGE and (detail.get("letter_only_amendment")
                                        or detail.get("signature_name_only_amendment")):
            continue
        severity = (
            "high" if (signal == RESTATEMENT and detail.get("limb") == "b")
            else auditor_mod.rank_401(detail) if signal == AUDITOR_CHANGE
            else "normal"
        )
        events.append(
            Event(
                signal_type=signal,
                confidence=CONFIRMED,
                company=filing.company,
                cik=filing.cik,
                form=filing.form,
                filed=filing.filed,
                accession=filing.accession,
                filing_url=filing.index_url,
                headline=_headline(signal, filing.company, filing.form, detail),
                sic_desc=filing.sic_desc,
                evidence={
                    "source": "SEC 8-K item code",
                    "item_code": code,
                    "item_title": ITEM_TITLES.get(code, ""),
                    "severity": severity,
                    "why": (auditor_mod.DIRECTION_LABELS["disclosure_withdrawn"]
                            if detail.get("direction") == "disclosure_withdrawn"
                            else SIGNAL_BLURBS[signal]),
                    **({"limb": detail["limb"],
                        "limb_label": auditor_mod.LIMB_LABELS.get(detail["limb"], ""),
                        "limb_basis": detail.get("basis", "")}
                       if signal == RESTATEMENT and detail.get("limb") else {}),
                    **(_auditor_evidence(detail) if signal == AUDITOR_CHANGE else {}),
                },
            )
        )
    return events
