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
from collections import defaultdict
from typing import Dict, List

from . import config
from .models import (AUDITOR_CHANGE, COMMENT_LETTER, CONFIRMED, GOING_CONCERN, LATE_FILING,
                     MATERIAL_WEAKNESS, OFFICER_DEPARTURE, POLICY_CHANGE,
                     RESTATEMENT, REVENUE_RECOGNITION)

log = logging.getLogger(__name__)

OK, WARN, FAIL, UNKNOWN = "ok", "warn", "fail", "unknown"

COMPARISON_SIGNALS = {GOING_CONCERN, POLICY_CHANGE, REVENUE_RECOGNITION}
# The scan runs every weekday evening, so one business day behind is normal
# and anything more is not. These were a single threshold of 5, which let the
# dataset sit three business days out of date while the page said OK - the
# failure mode being: a scan fails, commits nothing, and the last successful
# state keeps looking healthy because nothing records the attempt.
STALE_WARN_AFTER_DAYS = 1     # beyond this, say so on the page
STALE_AFTER_DAYS = 5          # beyond this, fail the run
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
            status = (OK if behind <= STALE_WARN_AFTER_DAYS
                      else FAIL if behind > STALE_AFTER_DAYS else WARN)
            note = ("" if behind <= STALE_WARN_AFTER_DAYS else
                    " - the dataset is behind; a scan that fails commits "
                    "nothing, so the last good state stays published")
            checks.append(_check(
                "Pipeline is current", status,
                f"last complete filing day {last}"
                + (f", {behind} business day{'' if behind == 1 else 's'} ago"
                   if behind else ", today") + note))
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
    # Asserting the link resolves to THIS filing, not merely to sec.gov. The
    # weaker version passed for any sec.gov URL, including the wrong one.
    wrong_filing = [e for e in events if e.filing_url and e.accession
                    and e.accession.replace("-", "") not in e.filing_url.replace("-", "")]
    checks.append(_check(
        "Every entry links to its own filing", OK if not (uncited or wrong_filing) else FAIL,
        f"{total - len(uncited) - len(wrong_filing)}/{total} link to the "
        "accession they report"))

    # A non-empty string was the old bar, which any typo cleared. The method
    # has to name something the pipeline actually does.
    KNOWN_METHOD = ("item code", "comparison", "compared", "Item 9A", "Form 12b-25",
                    "Item 5.02", "Re:", "note", "cover", "letter", "CORRESP", "UPLOAD")
    no_evidence = [e for e in events
                   if not any(k.lower() in (e.evidence.get("source") or "").lower()
                              for k in KNOWN_METHOD)]
    checks.append(_check(
        "Every entry states how it was found", OK if not no_evidence else FAIL,
        f"{total - len(no_evidence)}/{total} name a detection method the "
        "pipeline implements"))

    # --- the deterministic promise ---
    # The code has to be the one that produces this signal. Merely carrying
    # "4.01 or 4.02" would pass an auditor change built from a restatement code.
    CODE_FOR = {RESTATEMENT: "4.02", AUDITOR_CHANGE: "4.01",
                OFFICER_DEPARTURE: "5.02"}
    confirmed = [e for e in events if e.confidence == CONFIRMED]
    bad_codes = [e for e in confirmed
                 if e.signal_type in CODE_FOR
                 and e.evidence.get("item_code") != CODE_FOR[e.signal_type]]
    checks.append(_check(
        "Item codes match the signal they produced",
        OK if not bad_codes else FAIL,
        f"{len(confirmed)} entries from SEC item codes, {len(bad_codes)} "
        "carrying the wrong code for their signal"))

    # --- the comparison promise ---
    # Naming a prior filing was the old bar. It passed while amendments were
    # being compared against the previous filing in the series instead of the
    # one they amend - which published "no longer discloses going concern"
    # about companies whose amendment simply did not re-file the note.
    comparisons = [e for e in events if e.signal_type in COMPARISON_SIGNALS]
    no_prior = [e for e in comparisons if not e.prior_accession]
    unsound = []
    for e in comparisons:
        if not e.prior_accession:
            continue
        # Uses compare._base_form, the same helper that chooses the prior
        # filing. A local rstrip("/A") would be a second opinion on what a
        # form's base is, and two opinions is how a check disagrees with the
        # code it is checking.
        from .compare import _base_form

        prior_form = (e.evidence.get("prior_form") or "").upper()
        cur = e.form.upper()
        if _base_form(prior_form) != _base_form(cur):
            unsound.append((e, "compared across form types"))
        elif (e.evidence.get("prior_filed") or "") >= e.filed:
            unsound.append((e, "prior filing is not earlier"))
        elif cur.endswith("/A") and e.evidence.get("disclosure_absent"):
            # An amendment that simply omits the section is not a change.
            unsound.append((e, "amendment inferred from an absent section"))
    checks.append(_check(
        "Comparisons are like-for-like",
        OK if not (no_prior or unsound) else FAIL,
        f"{len(comparisons) - len(no_prior) - len(unsound)}/{len(comparisons)} "
        "compare the same form type against an earlier filing"
        + (f"; {len(unsound)} unsound" if unsound else "")))

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
    # "Starts with a capital" passed on "CORRESP 1 filename1.htm ...", because
    # a document header is a sentence start. The property that matters is that
    # the quote opens with the substance, so that is what is asserted.
    JUNK_OPENING = re.compile(
        r'^\s*(?:CORRESP|UPLOAD)\b'
        r'|^\s*\S*filename\d'
        r'|^\s*We\s+have\s+reviewed\s+your\s+filing'
        r'|^\s*(?:VIA|BY)\s+EDGAR\b',
        re.I)
    quoted = [e for e in events if e.quote]
    # A leading ellipsis is the deliberate case: the extractor found no
    # sentence boundary in reach (a long list-style note, or a table) and
    # marked the quote as an excerpt instead of silently starting mid-thought.
    ragged = [e for e in quoted
              if not re.match(r'^[A-Z“("•\u2026]', e.quote.strip())]
    # For a comment letter the property is positive: the quote has to open at a
    # comment. Listing header patterns to exclude is a blacklist, and the last
    # version of it accepted "January 15, 2026 By EDGAR Division of..." because
    # that is not a pattern I had thought of.
    COMMENT_OPENING = re.compile(
        r'^\s*(?:We\s+note|We\s+have\s+reviewed\s+your\s+response'
        r'|Please\s+(?:tell|revise|explain|advise)|In\s+your\s+response)', re.I)
    headed = [e for e in quoted
              if e.signal_type == COMMENT_LETTER and not COMMENT_OPENING.match(e.quote)]
    headed += [e for e in quoted
               if e.signal_type != COMMENT_LETTER and JUNK_OPENING.search(e.quote)]
    checks.append(_check(
        "Quotes open with the substance",
        OK if not (ragged or headed) else WARN,
        f"{len(ragged)} of {len(quoted)} begin mid-sentence, "
        f"{len(headed)} do not open at the substance"))

    # --- and where a quote is allowed to end ---
    # The opening was checked from the first week; the closing never was, and
    # ten quotes reached the page cut mid-word with nothing to mark the cut -
    # "...financial statements have been prepare". A quote either ends where
    # the filing's sentence ends, or it carries the ellipsis that says it was
    # trimmed. Anything else reads as a transcription error on a page whose
    # whole claim is that it quotes filings accurately.
    ENDS_CLEANLY = re.compile(r'[.!?;:"\u201d\u2019)\]\u2026]\s*$')
    cut_short = [e for e in quoted if not ENDS_CLEANLY.search(e.quote)]
    checks.append(_check(
        "Quotes end where the filing does",
        OK if not cut_short else WARN,
        f"{len(cut_short)} of {len(quoted)} end mid-sentence "
        f"with no ellipsis to mark the cut"))

    # --- does any entry contradict its own quoted evidence? ---
    # The defect that most damages the site is an event whose label says the
    # opposite of the passage printed underneath it. Nothing on the status page
    # would have caught the ChronoScale case, which published "substantial
    # doubt" over a quote saying doubt was not raised.
    CONTRADICTS = {
        GOING_CONCERN: (
            ("substantial_doubt",),
            re.compile(r"\b(?:does|do|did)\s+not\s+raise\s+substantial\s+doubt"
                       r"|substantial\s+doubt\s+(?:is|was)\s+not\s+raised"
                       r"|\bno\s+substantial\s+doubt\b", re.I)),
        MATERIAL_WEAKNESS: (
            ("material_weakness",),
            re.compile(r"concluded[^.]{0,80}?internal\s+control[^.]{0,60}?"
                       r"\b(?:was|were|is|are)\s+effective", re.I)),
    }
    contradictions = []
    for e in events:
        rule = CONTRADICTS.get(e.signal_type)
        if not rule or not e.quote:
            continue
        adverse_states, pattern = rule
        if e.evidence.get("current_state") in adverse_states and pattern.search(e.quote):
            contradictions.append(e)
    checks.append(_check(
        "No entry contradicts its own quote",
        OK if not contradictions else FAIL,
        f"{len(contradictions)} of {sum(1 for e in events if e.signal_type in CONTRADICTS)} "
        "going-concern and material-weakness entries quote a passage that "
        "negates the state they assert"))

    # --- are extracted auditor names actually firms? ---
    # The other checks are structural: they confirm a field is populated and
    # internally consistent. None of them could see that the Auditors page was
    # naming "that Simon & Edward LLP", a law firm, and one issuer as its own
    # auditor - the fields were present and the links resolved. This asserts
    # the shape of the value itself.
    from .auditor import BIG_FOUR, NATIONAL, firm_key

    # "EY", "BDO", "PwC" and "RSM" are the canonical labels this pipeline
    # assigns, so a bare length rule would condemn them.
    KNOWN_SHORT = set(BIG_FOUR) | set(NATIONAL)

    CONNECTIVE = re.compile(r"^(?:that|by|of|the|its|our|a|an|as|new|former)\b", re.I)
    LAW_FIRM = re.compile(r"Brisbois|Bisgaard|Skadden|Latham|Cooley|Sonsini", re.I)
    faults, checked, spellings = [], 0, defaultdict(set)
    for e in events:
        if e.signal_type != AUDITOR_CHANGE:
            continue
        for field in ("predecessor_auditor", "successor_auditor"):
            name = (e.evidence.get(field) or "").strip()
            if not name:
                continue
            checked += 1
            spellings[firm_key(name)].add(name)
            if CONNECTIVE.match(name):
                faults.append(f"{e.company}: {field} begins with a connective ({name!r})")
            elif LAW_FIRM.search(name):
                faults.append(f"{e.company}: {field} is a law firm ({name!r})")
            elif firm_key(name) and firm_key(name) == firm_key(e.company):
                faults.append(f"{e.company}: named as its own auditor")
            elif len(name) < 4 and name not in KNOWN_SHORT:
                faults.append(f"{e.company}: {field} too short to be a firm ({name!r})")
    # The movement table groups on firm_key, but the per-change list under it
    # printed whatever each filing said, so one firm appeared under three
    # spellings beneath a table counting it as one. Grouping correctly is not
    # enough if the page still shows the variants: assert that what is
    # PUBLISHED settles on one label per firm.
    # Variety in the STORED events is expected - filers spell a firm several
    # ways and the evidence keeps their wording. What must not vary is what
    # gets published, and that is asserted against the built page by
    # firm_labels_check, after rendering has chosen one label per firm.
    varied = sum(1 for v in spellings.values() if len(v) > 1)
    detail = (f"{checked} firm names across {len(spellings)} firms; "
              f"{varied} spelled more than one way by filers")
    checks.append(_check(
        "Auditor names are firm-shaped",
        OK if not faults else FAIL,
        detail if not faults else f"{len(faults)} malformed: {faults[0]}"))

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
    # A partial history is worse than none: the sequences page prints the rates
    # as percentages, so a run that only reached a handful of companies
    # publishes "0 of 6" beside a population of several hundred and reads as a
    # real finding. This happened twice, both times because a local run wrote
    # over the state the scheduled run had produced.
    hist_path = config.STATE_DIR / "history.json"
    if hist_path.exists():
        try:
            hist = json.loads(hist_path.read_text())
            n = hist.get("total_historical_events", 0)
            companies = hist.get("companies", 0)
            eligible = max((r.get("eligible", 0) for r in hist.get("rows", [])),
                           default=0)
            # Every company contributes at least the event that flagged it, so
            # a total below the company count means the refresh did not finish.
            if companies > 20 and (n < companies or eligible * 4 < companies):
                checks.append(_check(
                    "Follow-on rates computed", FAIL,
                    f"{companies} companies but only {n:,} historical events, "
                    f"at most {eligible} eligible - the history refresh did not "
                    "complete, so the published rates would come from a "
                    "fraction of the population"))
            else:
                checks.append(_check(
                    "Follow-on rates computed", OK if n else WARN,
                    f"{companies} companies, {n:,} historical events"))
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


