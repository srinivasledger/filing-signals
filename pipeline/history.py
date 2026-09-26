"""Historical sequence rates from per-company filing history.

The SEC submissions index contains recent filings and names any additional
archive files. All of those files must be loaded before choosing a company's
earliest observed signal. A recent-only index can truncate that observation.

What this measures, and what it does not:

  For each company, take the earliest observed Item 4.01 (auditor change),
  Item 4.02 (non-reliance) or Form 12b-25 (late filing) in the available EDGAR
  records since the current 8-K item numbering began on August 23, 2004.
  Only include it once the entire follow-on window has elapsed.
  Missing archives exclude a company, and recent precursors exclude it from
  that pair's denominator even if a follow-on has already been observed.

  This is a CONDITIONAL rate within a chosen population, not a population base
  rate. Without a matched control group it cannot support "an auditor change
  makes a restatement N times more likely". It answers the narrower and still
  useful question: among these companies, how often did one follow the other.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from collections import Counter
from typing import Dict, Iterable, List, Optional

from . import compare, config, fetch
from .models import AUDITOR_CHANGE, LATE_FILING, RESTATEMENT

log = logging.getLogger(__name__)

ITEM_AUDITOR, ITEM_RESTATEMENT = "4.01", "4.02"
LATE_FORM_PREFIX = "NT "

# How long after a precursor we still count a follow-on event as related.
WINDOW_DAYS = 540
METHODOLOGY_VERSION = 2
# Items 4.01/4.02 took effect on this date. Earlier NT forms are observable,
# but their follow-ons use different (or not-yet-required) item codes. Starting
# every signal at the same date avoids counting those unobservable windows as
# negative outcomes. SEC rule: https://www.sec.gov/file/2004-federal-register-pdf-4
OBSERVATION_START = dt.date(2004, 8, 23)


def _events_from_submissions(sub: Dict, archives: Iterable[Dict] = ()) -> List[Dict]:
    """Combine recent and archived item-coded events, without overlap.

    Incomplete arrays cannot establish a first occurrence. Fail the whole
    company's lookup instead of silently treating missing data as no events.
    """
    out: List[Dict] = []
    seen = set()
    blocks = [sub.get("filings", {}).get("recent"), *archives]
    for block in blocks:
        if not isinstance(block, dict):
            raise ValueError("missing submissions filing block")
        forms = block.get("form")
        if not isinstance(forms, list):
            raise ValueError("missing submissions forms")
        for key in ("accessionNumber", "filingDate", "items"):
            if not isinstance(block.get(key), list) or len(block[key]) != len(forms):
                raise ValueError(f"incomplete submissions {key} array")
        for i, form in enumerate(forms):
            date = block["filingDate"][i]
            dt.date.fromisoformat(date)
            accession = block["accessionNumber"][i]
            if not accession:
                raise ValueError("missing submissions accession number")
            items = block["items"][i] or ""
            kinds = []
            if form.upper().startswith(LATE_FORM_PREFIX):
                kinds.append(LATE_FILING)
            elif form.upper().startswith("8-K"):
                codes = {c.strip() for c in items.split(",") if c.strip()}
                if ITEM_RESTATEMENT in codes:
                    kinds.append(RESTATEMENT)
                if ITEM_AUDITOR in codes:
                    kinds.append(AUDITOR_CHANGE)
            for kind in kinds:
                key = (accession, kind)
                if key not in seen:
                    seen.add(key)
                    out.append({"kind": kind, "date": date, "form": form,
                                "accession": accession})
    out.sort(key=lambda e: e["date"])
    return out


def company_history(cik: int) -> List[Dict]:
    """All events in the SEC's listed records, or an error if any are missing."""
    sub = compare.submissions(cik)
    if not sub:
        # compare.submissions returns None for failed requests as well as 404s.
        # Neither is evidence that the company had no historical signals.
        raise ValueError("company submissions unavailable")
    files = sub.get("filings", {}).get("files")
    if not isinstance(files, list):
        raise ValueError("missing submissions archive list")
    archives = []
    loaded = set()
    for descriptor in files:
        name = descriptor.get("name", "")
        if not re.fullmatch(rf"CIK{cik:010d}-submissions-\d+\.json", name):
            raise ValueError("invalid submissions archive name")
        if name in loaded:
            continue
        # Use the shared client: it owns the SEC rate limit, caching, retries,
        # and SECBlocked behavior. A block must abort the refresh immediately.
        archives.append(fetch.get_json(f"{config.SUBMISSIONS}/{name}"))
        loaded.add(name)
    return _events_from_submissions(sub, archives)


