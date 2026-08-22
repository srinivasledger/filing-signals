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
    path = _event_file(day)
    if not path.exists():
        return []
    out: List[Event] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(Event.from_dict(json.loads(line)))
        except (ValueError, TypeError) as exc:
            log.warning("skipping malformed event row in %s: %s", path.name, exc)
    return out


def append_events(day: str, events: Iterable[Event]) -> int:
    """Write events for a day, skipping any already recorded.

    Re-running a date must never duplicate rows: events are keyed by
    (accession, signal_type) via Event.id.
    """
    config.EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    existing = {e.id for e in load_events_for_day(day)}
    fresh = [e for e in events if e.id not in existing]
    if not fresh:
        return 0
    with _event_file(day).open("a", encoding="utf-8") as fh:
        for event in fresh:
            fh.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")
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