def period_options_check(public: "pathlib.Path") -> Dict:
    """No filter offers a period the page cannot show.

    The period list was built from the whole record and handed to every page,
    while each page renders a different slice. The home page - a 150-entry
    window - offered "March 2025" and every other month it does not hold, and
    choosing one blanked the feed. Checked on the built pages rather than in
    the builder, because the defect was exactly a correct helper called with
    the wrong argument.
    """
    import re as _re

    pages = sorted(public.rglob("*.html"))
    if not pages:
        return _check("Filter options match the page", UNKNOWN, "nothing built yet")

    checked, dead = 0, []
    for page in pages:
        html_text = page.read_text()
        select = _re.search(r'<select id="period".*?</select>', html_text, _re.S)
        if not select:
            continue
        checked += 1
        offered = [v for v in _re.findall(r'<option value="([^"]+)"', select.group(0))
                   if v != "all"]
        # Every month any row on this page belongs to. A row can carry several.
        present = set()
        for attr in _re.findall(r'data-period="([^"]*)"', html_text):
            present.update(attr.split())
        for value in offered:
            if not any(m.startswith(value) for m in present):
                dead.append(f"{page.relative_to(public)}:{value}")

    detail = (f"{checked} pages with a period filter, "
              f"{len(dead)} option(s) that filter to nothing")
    if dead:
        return _check("Filter options match the page", FAIL,
                      detail + " - " + ", ".join(sorted(dead)[:6]))
    return _check("Filter options match the page", OK, detail)


