"""Read filing metadata from EDGAR's per-filing SGML header file.

Each filing has a companion `<accession>.hdr.sgml` of about 1 KB carrying the
item codes, SIC and company name. Reading that instead of the full submission
text is ~180x less data (1 KB vs 195 KB for a typical 8-K).

Two things were checked before settling on this:
  * HTTP Range requests do NOT work here. sec.gov answers a ranged request for
    an Archives document with a plain 200 and the entire body - no
    Accept-Ranges, no Content-Range - so "fetch the first 4 KB" silently
    downloads everything.
  * The header file gives NUMERIC item codes (<ITEMS>4.02) rather than the
    English titles in the full-text header. Matching "4.02" is far more robust
    than matching a prose title whose wording and punctuation vary.
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional, Tuple

from . import fetch
from .sic import describe

log = logging.getLogger(__name__)

# Canonical titles for the codes we act on, so the site can show a human label
# without depending on how any individual filer worded it.
ITEM_TITLES = {
    "4.01": "Changes in Registrant's Certifying Accountant",
    "4.02": ("Non-Reliance on Previously Issued Financial Statements or a "
             "Related Audit Report or Completed Interim Review"),
}

_ITEMS = re.compile(r"<ITEMS>\s*([0-9]+\.[0-9]+)", re.I)
_SIC = re.compile(r"<ASSIGNED-SIC>\s*(\d{3,4})", re.I)
_NAME = re.compile(r"<CONFORMED-NAME>\s*(.+)", re.I)
_PERIOD = re.compile(r"<PERIOD>\s*(\d{8})", re.I)


def header_url(cik: int, accession: str) -> str:
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik}/"
        f"{accession.replace('-', '')}/{accession}.hdr.sgml"
    )


def parse_header(text: str) -> Tuple[List[str], Optional[int], str, str]:
    """Return (item_codes, sic, sic_description, company_name)."""
    items, seen = [], set()
    for m in _ITEMS.finditer(text):
        code = m.group(1)
        if code not in seen:
            seen.add(code)
            items.append(code)

    sic: Optional[int] = None
    m = _SIC.search(text)
    if m:
        try:
            sic = int(m.group(1))
        except ValueError:
            sic = None

    name_m = _NAME.search(text)
    name = name_m.group(1).strip() if name_m else ""
    period = ""
    pm = _PERIOD.search(text)
    if pm:
        raw = pm.group(1)
        period = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return items, sic, describe(sic), name, period


def enrich(filing) -> bool:
    """Populate items/sic on a Filing in place.

    Returns False when the header could not be read, so the caller skips the
    filing rather than treating unknown items as "no items".
    """
    if not filing.accession:
        return False
    try:
        text = fetch.get(header_url(filing.cik, filing.accession), accept_404=True)
    except fetch.SECBlocked:
        # A refusal is not a missing document. Swallowing it here would turn a
        # blocked run into a day that merely looks quiet, and the day would be
        # recorded as processed and never retried.
        raise
    except Exception as exc:                     # noqa: BLE001
        log.warning("header fetch failed for %s: %s", filing.accession, exc)
        return False
    if not text:
        return False

    items, sic, sic_desc, name, period = parse_header(text)
    filing.period = period
    filing.items = items
    filing.sic = sic
    filing.sic_desc = sic_desc
    if name and not filing.company:
        filing.company = name
    return True
