"""Historical sequence rates from per-company filing history.

The obvious way to get history is to walk the daily index back a few years.
That is ~750 requests per year and hours of runtime. It is also unnecessary:
`data.sec.gov/submissions/CIK*.json` returns up to a thousand filings per
company - back to 2014 for an active filer - and 8-K rows carry their `items`.
One request per company therefore buys a decade of item-coded history.

What this measures, and what it does not:

  For each company we look at, we take every Item 4.01 (auditor change), every
  Item 4.02 (non-reliance) and every Form 12b-25 (late filing) it has ever
  filed, and ask how often one precedes another within a window.

  This is a CONDITIONAL rate within a chosen population, not a population base
  rate. Without a matched control group it cannot support "an auditor change
  makes a restatement N times more likely". It answers the narrower and still
  useful question: among these companies, how often did one follow the other.
"""
from __future__ import annotations

import datetime as dt
import logging
from collections import Counter
from typing import Dict, Iterable, List, Optional

from . import compare, fetch
from .models import AUDITOR_CHANGE, LATE_FILING, RESTATEMENT

log = logging.getLogger(__name__)

ITEM_AUDITOR, ITEM_RESTATEMENT = "4.01", "4.02"
LATE_FORM_PREFIX = "NT "

# How long after a precursor we still count a follow-on event as related.
WINDOW_DAYS = 540


def _events_from_submissions(sub: Dict) -> List[Dict]:
    """Every 4.01 / 4.02 / late filing in a company's history."""
    out: List[Dict] = []
    blocks = [sub.get("filings", {}).get("recent", {})]
    for block in blocks:
        forms = block.get("form") or []
        for i, form in enumerate(forms):
            date = (block.get("filingDate") or [None] * len(forms))[i]
            items = (block.get("items") or [""] * len(forms))[i] or ""
            if not date:
                continue
            if form.upper().startswith(LATE_FORM_PREFIX):
                out.append({"kind": LATE_FILING, "date": date, "form": form})
            elif form.upper().startswith("8-K"):
                codes = {c.strip() for c in items.split(",") if c.strip()}
                if ITEM_RESTATEMENT in codes:
                    out.append({"kind": RESTATEMENT, "date": date, "form": form})
                if ITEM_AUDITOR in codes:
                    out.append({"kind": AUDITOR_CHANGE, "date": date, "form": form})
    out.sort(key=lambda e: e["date"])
    return out


def company_history(cik: int) -> List[Dict]:
    sub = compare.submissions(cik)
    return _events_from_submissions(sub) if sub else []


def _followed_within(history: List[Dict], first: str, second: str,
                     window_days: int = WINDOW_DAYS) -> Optional[bool]:
    """True if a `second` event follows the company's FIRST `first` within the
    window. None when the company never had a `first` event at all.

    Measured from the first occurrence, deliberately. Taking any occurrence
    gave a company one chance per event: a filer that files an NT form every
    quarter had eight or ten overlapping 540-day windows in which an auditor
    change could land, and counted as a follow-on if any of them caught one.
    That inflates every rate, and inflates them most for exactly the frequent
    filers this population is full of.
    """
    firsts = [e for e in history if e["kind"] == first]
    if not firsts:
        return None
    fd = dt.date.fromisoformat(firsts[0]["date"])       # history is date-sorted
    for sec in (e for e in history if e["kind"] == second):
        sd = dt.date.fromisoformat(sec["date"])
        if 0 < (sd - fd).days <= window_days:
            return True
    return False


PAIRS = [
    (AUDITOR_CHANGE, RESTATEMENT, "An auditor change was followed by a non-reliance filing"),
    (LATE_FILING, RESTATEMENT, "A late filing was followed by a non-reliance filing"),
    (LATE_FILING, AUDITOR_CHANGE, "A late filing was followed by an auditor change"),
    (RESTATEMENT, AUDITOR_CHANGE, "A non-reliance filing was followed by an auditor change"),
]


def sequence_rates(ciks: Iterable[int]) -> Dict:
    """Compute conditional follow-on rates across a set of companies."""
    ciks = list(dict.fromkeys(ciks))
    histories: Dict[int, List[Dict]] = {}
    for cik in ciks:
        try:
            histories[cik] = company_history(cik)
        except fetch.SECBlocked:
            raise
        except Exception as exc:                 # noqa: BLE001
            log.warning("history lookup failed for CIK %s: %s", cik, exc)

    rows = []
    for first, second, label in PAIRS:
        eligible = followed = 0
        for hist in histories.values():
            result = _followed_within(hist, first, second)
            if result is None:
                continue
            eligible += 1
            followed += 1 if result else 0
        rows.append({
            "label": label, "first": first, "second": second,
            "eligible": eligible, "followed": followed,
            "rate": (f"{followed / eligible * 100:.0f}%" if eligible else "—"),
        })

    counts = Counter(e["kind"] for h in histories.values() for e in h)
    return {
        "companies": len(histories),
        "window_days": WINDOW_DAYS,
        "rows": rows,
        "event_counts": dict(counts),
        "total_historical_events": sum(counts.values()),
    }
