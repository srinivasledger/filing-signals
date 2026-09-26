"""Persistence. Plain text on disk, versioned by git - no database.

Events are append-only JSONL partitioned by filing date. State is a small JSON
file. Both diff cleanly in git, which matters because the workflow commits data
on every run: a binary database rewritten daily would add its full size to the
repository every single day.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from . import config
from .models import Event

log = logging.getLogger(__name__)

STATE_FILE = config.STATE_DIR / "pipeline.json"
HISTORY_FILE = config.STATE_DIR / "history.json"
HEALTH_FILE = config.STATE_DIR / "health.json"
CORRECTIONS_FILE = config.DATA / "corrections.jsonl"
MAX_RUN_HISTORY = 60


# --- state -------------------------------------------------------------------
def load_state() -> Dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except ValueError:
            log.warning("state file corrupt; starting fresh")
    return {"last_processed": None, "runs": []}


def save_state(state: Dict) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    # Coverage counters must survive pruning of the bounded run log.
    state["scan_days"] = scan_day_totals(state)
    state["runs"] = state.get("runs", [])[-MAX_RUN_HISTORY:]
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def scan_day_totals(state: Dict) -> Dict:
    """Known successful scan counts, one record per filing day."""
    totals = {}
    records = list((state.get("scan_days") or {}).items())
    records.extend((r.get("date"), r) for r in state.get("runs", []))
    for day, record in records:
        if not day or record.get("blocked") or not record.get("index_rows"):
            continue
        if "candidates" not in record:
            continue
        previous = totals.get(day, {})
        totals[day] = {
            key: max(previous.get(key, 0), record.get(key) or 0)
            for key in ("index_rows", "candidates", "operating")
        }
    return dict(sorted(totals.items()))


def record_run(state: Dict, stats: Dict) -> None:
    stats = dict(stats)
    stats["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    state.setdefault("runs", []).append(stats)


# --- events ------------------------------------------------------------------
def _event_file(day: str) -> Path:
    return config.EVENTS_DIR / f"{day}.jsonl"


def withdrawn_event_ids() -> set:
    """Permanent tombstones for withdrawn findings, including after a rescan."""
    if not CORRECTIONS_FILE.exists():
        return set()
    ids = set()
    for line in CORRECTIONS_FILE.read_text(encoding="utf-8").splitlines():
        try:
            correction = json.loads(line)
            if correction.get("action") == "withdraw":
                ids.add(correction["event_id"])
        except (ValueError, KeyError, TypeError):
            log.warning("ignoring malformed correction row")
    return ids


def load_events_for_day(day: str) -> List[Event]:
    """Read a day's events, tolerating a file that has been merged badly.

    These files are committed by an automated run and can be written from two
    places at once. A union merge duplicates rows; a botched conflict
    resolution can leave "<<<<<<<" markers in the data. Both have happened.
    Rather than trust the file, drop anything unparseable and keep the first
    occurrence of each event id.
    """
    path = _event_file(day)
    if not path.exists():
        return []
    out: List[Event] = []
    seen = set()
    withdrawn = withdrawn_event_ids()
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            if line:
                log.warning("discarding non-JSON row in %s: %.30s", path.name, line)
            continue
        try:
            event = Event.from_dict(json.loads(line))
        except (ValueError, TypeError) as exc:
            log.warning("skipping malformed event row in %s: %s", path.name, exc)
            continue
        if event.id in seen or event.id in withdrawn:
            continue
        seen.add(event.id)
        out.append(event)
    return out


def recorded_elsewhere(day: str) -> set:
    """Every event id already on disk under some OTHER day.

    Event.id is (accession, signal_type) - global, with no day in it - but the
    append de-duplicated only against the day being written. EDGAR re-lists a
    filing in a later daily index: four did between 31 March and 3 April 2026,
    among them Cyclerion's 10-K. Each arrived twice, was written under both
    days, and the integrity check stopped the run - correctly, and with no way
    for the next run to get past it, because the second copy was written again
    every time.
    """
    ids: set = set()
    if not config.EVENTS_DIR.exists():
        return ids
    mine = _event_file(day).name
    for path in sorted(config.EVENTS_DIR.glob("*.jsonl")):
        if path.name == mine:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                ids.add(json.loads(line)["id"])
            except (ValueError, KeyError, TypeError):
                continue          # the repair pass owns malformed rows
    return ids


def append_events(day: str, events: Iterable[Event]) -> int:
    """Write events for a day, skipping any already recorded.

    Re-running a date must never duplicate rows: events are keyed by
    (accession, signal_type) via Event.id.
    """
    config.EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    kept = load_events_for_day(day)          # already de-duplicated and cleaned
    existing = {e.id for e in kept}
    # De-duplicated against what is on disk AND against itself: one pass can
    # produce the same (accession, signal_type) twice - a filing carrying two
    # item codes that map to one signal, say - and without this the duplicate
    # was written, counted in the returned total, and only removed later by the
    # repair pass. That made the reported count wrong and left correctness
    # depending on a downstream sweep.
    # ...and against every other day already recorded, because the key has no
    # day in it and EDGAR does re-list a filing on a later index.
    elsewhere = recorded_elsewhere(day)
    withdrawn = withdrawn_event_ids()
    fresh, seen = [], set(existing)
    for e in events:
        if e.id in seen or e.id in elsewhere or e.id in withdrawn:
            continue
        seen.add(e.id)
        fresh.append(e)

    on_disk = _event_file(day)
    raw = on_disk.read_text().splitlines() if on_disk.exists() else []
    needs_repair = len(raw) != len(kept)

    if not fresh and not needs_repair:
        return 0

    # Rewrite rather than append, so a file that arrived duplicated or with
    # conflict markers is repaired by the next run instead of persisting.
    if needs_repair:
        log.warning("repairing %s: %d rows on disk, %d valid unique events",
                    on_disk.name, len(raw), len(kept))
    on_disk.write_text("".join(
        json.dumps(e.to_dict(), sort_keys=True) + "\n" for e in kept + fresh),
        encoding="utf-8")
    return len(fresh)


def load_all_events(limit: Optional[int] = None) -> List[Event]:
    """Every recorded event, newest filing date first."""
    if not config.EVENTS_DIR.exists():
        return []
    events: List[Event] = []
    for path in sorted(config.EVENTS_DIR.glob("*.jsonl"), reverse=True):
        events.extend(load_events_for_day(path.stem))
        if limit and len(events) >= limit:
            break
    events.sort(key=lambda e: (e.filed, e.company), reverse=True)
    return events[:limit] if limit else events


def record_non_filing_day(state: Dict, day: str) -> None:
    """A weekday the SEC published no daily index for.

    days_to_process only offers days whose index has had time to publish, so a
    missing one is a day nobody filed on - a market holiday. Worth recording:
    the currency check counts weekdays, and without this it reports a healthy
    pipeline as two business days behind every Labor Day and Thanksgiving.
    """
    days = set(state.get("no_filings") or [])
    days.add(day)
    state["no_filings"] = sorted(days)


# --- historical sequence rates ------------------------------------------------
def save_history(stats: Dict) -> None:
    """Never replace a computed history with an empty one.

    These rates have been wiped twice by a local process reaching this writer
    with nothing to write - most recently the test suite, which stubbed a
    function name that does not exist and let the real history pass run. A run
    that computed nothing has nothing to save, and the file it would overwrite
    costs one request per company to rebuild.
    """
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not stats.get("total_historical_events"):
        held = load_history().get("total_historical_events", 0)
        if held:
            log.warning("history: refusing to overwrite %d events with an "
                        "empty result", held)
            return
    HISTORY_FILE.write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n")


def load_history() -> Dict:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except ValueError:
            log.warning("history file corrupt; ignoring")
    return {}


def save_health(report: Dict) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    HEALTH_FILE.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def load_health() -> Dict:
    if HEALTH_FILE.exists():
        try:
            return json.loads(HEALTH_FILE.read_text())
        except ValueError:
            log.warning("health file corrupt; ignoring")
    return {}


def raw_row_count() -> int:
    """Rows physically on disk, before de-duplication.

    The integrity check must count these, not the loaded events: the loader
    repairs as it reads, so counting its output made the duplicate check blind
    to the exact thing it exists to detect.
    """
    total = 0
    if not config.EVENTS_DIR.exists():
        return 0
    for path in config.EVENTS_DIR.glob("*.jsonl"):
        for line in path.read_text().splitlines():
            if line.strip().startswith("{"):
                total += 1
    return total


def repair_all() -> int:
    """Rewrite every event file de-duplicated. Returns rows removed.

    A union merge keeps both sides' lines, so a day committed from two places
    can carry duplicates. Repairing only the days a run happens to process
    leaves older files damaged indefinitely.
    """
    if not config.EVENTS_DIR.exists():
        return 0
    removed = 0
    for path in sorted(config.EVENTS_DIR.glob("*.jsonl")):
        raw = [l for l in path.read_text().splitlines() if l.strip()]
        events = load_events_for_day(path.stem)
        if len(raw) == len(events):
            continue
        removed += len(raw) - len(events)
        log.warning("repairing %s: %d rows -> %d unique events",
                    path.name, len(raw), len(events))
        path.write_text("".join(
            json.dumps(e.to_dict(), sort_keys=True) + "\n" for e in events),
            encoding="utf-8")
    return removed
