"""Hold newly detected contradictions without publishing their claims.

The queue records only enough filing metadata to reproduce a review. It is
separate from the event log and from incomplete-day retries: the source scan
can complete while an individual interpretation remains unresolved.
"""
from __future__ import annotations

import json
import logging

from . import config, health, publish

log = logging.getLogger(__name__)
REASON = "known_quote_contradiction"
_FIELDS = {"accession", "signal_type", "filed", "scan_day", "source_url", "reason"}


def _path():
    return config.STATE_DIR / "review_queue.json"


def load_pending() -> dict:
    """Read the durable queue; corruption must not silently erase a hold."""
    path = _path()
    if not path.exists():
        return {}
    stored = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(stored, dict) or stored.get("version") != 1
            or not isinstance(stored.get("pending"), dict)):
        raise ValueError("Unsupported or malformed incoming review queue")
    pending = stored["pending"]
    for event_id, record in pending.items():
        if (not isinstance(event_id, str) or not event_id
                or not isinstance(record, dict) or set(record) != _FIELDS
                or not all(isinstance(value, str) and value for value in record.values())
                or record["reason"] != REASON):
            raise ValueError("Malformed incoming review queue record")
    return pending


def _save(pending: dict) -> None:
    if any(set(record) != _FIELDS or
           not all(isinstance(value, str) and value for value in record.values())
           for record in pending.values()):
        raise ValueError("Cannot save incomplete incoming review metadata")
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    try:
        temporary.write_text(json.dumps({"version": 1, "pending": pending},
                                        indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _resolved_ids() -> set:
    return ({event.id for event in publish.load_all_events()}
            | publish.withdrawn_event_ids())


def screen_incoming(events, scan_day: str) -> tuple:
    """Return fresh publishable candidates and the number held this scan.

    Existing events remain subject to the ordinary hard integrity gate. A
    conflicting copy in a new batch holds that whole ID, even if another copy
    looks valid. A previously held ID is released only after a valid candidate
    actually reaches the event log, or an explicit withdrawal tombstones it.
    """
    pending = load_pending()
    before = dict(pending)
    resolved = _resolved_ids()
    pending = {event_id: record for event_id, record in pending.items()
               if event_id not in resolved}
    fresh = [event for event in events if event.id not in resolved]
    held_ids = {event.id for event in health._known_quote_contradictions(fresh)}
    for event in fresh:
        if event.id not in held_ids:
            continue
        # Preserve the first observed scan day when EDGAR re-lists a filing.
        pending.setdefault(event.id, {
            "accession": event.accession,
            "signal_type": event.signal_type,
            "filed": event.filed,
            "scan_day": scan_day,
            "source_url": event.filing_url,
            "reason": REASON,
        })
    for event_id in sorted(held_ids):
        record = pending[event_id]
        log.warning("candidate held for review: id=%s accession=%s scan_day=%s "
                    "reason=%s source=%s", event_id, record["accession"],
                    scan_day, REASON, record["source_url"])
    if pending != before:
        _save(pending)
    return [event for event in fresh if event.id not in held_ids], len(held_ids)


def reconcile_published() -> None:
    """Clear only holds whose IDs are now published or explicitly withdrawn."""
    pending = load_pending()
    if not pending:
        return
    resolved = _resolved_ids()
    remaining = {event_id: record for event_id, record in pending.items()
                 if event_id not in resolved}
    if remaining != pending:
        _save(remaining)
