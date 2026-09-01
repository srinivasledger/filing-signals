"""Pipeline entry point.

Self-healing by construction: a run processes every business day from the last
successfully recorded date up to yesterday, rather than "today". A missed cron,
an SEC block, or a runner outage is therefore repaired by the next run instead
of leaving a permanent hole in the record.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import time
import sys
from typing import List, Optional

try:
    from zoneinfo import ZoneInfo
    EASTERN = ZoneInfo("America/New_York")
except Exception:                                # pragma: no cover
    EASTERN = dt.timezone(dt.timedelta(hours=-5))

from . import (analyze, compare, config, enrich, fetch, health, history, ingest,
               late, letters, publish, size, triage, universe)
from .models import Event

log = logging.getLogger("pipeline")

# Bound the work a single run can attempt, so an unusual day cannot stall CI.
MAX_PERIODIC_PER_DAY = int(__import__("os").getenv("MAX_PERIODIC_PER_DAY", "120"))


def business_days(start: dt.date, end: dt.date) -> List[dt.date]:
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += dt.timedelta(days=1)
    return days


# EDGAR publishes the day's index at about 22:00 ET, not at the 17:30 filing
# cutoff. Measured on two Last-Modified headers: form.20260828.idx at 02:02 UTC
# and form.20260831.idx at 02:03 UTC, both the following day - 22:02 and 22:03
# ET. Treating 19:00 as the close meant every run asked for an index that did
# not exist yet, got nothing, and left the day for the next run to backfill, so
# the site trailed current filings permanently.
EDGAR_CLOSE_HOUR_ET = 23


def last_complete_day(now_et: dt.datetime) -> dt.date:
    if now_et.hour >= EDGAR_CLOSE_HOUR_ET:
        return now_et.date()
    return now_et.date() - dt.timedelta(days=1)


def days_to_process(state: dict, today_et: dt.date,
                    end: Optional[dt.date] = None) -> List[dt.date]:
    """Every unprocessed business day through the last complete day, capped."""
    if end is None:
        end = today_et - dt.timedelta(days=1)
    last = state.get("last_processed")
    if last:
        start = dt.date.fromisoformat(last) + dt.timedelta(days=1)
    else:
        start = end - dt.timedelta(days=config.COLD_START_DAYS)
    if start > end:
        return []
    days = business_days(start, end)
    if len(days) > config.MAX_BACKFILL_DAYS:
        log.warning("%d days pending; processing the most recent %d",
                    len(days), config.MAX_BACKFILL_DAYS)
        days = days[-config.MAX_BACKFILL_DAYS:]
    return days


def process_day(day: dt.date) -> tuple:
    """Return (events, stats) for one business day."""
    stats = {"date": day.isoformat(), "index_rows": 0, "candidates": 0,
             "operating": 0, "events": 0}

    rows = ingest.fetch_day(day)
    if rows is None:
        return [], None                          # weekend/holiday: nothing to record
    stats["index_rows"] = len(rows)

    candidates = universe.filter_forms(rows)
    stats["candidates"] = len(candidates)
    log.info("%s: %d candidate filings from %d index rows",
             day, len(candidates), len(rows))

    readable = [f for f in candidates if enrich.enrich(f)]
    operating = universe.filter_operating(readable)
    stats["operating"] = len(operating)

    events: List[Event] = []

    # 8-K item codes: deterministic, cheap, high precision.
    for filing in operating:
        if filing.form.upper() in universe.EVENT_FORMS:
            events.extend(triage.events_from_filing(filing))

    # Late-filing notifications: one document read, no comparison needed.
    for filing in operating:
        if filing.form.upper() in universe.LATE_FORMS:
            try:
                events.extend(late.analyse_late_filing(filing))
            except fetch.SECBlocked:
                raise
            except Exception as exc:                 # noqa: BLE001
                log.warning("  late-filing parse failed for %s: %s", filing.company, exc)

    # SEC comment letters. Most review registration statements rather than
    # periodic reports, so the module filters hard; only a couple a week
    # survive, which is the point.
    for filing in operating:
        if filing.form.upper() in universe.LETTER_FORMS:
            try:
                events.extend(
                    letters.analyse_letter(filing, disclosed_on=day.isoformat()))
            except fetch.SECBlocked:
                raise
            except Exception as exc:             # noqa: BLE001
                log.warning("  letter parse failed for %s: %s", filing.company, exc)

    events = late.merge_same_day(events)

    # Periodic reports: needs the previous filing, so it is the expensive path.
    periodic = [f for f in operating if f.form.upper() in universe.PERIODIC_FORMS]
    if len(periodic) > MAX_PERIODIC_PER_DAY:
        log.warning("%s: %d periodic reports, capping at %d",
                    day, len(periodic), MAX_PERIODIC_PER_DAY)
        periodic = periodic[:MAX_PERIODIC_PER_DAY]
    for i, filing in enumerate(periodic, 1):
        log.info("  [%d/%d] comparing %s %s", i, len(periodic), filing.form, filing.company)
        try:
            events.extend(compare.analyse_periodic(filing))
        except fetch.SECBlocked:
            raise
        except Exception as exc:                 # noqa: BLE001
            log.warning("  comparison failed for %s: %s", filing.company, exc)

    stats["events"] = len(events)
    return events, stats


def days_to_backfill(state: dict, already: List[dt.date]) -> List[dt.date]:
    """Business days older than anything held, working back to HISTORY_FROM.

    Returns at most HISTORY_CHUNK, so a run's cost stays bounded whether it is
    filling in eight months or nothing at all.
    """
    if not config.HISTORY_FROM:
        return []
    try:
        floor = dt.date.fromisoformat(config.HISTORY_FROM)
    except ValueError:
        log.warning("HISTORY_FROM is not a date: %r", config.HISTORY_FROM)
        return []

    earliest = state.get("earliest_processed")
    known = [dt.date.fromisoformat(r["date"]) for r in state.get("runs", [])
             if r.get("date")]
    if earliest:
        known.append(dt.date.fromisoformat(earliest))
    known += already
    if not known:
        return []

    start = min(known) - dt.timedelta(days=1)
    if start < floor:
        return []
    days = business_days(floor, start)
    return days[-config.HISTORY_CHUNK:]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the filing signals pipeline.")
    parser.add_argument("--date", help="process a single YYYY-MM-DD instead of catching up")
    parser.add_argument("--days", type=int, help="process the last N business days")
    parser.add_argument("--no-render", action="store_true", help="skip site rendering")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(message)s",
    )
    config.require_user_agent()

    state = publish.load_state()
    now_et = dt.datetime.now(EASTERN)
    today_et = now_et.date()
    end = last_complete_day(now_et)

    if args.date:
        targets = [dt.date.fromisoformat(args.date)]
    elif args.days:
        targets = business_days(end - dt.timedelta(days=args.days * 2), end)[-args.days:]
    else:
        targets = days_to_process(state, today_et, end=end)

    # Catching up on today comes first; history fills whatever is left.
    filling = [] if (args.date or args.days) else days_to_backfill(state, targets)
    if filling:
        log.info("filling history: %d day(s) back to %s (floor %s)",
                 len(filling), filling[0], config.HISTORY_FROM)

    if not targets and not filling:
        log.info("nothing to process; already current through %s", state.get("last_processed"))
    elif targets:
        log.info("processing %d day(s): %s to %s", len(targets), targets[0], targets[-1])
    targets = targets + filling

    analyzer = analyze.get_analyzer()

    # Company size, from the SEC's own public-float test. Refreshed weekly and
    # cached, so a failed refresh leaves the previous index in place.
    try:
        float_by_cik = size.load_or_refresh(
            config.STATE_DIR / "company_size.json", today_et)
    except fetch.SECBlocked:
        log.warning("size index skipped: SEC access blocked")
        float_by_cik = {}
    except Exception as exc:                     # noqa: BLE001
        log.warning("size index unavailable (%s); continuing without", exc)
        float_by_cik = {}

    def tag_size(events):
        for e in events:
            val = float_by_cik.get(str(e.cik))
            if not val:
                continue
            try:
                tier, checked = size.verified_tier(e.cik, val)
            except fetch.SECBlocked:
                raise
            except Exception:                    # noqa: BLE001
                tier, checked = size.tier_for(val), val
            if tier and checked:
                e.size_tier, e.public_float = tier, checked
        return events
    total_new = 0
    blocked = False
    started_at = time.monotonic()
    filling_set = {d for d in filling}

    for day in targets:
        # Today's filings are never abandoned; only the history fill yields.
        if day in filling_set:
            spent = time.monotonic() - started_at
            if spent > config.HISTORY_BUDGET_SECONDS:
                left = sum(1 for d in filling_set if d >= day)
                log.warning("history fill stopping after %.0f min (budget %.0f min); "
                            "%d day(s) left for the next run",
                            spent / 60, config.HISTORY_BUDGET_SECONDS / 60, left)
                break
        try:
            events, stats = process_day(day)
        except fetch.SECBlocked as exc:
            # Expected failure mode, not a crash. Stop cleanly; the next run
            # picks up from the same place.
            log.error("SEC access blocked: %s", exc)
            log.error("stopping; the next run will resume from %s", day)
            blocked = True
            publish.record_run(state, {
                "date": day.isoformat(), "index_rows": 0, "candidates": 0,
                "operating": 0, "events": 0, "events_written": 0,
                "blocked": True, "analysis": "blocked",
            })
            break

        if stats is None:
            continue

        events = tag_size(events)
        if events:
            events = analyzer.enrich(events)
        written = publish.append_events(day.isoformat(), events)
        total_new += written
        stats["events_written"] = written
        stats["analysis"] = "claude" if analyzer.enabled else "deterministic-only"
        publish.record_run(state, stats)

        last = state.get("last_processed")
        if not last or day.isoformat() > last:
            state["last_processed"] = day.isoformat()
        # Only ever moves earlier, so a filled day is never re-fetched.
        first = state.get("earliest_processed")
        if not first or day.isoformat() < first:
            state["earliest_processed"] = day.isoformat()

        log.info("%s: %d new event(s)", day, written)

    publish.save_state(state)
    log.info("run complete: %d new event(s) recorded", total_new)

    # Follow-on rates across every company recorded so far. One submissions
    # request per company buys its full item-coded history, so this is cheap
    # even though it reaches back years.
    if not blocked:
        try:
            ciks = sorted({e.cik for e in publish.load_all_events()})
            if ciks:
                stats = history.sequence_rates(ciks)
                publish.save_history(stats)
                log.info("history: %d companies, %d historical events",
                         stats["companies"], stats["total_historical_events"])
        except fetch.SECBlocked:
            log.warning("history pass skipped: SEC access blocked")
        except Exception as exc:                 # noqa: BLE001
            log.warning("history pass failed: %s", exc)

    # Repair any file that arrived duplicated before the checks look at it.
    try:
        removed = publish.repair_all()
        if removed:
            log.warning("removed %d duplicated row(s) from the event log", removed)
    except Exception as exc:                     # noqa: BLE001
        log.warning("event log repair failed: %s", exc)

    # Self-checks last, so they see the finished state.
    report = {}
    try:
        report = health.run_checks(publish.load_all_events(), state, today_et)
        publish.save_health(report)
        s = report["summary"]
        log.info("health: %s (%d ok, %d warn, %d fail)",
                 s["overall"].upper(), s["ok"], s["warn"], s["fail"])
        for c in report["checks"]:
            if c["status"] in ("warn", "fail"):
                log.warning("  %s: %s — %s", c["status"].upper(), c["name"], c["detail"])
    except Exception as exc:                     # noqa: BLE001
        log.warning("health checks did not run: %s", exc)

    if not args.no_render:
        from . import render
        render.build()

    # What counts as a failed run.
    #
    # Not: an unpublished index, a refused request, or a quiet day. All three
    # are expected, self-healing, and already visible on the status page -
    # failing the job for them alarms the owner about something the design
    # handles, and skips deploying anything else that changed.
    #
    # Yes: output that is wrong. A failing self-check means an entry lacks its
    # citation, a comparison cannot name what it compared against, or a
    # re-run duplicated something. That is worth an email, because the site is
    # publishing something it should not.
    #
    # An unhandled exception exits non-zero on its own and still fails.
    failed = [c for c in (report or {}).get("checks", [])
              if c.get("status") == "fail"]
    if failed:
        for c in failed:
            log.error("integrity check failed: %s - %s", c["name"], c["detail"])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
