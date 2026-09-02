"""Render the static site from recorded events.

Pure function of what is on disk: no network, no API. Rendering is separated
from collection so the site can always be rebuilt, and so a rendering change
never risks the data.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import shutil
from collections import Counter, defaultdict
from html import escape
from typing import Dict, List
from xml.sax.saxutils import escape as xml_escape

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import charts, config, health as health_mod, preview, publish, size as size_mod
from .models import (AUDITOR_CHANGE, COMMENT_LETTER, GOING_CONCERN, LATE_FILING,
                     MATERIAL_WEAKNESS, OFFICER_DEPARTURE, POLICY_CHANGE,
                     RESTATEMENT, REVENUE_RECOGNITION, SIGNAL_BLURBS,
                     SIGNAL_LABELS, Event, mid_sentence)

log = logging.getLogger(__name__)

SIGNAL_ORDER = [
    RESTATEMENT, COMMENT_LETTER, MATERIAL_WEAKNESS, AUDITOR_CHANGE,
    OFFICER_DEPARTURE, GOING_CONCERN, POLICY_CHANGE, REVENUE_RECOGNITION,
    LATE_FILING,
]
# The home page holds a recent window, not the whole record. Pages are served
# gzipped, so bytes are not the constraint - 344 full cards are 851 KB on disk
# but 79 KB over the wire. The cost that matters is the DOM: every card is a
# dozen nodes the phone must parse, lay out and paint. 150 is about five filing
# days, which is what a "latest" page should be; the complete record lives on
# the signals pages, where an entry is a link rather than a card.
#
# What matters most is that the truncation is stated rather than silent: a
# filter applied to a quietly-truncated set gives wrong answers.
MAX_HOME_EVENTS = 150

# How many of each signal the overview previews before linking to the full page.
SIGNAL_PREVIEW = 12
# The letters page leads with topic counts drawn from every letter; the cards
# beneath it are a window, since a card costs ~15x what a list row does. The
# sequences page is bounded the same way. Both name the full list.
LETTER_CARDS = 60
MAX_SEQUENCES = 120

# A company reaching several of these signals in sequence is the thing a raw
# EDGAR feed cannot show. Ordered by how far along the progression they sit.
PROGRESSION_ORDER = [LATE_FILING, AUDITOR_CHANGE, GOING_CONCERN,
                     POLICY_CHANGE, REVENUE_RECOGNITION, RESTATEMENT]


# Late filings are far more numerous than the rest - a single quarter-end day
# produced 122 of them - so a purely chronological feed buries the rare events
# under routine ones. Within a day, rank by how unusual the signal is.
_SIGNAL_WEIGHT = {
    RESTATEMENT: 0, COMMENT_LETTER: 1, MATERIAL_WEAKNESS: 2, AUDITOR_CHANGE: 3,
    OFFICER_DEPARTURE: 4, GOING_CONCERN: 5, POLICY_CHANGE: 6,
    REVENUE_RECOGNITION: 7, LATE_FILING: 8,
}


def _rank(event) -> tuple:
    """Sort key: newest first, then elevated, then by signal rarity."""
    elevated = 0 if event.evidence.get("severity") == "high" else 1
    return (event.filed, -elevated, -_SIGNAL_WEIGHT.get(event.signal_type, 9),
            event.company)


def _scan_totals(runs):
    """Denominator for the flag rate. Counts each filing day once, since a day
    may be re-run and would otherwise be double counted."""
    per_day = {}
    for r in runs:
        day = r.get("date")
        if not day:
            continue
        prev = per_day.get(day, {})
        per_day[day] = {
            "index_rows": max(prev.get("index_rows", 0), r.get("index_rows") or 0),
            "candidates": max(prev.get("candidates", 0), r.get("candidates") or 0),
        }
    return (sum(v["index_rows"] for v in per_day.values()),
            sum(v["candidates"] for v in per_day.values()))


# What each notice defers, and how long Rule 12b-25 allows for it. The windows
# are widened slightly over the statutory five and fifteen days, because the
# extension runs in calendar days and filings land on business days.
_EXTENSION_WINDOW = {
    "NT 10-Q": ({"10-Q"}, 10),
    "NT 10-K": ({"10-K"}, 25),
    "NT 20-F": ({"20-F"}, 25),
}


def _is_extension_of(event, later) -> bool:
    """True when this late notice is answered by the report it deferred.

    The pair is one situation. Reporting it as a progression asserts a
    sequence that the filing calendar produced, not the company.
    """
    import datetime as _dt

    window = _EXTENSION_WINDOW.get(event.form.upper().rstrip("/A"))
    if not window:
        return False
    forms, days = window
    for other in later:
        if other.form.upper().rstrip("/A") not in forms:
            continue
        try:
            gap = (_dt.date.fromisoformat(other.filed)
                   - _dt.date.fromisoformat(event.filed)).days
        except ValueError:
            continue
        if 0 <= gap <= days:
            return True
    return False


def _company_sequence(events):
    """Distinct signals for one company, oldest first, with the gap between.

    Order alone understates what a progression means: a late filing followed
    by an auditor change six weeks later reads very differently from the same
    pair eighteen months apart.
    """
    import datetime as _dt

    seen, ordered = set(), []
    for e in sorted(events, key=lambda x: x.filed):
        if e.signal_type not in seen:
            seen.add(e.signal_type)
            ordered.append(e)

    # One filing is one occasion, however many signals it carries. Two events
    # read out of the same accession were being drawn as a progression with a
    # nought-day gap between them.
    by_filing, collapsed = set(), []
    for e in ordered:
        if e.accession in by_filing:
            continue
        by_filing.add(e.accession)
        collapsed.append(e)
    ordered = collapsed

    # A Form 12b-25 followed by the report it covered is Rule 12b-25 working,
    # not a company deteriorating: the rule grants five calendar days for a
    # 10-Q and fifteen for a 10-K, and the going-concern note the tracker then
    # reports comes from that very report. It is one reporting event split
    # across two filings, and it accounted for 28 of 48 chains on this page.
    ordered = [e for i, e in enumerate(ordered)
               if not _is_extension_of(e, ordered[i + 1:])]

    if len(ordered) < 2:
        return []

    steps = []
    previous = None
    for e in ordered:
        gap = None
        if previous is not None:
            try:
                gap = (_dt.date.fromisoformat(e.filed)
                       - _dt.date.fromisoformat(previous)).days
            except ValueError:
                gap = None
        steps.append({"event": e, "days_since_previous": gap})
        previous = e.filed
    steps[0]["span_days"] = None
    try:
        steps[0]["span_days"] = (_dt.date.fromisoformat(ordered[-1].filed)
                                 - _dt.date.fromisoformat(ordered[0].filed)).days
    except ValueError:
        pass
    return steps


def _letter_stats(events):
    """What the SEC staff is asking about, and how old the letters are.

    Aggregated by topic because that is the view nobody publishes: EDGAR shows
    letters one filing at a time. The lag is reported alongside because it is
    large and changes how the whole page should be read.
    """
    import datetime as _dt

    letters = [e for e in events if e.signal_type == COMMENT_LETTER]
    if not letters:
        return {}

    topics = Counter(t for e in letters for t in e.evidence.get("topics", []))
    lags = []
    for e in letters:
        dated = e.evidence.get("letter_dated")
        if not dated:
            continue
        try:
            lags.append((_dt.date.fromisoformat(e.filed)
                         - _dt.date.fromisoformat(dated)).days)
        except ValueError:
            continue
    lags.sort()

    # One review is one conversation. EDGAR shows each letter separately, so
    # Stanley Black & Decker's single review appeared as eight cards saying
    # much the same thing. Threaded on the company plus the filing under
    # review, which the "Re:" line already gives us.
    threads: Dict[tuple, List[Event]] = defaultdict(list)
    for e in letters:
        threads[(e.cik, (e.evidence.get("reviewing") or "")[:60])].append(e)
    threaded = []
    for (cik, subject), rows in threads.items():
        rows.sort(key=lambda e: e.filed)
        threaded.append({
            "cik": cik,
            "company": rows[-1].company,
            "ticker": next((e.ticker for e in rows if e.ticker), ""),
            "subject": subject,
            "letters": rows,
            "opened": rows[0].filed,
            "closed": rows[-1].filed,
            "from_staff": sum(1 for e in rows
                              if e.evidence.get("direction") == "staff to company"),
            "formerly": next((e.evidence.get("formerly") for e in rows
                              if e.evidence.get("formerly")), ""),
            "topics": sorted({t for e in rows for t in e.evidence.get("topics", [])}),
            "size_tier": next((e.size_tier for e in rows if e.size_tier), ""),
        })
    threaded.sort(key=lambda t: t["closed"], reverse=True)

    return {
        "letters": sorted(letters, key=lambda e: (e.filed, e.company), reverse=True),
        "threads": threaded,
        "reviews": len(threaded),
        "count": len(letters),
        "companies": len({e.cik for e in letters}),
        "topics": topics.most_common(),
        "topic_max": max(topics.values()) if topics else 1,
        "from_staff": sum(1 for e in letters
                          if e.evidence.get("direction") == "staff to company"),
        "median_lag": lags[len(lags) // 2] if lags else None,
        "min_lag": lags[0] if lags else None,
        "max_lag": lags[-1] if lags else None,
    }


def _fill_boundary(runs, row) -> str:
    """The filing day a run would have processed had it been catching up.

    A run started on day D processes D or the business day before it. Anything
    materially older than that is the history fill working backwards.
    """
    import datetime as _dt
    finished = (row.get("finished_at") or "")[:10]
    if not finished:
        return ""
    try:
        return (_dt.date.fromisoformat(finished) - _dt.timedelta(days=5)).isoformat()
    except ValueError:
        return ""


def _periods(events):
    """Years and the months present within them, newest first.

    Built from the data rather than a fixed calendar, so a month with nothing
    in it never appears as an option that filters to an empty page.
    """
    import calendar

    months = sorted({e.filed[:7] for e in events if e.filed}, reverse=True)
    by_year: Dict[str, list] = defaultdict(list)
    for ym in months:
        year, mon = ym.split("-")
        by_year[year].append((ym, f"{calendar.month_name[int(mon)]} {year}"))
    return [(y, by_year[y]) for y in sorted(by_year, reverse=True)]


def _auditor_stats(events):
    """Which audit firms appear across the flagged population, and how.

    Grouped on firm_key rather than the displayed name. Filers spell the same
    firm several ways - "GreenGrowth CPAs", "Green Growth CPAs" and
    "GreenGrowth CPA" were three separate rows - and a movement table that
    splits one firm across three lines is worse than no table.
    """
    from .auditor import firm_key

    leaving, arriving, downgrades = Counter(), Counter(), Counter()
    display: Dict[str, Counter] = defaultdict(Counter)
    for e in events:
        ev = e.evidence
        for field, bucket in (("predecessor_auditor", leaving),
                              ("successor_auditor", arriving)):
            name = ev.get(field)
            if not name:
                continue
            key = firm_key(name)
            bucket[key] += 1
            display[key][name] += 1
        if ev.get("tier_downgrade") and ev.get("predecessor_auditor"):
            downgrades[firm_key(ev["predecessor_auditor"])] += 1

    keys = sorted(set(leaving) | set(arriving),
                  key=lambda k: -(leaving[k] + arriving[k]))
    # Show the spelling the filers used most often for that firm.
    return [{"firm": display[k].most_common(1)[0][0],
             "left": leaving[k], "joined": arriving[k],
             "net": arriving[k] - leaving[k], "downgrades_from": downgrades[k]}
            for k in keys]


# Evidence values are Python objects. Rendered raw they reached the page as
# "['2024-04', '2025-11']", "True" and "None".
_EVIDENCE_HIDDEN = {
    "why", "source", "severity", "stated_reason", "new_language", "contexts",
    "limb_label", "limb", "limb_basis", "direction_label", "caveat",
    "current_state", "prior_state", "comparable",
}


def _evidence_value(value):
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    if value is None:
        return "\u2014"
    return value


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(config.TEMPLATES)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["floatformat"] = size_mod.format_float
    # One rule for dropping a display label into a sentence, shared with the
    # letter headlines. Lowercasing wholesale ate the acronym in both places.
    env.filters["mid_sentence"] = mid_sentence
    env.filters["evidence"] = _evidence_value
    return env


def asset_versions() -> Dict[str, str]:
    """Short content hashes for the static assets.

    GitHub Pages caches these aggressively. A broken filter.js stayed live in
    browsers that had already loaded it even after the fix deployed, because
    the URL had not changed. Hashing the content means a changed file is a
    changed URL, and an unchanged one still caches.
    """
    import hashlib

    out: Dict[str, str] = {}
    for path in sorted(config.STATIC.glob("*")):
        if path.is_file():
            digest = hashlib.sha1(path.read_bytes()).hexdigest()[:8]
            out[path.name] = digest
    return out


def _write(path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _feed_xml(events: List[Event], built_at: str) -> str:
    base = config.SITE_URL or ""
    items = []
    for e in events[:60]:
        desc = (e.ai.get("summary") if e.ai else "") or e.quote or e.headline
        link = e.filing_url
        items.append(
            "<item>"
            f"<title>{xml_escape(e.headline)}</title>"
            f"<link>{xml_escape(link)}</link>"
            f"<guid isPermaLink=\"false\">{e.id}</guid>"
            f"<category>{xml_escape(e.label)}</category>"
            f"<pubDate>{e.filed}</pubDate>"
            f"<description>{xml_escape(desc)}</description>"
            "</item>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>'
        f"<title>{xml_escape(config.SITE_TITLE)}</title>"
        f"<link>{xml_escape(base or 'https://example.invalid')}</link>"
        f"<description>{xml_escape(config.SITE_TAGLINE)}</description>"
        f"<lastBuildDate>{built_at}</lastBuildDate>"
        + "".join(items)
        + "</channel></rss>\n"
    )


def build(second_pass: bool = False) -> None:
    events = publish.load_all_events()
    state = publish.load_state()
    env = _env()
    built_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    config.PUBLIC.mkdir(parents=True, exist_ok=True)
    static_out = config.PUBLIC / "static"
    if static_out.exists():
        shutil.rmtree(static_out)
    shutil.copytree(config.STATIC, static_out)

    # Ordered by when each run actually executed, newest first. Reversing the
    # append order interleaved the nightly scan with the history fill, which
    # walks backwards - so the table jumped between last night's filings and
    # filings from months ago with no visible reason. Each row is marked with
    # which of the two it was.
    runs = sorted(state.get("runs", []),
                  key=lambda r: (r.get("finished_at") or "", r.get("date") or ""),
                  reverse=True)
    _newest_day = max((r.get("date") or "" for r in runs), default="")
    for r in runs:
        # A run whose filing day is well behind the record is the fill.
        r["kind"] = ("fill" if r.get("date", "") < _fill_boundary(runs, r)
                     else "scan")
    analysis_on = any(r.get("analysis") == "claude" for r in runs[:5])

    activity_svg = charts.activity_chart(events)
    activity_data = charts.chart_data(events)
    mix_svg = charts.mix_bar(events)

    health = publish.load_health()
    summary = health.get("summary") or {}
    if not summary:
        status_state, status_label = "unknown", "Status unknown"
    elif summary.get("fail"):
        n = summary["fail"]
        status_state = "fail"
        status_label = f"{n} check{'s' if n > 1 else ''} failing"
    elif summary.get("warn"):
        n = summary["warn"]
        status_state = "warn"
        status_label = f"{n} warning{'s' if n > 1 else ''}"
    else:
        status_state = "ok"
        status_label = f"All {summary.get('total', 0)} checks passing"

    common = {
        "asset_v": asset_versions(),
        "og_description": (
            f"{len(events):,} events found in SEC filings and published "
            "automatically each weekday: restatements, SEC comment letters, "
            "material weaknesses, auditor and CFO changes, late filings and "
            "going-concern transitions."),
        "status_state": status_state,
        "status_label": status_label,
        "site_title": config.SITE_TITLE,
        "site_url": config.SITE_URL,
        "repo_url": config.REPO_URL,
        "site_tagline": config.SITE_TAGLINE,
        "built_at": built_at,
        "periods": _periods(events),
        "signal_labels": [(k, SIGNAL_LABELS[k]) for k in SIGNAL_ORDER],
        "hidden_evidence": _EVIDENCE_HIDDEN,
        "blurbs": SIGNAL_BLURBS,
    }

    situations = len({(e.cik, e.filed) for e in events})
    routine_n = sum(1 for e in events if e.routine)
    scanned, candidates = _scan_totals(state.get("runs", []))
    flag_rate = (f"{len(events) / candidates * 100:.1f}%"
                 if candidates else "—")

    # The link-preview card, drawn with the live figures. After flag_rate,
    # which it puts on the card.
    try:
        preview.build({
            "events": len(events),
            "companies": len({e.cik for e in events}),
            "days": len({e.filed for e in events}),
            "flag_rate": flag_rate,
            "through": state.get("last_processed") or "",
        })
    except Exception as exc:                     # noqa: BLE001
        log.warning("link-preview card not generated: %s", exc)

    # --- home ---
    ranked = sorted(events, key=_rank, reverse=True)
    # One registrant, one name. A company that renames itself keeps filing
    # under both for a while, so CIK 1130713 appeared as "BED BATH & BEYOND"
    # and "NEIGHBORHOOD INTELLIGENCE" on the same day, and every per-company
    # count risked splitting in two. The newest filing carries the current
    # name, and the earlier one is kept as context rather than discarded.
    # One firm, one spelling, on every surface. The movement table already
    # grouped on firm_key, but the per-change list below it printed whatever
    # each filing said - so the same firm appeared as "Ham, Langston and
    # Brezina, LLP", "Ham, Langston & Brezina, LLP" and "Ham, Langston &
    # Brezina, L.L.P" beneath a table counting them as one. The filer's exact
    # wording stays in the evidence; the label shown is the commonest spelling.
    from .auditor import firm_key

    _firm_spellings: Dict[str, Counter] = defaultdict(Counter)
    for e in events:
        for field in ("predecessor_auditor", "successor_auditor"):
            name = e.evidence.get(field)
            if name:
                _firm_spellings[firm_key(name)][name] += 1
    _firm_label = {k: c.most_common(1)[0][0] for k, c in _firm_spellings.items()}
    for e in events:
        for field in ("predecessor_auditor", "successor_auditor"):
            name = e.evidence.get(field)
            if not name:
                continue
            label = _firm_label.get(firm_key(name))
            if label and label != name:
                e.evidence.setdefault(field + "_as_filed", name)
                e.evidence[field] = label

    _newest_name: Dict[int, str] = {}
    for e in sorted(events, key=lambda x: x.filed):
        _newest_name[e.cik] = e.company
    for e in events:
        current = _newest_name.get(e.cik)
        if current and e.company != current:
            e.evidence.setdefault("formerly", e.company)
            e.company = current

    home_events = ranked[:MAX_HOME_EVENTS]
    truncated = len(events) - len(home_events)
    _write(
        config.PUBLIC / "index.html",
        env.get_template("index.html").render(
            rel="", page_path="", events=home_events,
            total_events=len(events),
            companies=len({e.cik for e in events}),
            situations=situations, routine_n=routine_n,
            days_covered=len({e.filed for e in events}),
            last_run=state.get("last_processed"),
            filings_scanned=scanned, candidates_scanned=candidates,
            flag_rate=flag_rate, truncated=truncated,
            shown_events=len(home_events), activity_chart=activity_svg,
            activity_data=activity_data, mix_bar=mix_svg,
            **common,
        ),
    )

    # --- signals overview, and one complete page per signal ---
    #
    # The overview used to carry every entry ever recorded, which grows without
    # bound. Splitting per signal keeps each page to roughly a ninth of the
    # record, and grouping by year inside it means a page only grows for as
    # long as a year lasts. The overview keeps a preview of each so it stays
    # the useful landing page.
    by_signal: Dict[str, List[Event]] = defaultdict(list)
    for e in events:
        by_signal[e.signal_type].append(e)

    (config.PUBLIC / "signals").mkdir(parents=True, exist_ok=True)
    signal_tpl = env.get_template("signal.html")
    for key in SIGNAL_ORDER:
        label = SIGNAL_LABELS[key]
        rows = by_signal.get(key) or []
        if not rows:
            continue
        by_year: Dict[str, List[Event]] = defaultdict(list)
        for e in rows:
            by_year[e.filed[:4]].append(e)
        _write(
            config.PUBLIC / "signals" / f"{key}.html",
            signal_tpl.render(
                rel="../", page_path=f"signals/{key}.html", key=key, label=label,
                blurb=SIGNAL_BLURBS.get(key, ""), total=len(rows),
                years=sorted(by_year.items(), reverse=True), **common,
            ),
        )

    _write(
        config.PUBLIC / "signals.html",
        env.get_template("signals.html").render(
            rel="", page_path="signals.html",
            by_signal={k: v[:SIGNAL_PREVIEW] for k, v in by_signal.items()},
            counts=Counter(e.signal_type for e in events),
            preview=SIGNAL_PREVIEW, **common,
        ),
    )

    # --- per company ---
    by_company: Dict[int, List[Event]] = defaultdict(list)
    for e in events:
        by_company[e.cik].append(e)
    company_tpl = env.get_template("company.html")
    sequences = []
    for cik, evs in by_company.items():
        newest = evs[0]
        seq = _company_sequence(evs)
        if seq:
            sequences.append({"cik": cik, "company": newest.company,
                              "ticker": next((e.ticker for e in evs if e.ticker), ""),
                              "size_tier": next((e.size_tier for e in evs if e.size_tier), ""),
                              "span_days": seq[0].get("span_days"),
                              "steps": seq})
        _write(
            config.PUBLIC / "company" / f"{cik}.html",
            company_tpl.render(
                rel="../", page_path=f"company/{cik}.html", cik=cik, company=newest.company,
                ticker=next((e.ticker for e in evs if e.ticker), ""),
                sic_desc=next((e.sic_desc for e in evs if e.sic_desc), ""),
                events=sorted(evs, key=_rank, reverse=True), sequence=seq, **common,
            ),
        )
    sequences.sort(key=lambda s: (-len(s["steps"]), s["company"]))

    # --- sequences + auditor concentration ---
    _write(config.PUBLIC / "sequences.html",
           env.get_template("sequences.html").render(
               rel="", page_path="sequences.html",
               sequences=sequences[:MAX_SEQUENCES],
               sequences_total=len(sequences), history=publish.load_history(),
               rates_chart=charts.rates_chart(
                   publish.load_history().get("rows", [])),
               **common))
    letter_stats = _letter_stats(events)
    if letter_stats:
        letter_stats["shown"] = letter_stats["threads"][:LETTER_CARDS]
    if letter_stats:
        _write(config.PUBLIC / "letters.html",
               env.get_template("letters.html").render(
                   rel="", page_path="letters.html", **letter_stats, **common))

    _write(config.PUBLIC / "auditors.html",
           env.get_template("auditors.html").render(
               rel="", page_path="auditors.html", firms=_auditor_stats(events),
               changes=[e for e in events if e.signal_type == AUDITOR_CHANGE],
               **common))

    # --- static pages ---
    _write(config.PUBLIC / "methodology.html",
           env.get_template("methodology.html").render(
               rel="", page_path="methodology.html",
               # Computed once and passed to both pages that state it. The
               # methodology page said "about eleven months" in prose while the
               # letters page computed 261 days from the same data.
               letter_median_lag=(letter_stats or {}).get("median_lag"),
               filings_scanned=scanned, candidates_scanned=candidates,
               total_events=len(events), flag_rate=flag_rate, **common))
    _write(config.PUBLIC / "status.html",
           env.get_template("status.html").render(
               rel="", page_path="status.html", runs=runs, last_run=state.get("last_processed"),
               analysis_on=analysis_on, health=health, **common))

    # --- machine-readable ---
    _write(config.PUBLIC / "events.json",
           json.dumps({
               "generated_at": built_at,
               "last_filing_day": state.get("last_processed"),
               "count": len(events),
               "events": [e.to_dict() for e in events],
           }, indent=2))
    _write(config.PUBLIC / "feed.xml", _feed_xml(events, built_at))
    (config.PUBLIC / ".nojekyll").write_text("")
    if config.CUSTOM_DOMAIN:
        # Rewritten on every deploy; see config.CUSTOM_DOMAIN for why.
        (config.PUBLIC / "CNAME").write_text(config.CUSTOM_DOMAIN + "\n")

    log.info("site built: %d events, %d companies -> %s",
             len(events), len(by_company), config.PUBLIC)

    # Page weight can only be measured once the pages exist, but its result
    # shows in the header of every page. So it is measured after a full build
    # and, if that changed the report, everything is rendered once more. The
    # alternative - reporting it only on the status page - leaves the header
    # counting a different number of checks from the list underneath it.
    if not second_pass:
        try:
            built = [health_mod.page_weight_check(config.PUBLIC),
                     health_mod.firm_labels_check(config.PUBLIC)]
            for c in built:
                log.info("%s", c["detail"])
            names = {c["name"] for c in built}
            checks = [c for c in health.get("checks", [])
                      if c["name"] not in names] + built
            if checks != health.get("checks"):
                health["checks"] = checks
                health["summary"] = health_mod._summarise(checks)
                publish.save_health(health)
                build(second_pass=True)
        except Exception as exc:                 # noqa: BLE001
            log.warning("page weight check did not run: %s", exc)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    build()