def firm_labels_check(public: "pathlib.Path") -> Dict:
    """One firm, one label, on the page that was actually generated.

    The movement table grouped correctly while the per-change list beneath it
    printed each filing's own wording, so one firm appeared under three names
    under a table counting it as one. Checking the stored events cannot catch
    that - they are meant to vary - so this reads the built page.
    """
    import re as _re
    from html import unescape

    from .auditor import firm_key

    page = public / "auditors.html"
    if not page.exists():
        return _check("Firm labels are consistent", UNKNOWN, "auditors page not built")
    html_text = page.read_text()
    names = set()
    for cell in _re.findall(r"<td[^>]*>(.*?)</td>", html_text, _re.S):
        text = unescape(_re.sub(r"<[^>]+>", "", cell)).strip()
        if text and not text.replace("-", "").replace("+", "").isdigit() and text != "—":
            names.add(text)
    for part in _re.findall(r"<span class=\"muted\">(.*?)</span>", html_text, _re.S):
        for side in unescape(_re.sub(r"<[^>]+>", "", part)).split("→"):
            side = side.split("·")[0].strip()
            if side and side not in ("not stated", "firms not identified"):
                names.add(side)

    by_key: Dict[str, set] = defaultdict(set)
    for n in names:
        k = firm_key(n)
        if k:
            by_key[k].add(n)
    split = {k: sorted(v) for k, v in by_key.items() if len(v) > 1}
    if split:
        k, v = next(iter(split.items()))
        return _check("Firm labels are consistent", FAIL,
                      f"{len(split)} firm(s) shown under more than one name, "
                      f"e.g. {v}")
    return _check("Firm labels are consistent", OK,
                  f"{len(by_key)} firms on the auditors page, one label each")


def _summarise(checks: List[Dict]) -> Dict:
    counts = {OK: 0, WARN: 0, FAIL: 0, UNKNOWN: 0}
    for c in checks:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    overall = FAIL if counts[FAIL] else (WARN if counts[WARN] else OK)
    return {"overall": overall, **counts, "total": len(checks)}
