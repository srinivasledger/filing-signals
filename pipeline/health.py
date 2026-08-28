"""Self-checks, run after every pipeline run and published on the status page.

The site updates unattended, so nothing else notices when it starts producing
subtly wrong output. These checks assert the properties the design depends on -
every claim carries a citation, comparison signals name the filing they were
compared against, re-running never duplicates an entry - and say so in public
rather than in a log nobody reads.

Checks never raise. A check that cannot run reports "unknown" rather than
taking the run down with it.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
from typing import Dict, List

from . import config
from .models import (AUDITOR_CHANGE, CONFIRMED, GOING_CONCERN, POLICY_CHANGE,
                     RESTATEMENT, REVENUE_RECOGNITION)

log = logging.getLogger(__name__)

OK, WARN, FAIL, UNKNOWN = "ok", "warn", "fail", "unknown"

COMPARISON_SIGNALS = {GOING_CONCERN, POLICY_CHANGE, REVENUE_RECOGNITION}
STALE_AFTER_DAYS = 5
SIZE_STALE_AFTER_DAYS = 14
MIN_SIZE_COVERAGE = 3000


def _check(name: str, status: str, detail: str) -> Dict:
    return {"name": name, "status": status, "detail": detail}


def _business_days_between(start: dt.date, end: dt.date) -> int:
    days, cur = 0, start
    while cur < end:
        cur += dt.timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days


def run_checks(events, state: Dict, today: dt.date) -> Dict:
    checks: List[Dict] = []

    # --- is it actually running? ---
    last = state.get("last_processed")
    if not last:
        checks.append(_check("Pipeline has run", FAIL, "no filing day recorded yet"))
    else:
        try:
            behind = _business_days_between(dt.date.fromisoformat(last), today)
            status = OK if behind <= STALE_AFTER_DAYS else FAIL
            checks.append(_check(
                "Pipeline is current", status,
                f"last complete filing day {last}"
                + (f", {behind} business days ago" if behind else ", today")))
        except ValueError:
            checks.append(_check("Pipeline is current", UNKNOWN,
                                 f"unreadable date {last!r}"))

    runs = state.get("runs", [])
    if runs:
        recent = runs[-10:]
        scanned = sum(r.get("index_rows") or 0 for r in recent)
        checks.append(_check(
            "Recent runs completed", OK if scanned else WARN,
            f"{len(recent)} runs recorded, {scanned:,} index rows read"))
    else:
        checks.append(_check("Recent runs completed", UNKNOWN, "no run history"))

    # --- was SEC access refused recently? ---
    recent_blocks = [r for r in state.get("runs", [])[-10:] if r.get("blocked")]
    if recent_blocks:
        last = recent_blocks[-1].get("date", "?")
        checks.append(_check(
            "SEC access", WARN,
            f"{len(recent_blocks)} of the last 10 runs were refused by SEC; "
            f"most recently for {last}. The next run resumes from there."))
    else:
        checks.append(_check("SEC access", OK, "no refusals in the last 10 runs"))

    # --- every published claim is citable ---
    total = len(events)
    if not total:
        checks.append(_check("Events recorded", WARN, "no events yet"))
        return {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "checks": checks, "summary": _summarise(checks)}

    uncited = [e for e in events if not e.filing_url or "sec.gov" not in e.filing_url]
    checks.append(_check(
        "Every entry links to its filing", OK if not uncited else FAIL,
        f"{total - len(uncited)}/{total} link to sec.gov"))

    no_evidence = [e for e in events if not e.evidence.get("source")]
    checks.append(_check(
        "Every entry states how it was found", OK if not no_evidence else FAIL,
        f"{total - len(no_evidence)}/{total} carry a detection method"))

    # --- the deterministic promise ---
    confirmed = [e for e in events if e.confidence == CONFIRMED]
    bad_codes = [e for e in confirmed
                 if e.evidence.get("item_code") not in ("4.01", "4.02")]
    checks.append(_check(
        "Item-code entries carry an item code",
        OK if not bad_codes else FAIL,
        f"{len(confirmed)} entries from SEC item codes, {len(bad_codes)} without one"))

    # --- the comparison promise ---
    comparisons = [e for e in events if e.signal_type in COMPARISON_SIGNALS]
    no_prior = [e for e in comparisons if not e.prior_accession]
    checks.append(_check(
        "Comparisons name the prior filing",
        OK if not no_prior else FAIL,
        f"{len(comparisons) - len(no_prior)}/{len(comparisons)} cite what they "
        "were compared against"))

    # --- idempotency ---
    # Counted from the rows on disk, not from the loaded events: the loader
    # de-duplicates as it reads, so measuring its output reported "no
    # duplicates" while a duplicate sat in the file.
    from . import publish as _publish

    unique = len({e.id for e in events})
    on_disk = _publish.raw_row_count()
    dupes = max(0, on_disk - unique)
    checks.append(_check(
        "No duplicated entries", OK if not dupes else FAIL,
        f"{unique} unique entries across {on_disk} rows"
        + (f", {dupes} duplicated" if dupes else "")))

    # --- quote hygiene (a real past defect) ---
    ragged = [e for e in events
              if e.quote and not re.match(r'^[A-Z“("•]', e.quote.strip())]
    checks.append(_check(
        "Quotes start at a sentence", OK if not ragged else WARN,
        f"{len(ragged)} of {sum(1 for e in events if e.quote)} quoted passages "
        "begin mid-sentence"))

    # --- is the home page still showing everything? ---
    # Truncation here is deliberate and stated on the page itself, so it is
    # reported, not warned about. It was a WARN while the cap sat above the
    # record and crossing it would have been a surprise; with the cap set to a
    # window the page is meant to hold, a permanent warning would sit in the
    # header of every page and bury the ones that mean something.
    from .render import MAX_HOME_EVENTS
    if total > MAX_HOME_EVENTS:
        checks.append(_check(
            "Home page window", OK,
            f"showing the most recent {MAX_HOME_EVENTS} of {total}, as stated "
            "on the page; the full record is on the signals pages"))
    else:
        checks.append(_check("Home page window", OK,
                             f"all {total} entries fit on the home page"))

    # --- size index ---
    checks.append(_size_check(today))

    # --- follow-on statistics ---
    hist_path = config.STATE_DIR / "history.json"
    if hist_path.exists():
        try:
            hist = json.loads(hist_path.read_text())
            n = hist.get("total_historical_events", 0)
            checks.append(_check(
                "Follow-on rates computed", OK if n else WARN,
                f"{hist.get('companies', 0)} companies, {n:,} historical events"))
        except ValueError:
            checks.append(_check("Follow-on rates computed", WARN, "stats file unreadable"))
    else:
        checks.append(_check("Follow-on rates computed", UNKNOWN, "not yet computed"))

    return {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "checks": checks, "summary": _summarise(checks)}


def _size_check(today: dt.date) -> Dict:
    path = config.STATE_DIR / "company_size.json"
    if not path.exists():
        return _check("Company size index", UNKNOWN, "not built yet")
    try:
        blob = json.loads(path.read_text())
    except ValueError:
        return _check("Company size index", WARN, "index unreadable")
    count = blob.get("count", 0)
    built = blob.get("built_on", "")
    try:
        age = (today - dt.date.fromisoformat(built)).days
    except ValueError:
        return _check("Company size index", WARN, f"{count:,} companies, undated")
    status = OK
    if age > SIZE_STALE_AFTER_DAYS or count < MIN_SIZE_COVERAGE:
        status = WARN
    return _check("Company size index", status,
                  f"{count:,} companies, refreshed {age} day(s) ago")


# What a page may weigh before it stops feeling instant. Pages are served
# gzipped, so this is measured over the wire, not on disk. 250 KB is generous:
# the home page is ~39 KB today and the whole record page ~8 KB.
MAX_PAGE_WIRE_BYTES = 250 * 1024


def page_weight_check(public: "pathlib.Path") -> Dict:
    """Warn before a page gets slow, rather than after someone notices.

    Every list page grows with the record, and nothing prunes them. Rather than
    guessing when that becomes a problem, the build measures itself: this fails
    nothing, but it turns "it will stay fast" from a promise into something
    observed on every run.
    """
    import gzip

    pages = sorted(public.rglob("*.html"))     # every page, at any depth
    if not pages:
        return _check("Page weight", UNKNOWN, "nothing built yet")

    weighed = []
    for path in pages:
        try:
            weighed.append((len(gzip.compress(path.read_bytes())), path))
        except OSError:
            continue
    if not weighed:
        return _check("Page weight", UNKNOWN, "pages unreadable")

    worst, path = max(weighed)
    detail = (f"heaviest page {path.name} is {worst / 1024:.0f} KB over the wire "
              f"(limit {MAX_PAGE_WIRE_BYTES // 1024} KB), {len(weighed)} pages checked")
    if worst > MAX_PAGE_WIRE_BYTES:
        return _check("Page weight", WARN, detail + " - time to split it by year")
    return _check("Page weight", OK, detail)


def _summarise(checks: List[Dict]) -> Dict:
    counts = {OK: 0, WARN: 0, FAIL: 0, UNKNOWN: 0}
    for c in checks:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    overall = FAIL if counts[FAIL] else (WARN if counts[WARN] else OK)
    return {"overall": overall, **counts, "total": len(checks)}
