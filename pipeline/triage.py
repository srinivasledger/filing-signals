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

from .enrich import ITEM_TITLES
from .models import (AUDITOR_CHANGE, CONFIRMED, RESTATEMENT, SIGNAL_BLURBS,
                     Event)

# We match the SEC's numeric item codes, taken from the filing's SGML header.
# Item 4.02 = Non-Reliance on Previously Issued Financial Statements.
# Item 4.01 = Changes in Registrant's Certifying Accountant.
# Matching codes rather than English titles avoids depending on filer wording.
ITEM_RESTATEMENT = "4.02"
ITEM_AUDITOR = "4.01"

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


def _headline(signal: str, company: str, form: str) -> str:
    if signal == RESTATEMENT:
        return f"{company} said previously issued financial statements should no longer be relied upon"
    if signal == AUDITOR_CHANGE:
        return f"{company} reported a change in its independent accounting firm"
    return f"{company} filed {form}"


def events_from_filing(filing) -> List[Event]:
    """Build events from an 8-K's item codes. Returns [] for everything else."""
    if not filing.items:
        return []

    events: List[Event] = []
    for signal in classify_items(filing.items):
        code = ITEM_RESTATEMENT if signal == RESTATEMENT else ITEM_AUDITOR
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
                headline=_headline(signal, filing.company, filing.form),
                sic_desc=filing.sic_desc,
                evidence={
                    "source": "SEC 8-K item code",
                    "item_code": code,
                    "item_title": ITEM_TITLES.get(code, ""),
                    "why": SIGNAL_BLURBS[signal],
                },
            )
        )
    return events
