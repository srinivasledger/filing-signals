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


def _subclassify(signal: str, filing) -> dict:
    """Read the 8-K itself. The item code says an event happened; only the
    document says which kind, and the kind is what matters."""
    from . import compare                       # local import avoids a cycle
    try:
        url = compare.current_document(filing.cik, filing.accession)
        text = compare.load_text(url) if url else None
    except Exception:                            # noqa: BLE001
        text = None
    if not text:
        return {}
    if signal == RESTATEMENT:
        return auditor_mod.classify_402(text)
    return auditor_mod.classify_401(text)


def _officer_event(filing) -> List[Event]:
    """Item 5.02 covers every director and officer change and is one of the
    commonest 8-K items, so the code alone is noise. Only a finance chief
    leaving qualifies, and the filing has to be read to know."""
    from . import compare

    try:
        url = compare.current_document(filing.cik, filing.accession)
        text = compare.load_text(url) if url else None
    except Exception:                            # noqa: BLE001
        return []
    if not text:
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
                    "why": SIGNAL_BLURBS[signal],
                    **({"limb": detail["limb"],
                        "limb_label": auditor_mod.LIMB_LABELS.get(detail["limb"], ""),
                        "limb_basis": detail.get("basis", "")}
                       if signal == RESTATEMENT and detail.get("limb") else {}),
                    **(_auditor_evidence(detail) if signal == AUDITOR_CHANGE else {}),
                },
            )
        )
    return events
