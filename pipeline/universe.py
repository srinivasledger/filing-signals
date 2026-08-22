"""Decide which filings are worth looking at.

Two gates, cheapest first:
  1. Form type - free, applied straight off the daily index.
  2. SIC code  - applied after the header read, which we need anyway.

Measured effect on 2026-08-21: 4,983 index rows -> ~200 candidates. The bulk
of what we drop is fund paperwork (684 N-PX, 447 NPORT-P, 552 424B2) that can
never carry an accounting-policy or going-concern signal.
"""
from __future__ import annotations

from typing import Iterable, List

# 8-K carries the item-coded events (restatement, auditor change).
EVENT_FORMS = {"8-K", "8-K/A"}

# Periodic reports carry the narrative we diff for going-concern and policy.
PERIODIC_FORMS = {
    "10-K", "10-K/A", "10-KT", "10-KT/A",
    "10-Q", "10-Q/A", "10-QT", "10-QT/A",
    "20-F", "20-F/A", "40-F", "40-F/A",
}

CANDIDATE_FORMS = EVENT_FORMS | PERIODIC_FORMS

# SIC ranges that are pooled-investment or securitisation vehicles rather than
# operating companies. They file constantly and never carry these signals.
EXCLUDED_SIC = {
    6722,  # management investment offices, open-end
    6726,  # investment offices not elsewhere classified
    6189,  # asset-backed securities
    6199,  # finance services (mostly shells/SPACs in practice)
    6770,  # blank checks
}


def form_is_candidate(form: str) -> bool:
    return form.upper() in CANDIDATE_FORMS


def sic_is_operating(sic: int | None) -> bool:
    """Unknown SIC is allowed through: a missing code is not evidence of a
    fund, and dropping it would silently lose real filers."""
    if sic is None:
        return True
    return sic not in EXCLUDED_SIC


def filter_forms(filings: Iterable) -> List:
    return [f for f in filings if form_is_candidate(f.form)]


def filter_operating(filings: Iterable) -> List:
    return [f for f in filings if sic_is_operating(f.sic)]