def _followed_within(history: List[Dict], first: str, second: str,
                     window_days: int = WINDOW_DAYS, *,
                     as_of: Optional[dt.date] = None) -> Optional[bool]:
    """Follow-on outcome for a mature window from the earliest observed first.

    None means no precursor, or its full window has not yet elapsed. Immature
    successes must also be excluded, or the rate would be biased upward.

    Measured from the first occurrence, deliberately. Taking any occurrence
    gave a company one chance per event: a filer that files an NT form every
    quarter had eight or ten overlapping 540-day windows in which an auditor
    change could land, and counted as a follow-on if any of them caught one.
    That inflates every rate, and inflates them most for exactly the frequent
    filers this population is full of.
    """
    as_of = as_of or dt.datetime.now(dt.timezone.utc).date()
    firsts = [dt.date.fromisoformat(e["date"]) for e in history
              if e["kind"] == first and dt.date.fromisoformat(e["date"]) <= as_of]
    if not firsts:
        return None
    fd = min(firsts)
    if (as_of - fd).days < window_days:
        return None
    for sec in (e for e in history if e["kind"] == second):
        sd = dt.date.fromisoformat(sec["date"])
        if sd <= as_of and 0 < (sd - fd).days <= window_days:
            return True
    return False


PAIRS = [
    (AUDITOR_CHANGE, RESTATEMENT, "An auditor change was followed by a non-reliance filing"),
    (LATE_FILING, RESTATEMENT, "A late filing was followed by a non-reliance filing"),
    (LATE_FILING, AUDITOR_CHANGE, "A late filing was followed by an auditor change"),
    (RESTATEMENT, AUDITOR_CHANGE, "A non-reliance filing was followed by an auditor change"),
]


def sequence_rates(ciks: Iterable[int], *, as_of: Optional[dt.date] = None) -> Dict:
    """Compute mature-window rates, naming unavailable and immature coverage."""
    as_of = as_of or dt.datetime.now(dt.timezone.utc).date()
    ciks = list(dict.fromkeys(ciks))
    histories: Dict[int, List[Dict]] = {}
    omitted = []
    for cik in ciks:
        try:
            histories[cik] = [e for e in company_history(cik)
                              if OBSERVATION_START <= dt.date.fromisoformat(e["date"]) <= as_of]
        except fetch.SECBlocked:
            raise
        except Exception as exc:                 # noqa: BLE001
            log.warning("history lookup failed for CIK %s: %s", cik, exc)
            omitted.append(cik)

    rows = []
    for first, second, label in PAIRS:
        eligible = followed = immature = with_first = 0
        for hist in histories.values():
            if not any(e["kind"] == first for e in hist):
                continue
            with_first += 1
            result = _followed_within(hist, first, second, as_of=as_of)
            if result is None:
                immature += 1
                continue
            eligible += 1
            followed += 1 if result else 0
        rows.append({
            "label": label, "first": first, "second": second,
            "eligible": eligible, "followed": followed,
            "with_first": with_first, "immature": immature,
            "rate": (f"{followed / eligible * 100:.0f}%" if eligible else "—"),
        })

    counts = Counter(e["kind"] for h in histories.values() for e in h)
    return {
        "methodology_version": METHODOLOGY_VERSION,
        "as_of": as_of.isoformat(),
        "observation_start": OBSERVATION_START.isoformat(),
        "requested_companies": len(ciks),
        "companies": len(histories),
        "omitted_companies": len(omitted),
        "omitted_ciks": omitted,
        "window_days": WINDOW_DAYS,
        "rows": rows,
        "event_counts": dict(counts),
        "total_historical_events": sum(counts.values()),
    }
