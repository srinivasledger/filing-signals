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
    state["runs"] = state.get("runs", [])[-MAX_RUN_HISTORY:]
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def record_run(state: Dict, stats: Dict) -> None:
    stats = dict(stats)
    stats["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    state.setdefault("runs", []).append(stats)


# --- events ------------------------------------------------------------------
def _event_file(day: str) -> Path:
    return config.EVENTS_DIR / f"{day}.jsonl"


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
        if event.id in seen:
            continue
        seen.add(event.id)
        out.append(event)
    return out


def append_events(day: str, events: Iterable[Event]) -> int:
    """Write events for a day, skipping any already recorded.

    Re-running a date must never duplicate rows: events are keyed by
    (accession, signal_type) via Event.id.
    """
    config.EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    kept = load_events_for_day(day)          # already de-duplicated and cleaned
    existing = {e.id for e in kept}
    fresh = [e for e in events if e.id not in existing]

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


# --- historical sequence rates ------------------------------------------------
def save_history(stats: Dict) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
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
