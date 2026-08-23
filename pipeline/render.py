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

from . import charts, config, publish, size as size_mod
from .models import (AUDITOR_CHANGE, GOING_CONCERN, LATE_FILING, POLICY_CHANGE,
                     RESTATEMENT, REVENUE_RECOGNITION, SIGNAL_BLURBS,
                     SIGNAL_LABELS, Event)

log = logging.getLogger(__name__)

SIGNAL_ORDER = [
    RESTATEMENT, AUDITOR_CHANGE, LATE_FILING, GOING_CONCERN, POLICY_CHANGE,
    REVENUE_RECOGNITION,
]
# The home page holds a recent window, not the whole record. At ~31 events a
# weekday the full set outgrows a single page quickly, and a 600KB page is
# already large. What matters is that the truncation is stated rather than
# silent: a filter applied to a quietly-truncated set gives wrong answers.
MAX_HOME_EVENTS = 400

# A company reaching several of these signals in sequence is the thing a raw
# EDGAR feed cannot show. Ordered by how far along the progression they sit.
PROGRESSION_ORDER = [LATE_FILING, AUDITOR_CHANGE, GOING_CONCERN,
                     POLICY_CHANGE, REVENUE_RECOGNITION, RESTATEMENT]


# Late filings are far more numerous than the rest - a single quarter-end day
# produced 122 of them - so a purely chronological feed buries the rare events
# under routine ones. Within a day, rank by how unusual the signal is.
_SIGNAL_WEIGHT = {
    RESTATEMENT: 0, AUDITOR_CHANGE: 1, GOING_CONCERN: 2,
    POLICY_CHANGE: 3, REVENUE_RECOGNITION: 4, LATE_FILING: 5,
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


def _company_sequence(events):
    """Distinct signals for one company, oldest first, as a progression."""
    seen, ordered = set(), []
    for e in sorted(events, key=lambda x: x.filed):
        if e.signal_type not in seen:
            seen.add(e.signal_type)
            ordered.append(e)
    return ordered if len(ordered) > 1 else []


def _auditor_stats(events):
    """Which audit firms appear across the flagged population, and how."""
    leaving, arriving, downgrades = Counter(), Counter(), Counter()
    for e in events:
        ev = e.evidence
        if ev.get("predecessor_auditor"):
            leaving[ev["predecessor_auditor"]] += 1
        if ev.get("successor_auditor"):
            arriving[ev["successor_auditor"]] += 1
        if ev.get("tier_downgrade") and ev.get("predecessor_auditor"):
            downgrades[ev["predecessor_auditor"]] += 1
    firms = sorted(set(leaving) | set(arriving),
                   key=lambda f: -(leaving[f] + arriving[f]))
    return [{"firm": f, "left": leaving[f], "joined": arriving[f],
             "net": arriving[f] - leaving[f], "downgrades_from": downgrades[f]}
            for f in firms]


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(config.TEMPLATES)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["floatformat"] = size_mod.format_float
    return env


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


def build() -> None:
    events = publish.load_all_events()
    state = publish.load_state()
    env = _env()
    built_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    config.PUBLIC.mkdir(parents=True, exist_ok=True)
    static_out = config.PUBLIC / "static"
    if static_out.exists():
        shutil.rmtree(static_out)
    shutil.copytree(config.STATIC, static_out)

    runs = list(reversed(state.get("runs", [])))
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
        "status_state": status_state,
        "status_label": status_label,
        "site_title": config.SITE_TITLE,
        "repo_url": config.REPO_URL,
        "site_tagline": config.SITE_TAGLINE,
        "built_at": built_at,
        "signal_labels": [(k, SIGNAL_LABELS[k]) for k in SIGNAL_ORDER],
        "blurbs": SIGNAL_BLURBS,
    }

    scanned, candidates = _scan_totals(state.get("runs", []))
    flag_rate = (f"{len(events) / candidates * 100:.1f}%"
                 if candidates else "—")

    # --- home ---
    ranked = sorted(events, key=_rank, reverse=True)
    home_events = ranked[:MAX_HOME_EVENTS]
    truncated = len(events) - len(home_events)
    _write(
        config.PUBLIC / "index.html",
        env.get_template("index.html").render(
            rel="", events=home_events,
            total_events=len(events),
            companies=len({e.cik for e in events}),
            days_covered=len({e.filed for e in events}),
            last_run=state.get("last_processed"),
            filings_scanned=scanned, candidates_scanned=candidates,
            flag_rate=flag_rate, truncated=truncated,
            shown_events=len(home_events), activity_chart=activity_svg,
            activity_data=activity_data, mix_bar=mix_svg,
            **common,
        ),
    )

    # --- signals overview ---
    by_signal: Dict[str, List[Event]] = defaultdict(list)
    for e in events:
        by_signal[e.signal_type].append(e)
    _write(
        config.PUBLIC / "signals.html",
        env.get_template("signals.html").render(
            rel="", by_signal=by_signal,
            counts=Counter(e.signal_type for e in events), **common,
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
                              "steps": seq})
        _write(
            config.PUBLIC / "company" / f"{cik}.html",
            company_tpl.render(
                rel="../", cik=cik, company=newest.company,
                ticker=next((e.ticker for e in evs if e.ticker), ""),
                sic_desc=next((e.sic_desc for e in evs if e.sic_desc), ""),
                events=sorted(evs, key=_rank, reverse=True), sequence=seq, **common,
            ),
        )
    sequences.sort(key=lambda s: (-len(s["steps"]), s["company"]))

    # --- sequences + auditor concentration ---
    _write(config.PUBLIC / "sequences.html",
           env.get_template("sequences.html").render(
               rel="", sequences=sequences, history=publish.load_history(),
               rates_chart=charts.rates_chart(
                   publish.load_history().get("rows", [])),
               **common))
    _write(config.PUBLIC / "auditors.html",
           env.get_template("auditors.html").render(
               rel="", firms=_auditor_stats(events),
               changes=[e for e in events if e.signal_type == AUDITOR_CHANGE],
               **common))

    # --- static pages ---
    _write(config.PUBLIC / "methodology.html",
           env.get_template("methodology.html").render(
               rel="", filings_scanned=scanned, candidates_scanned=candidates,
               total_events=len(events), flag_rate=flag_rate, **common))
    _write(config.PUBLIC / "status.html",
           env.get_template("status.html").render(
               rel="", runs=runs, last_run=state.get("last_processed"),
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    build()
