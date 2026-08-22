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

from . import config, publish
from .models import (AUDITOR_CHANGE, GOING_CONCERN, POLICY_CHANGE,
                     RESTATEMENT, REVENUE_RECOGNITION, SIGNAL_BLURBS,
                     SIGNAL_LABELS, Event)

log = logging.getLogger(__name__)

SIGNAL_ORDER = [
    RESTATEMENT, AUDITOR_CHANGE, GOING_CONCERN, POLICY_CHANGE, REVENUE_RECOGNITION,
]
MAX_HOME_EVENTS = 300


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(config.TEMPLATES)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


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

    common = {
        "site_title": config.SITE_TITLE,
        "site_tagline": config.SITE_TAGLINE,
        "built_at": built_at,
        "signal_labels": [(k, SIGNAL_LABELS[k]) for k in SIGNAL_ORDER],
        "blurbs": SIGNAL_BLURBS,
    }

    # --- home ---
    home_events = events[:MAX_HOME_EVENTS]
    _write(
        config.PUBLIC / "index.html",
        env.get_template("index.html").render(
            rel="", events=home_events,
            total_events=len(events),
            companies=len({e.cik for e in events}),
            days_covered=len({e.filed for e in events}),
            last_run=state.get("last_processed"),
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
    for cik, evs in by_company.items():
        newest = evs[0]
        _write(
            config.PUBLIC / "company" / f"{cik}.html",
            company_tpl.render(
                rel="../", cik=cik, company=newest.company,
                ticker=next((e.ticker for e in evs if e.ticker), ""),
                sic_desc=next((e.sic_desc for e in evs if e.sic_desc), ""),
                events=evs, **common,
            ),
        )

    # --- static pages ---
    _write(config.PUBLIC / "methodology.html",
           env.get_template("methodology.html").render(rel="", **common))
    _write(config.PUBLIC / "status.html",
           env.get_template("status.html").render(
               rel="", runs=runs, last_run=state.get("last_processed"),
               analysis_on=analysis_on, **common))

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

    log.info("site built: %d events, %d companies -> %s",
             len(events), len(by_company), config.PUBLIC)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    build()
