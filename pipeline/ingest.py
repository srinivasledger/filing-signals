"""Read EDGAR daily index files into structured filing rows.

Two traps in the .idx format, both verified against real files:

1. It looks whitespace-separated but is not. Form types such as "DEF 14A",
   "NT 10-K/A" and "S-8 POS" contain spaces, so splitting on whitespace
   silently corrupts rows.
2. The column header is split across two physical lines AND does not align
   with the data rows ("CIK" sits at offset 74 in the header but 78 in the
   data), so header-derived offsets are wrong too.

So we parse right-anchored instead: the trailing three fields (CIK, date,
path) are unambiguous, and whatever precedes them splits cleanly into form
type and company name.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import config, fetch

log = logging.getLogger(__name__)

# A data row ends with three unambiguous fields: CIK, an 8-digit filing date,
# and an "edgar/..." path containing no spaces. Anchoring on those lets the
# variable-width form type and company name fall out safely.
_ROW = re.compile(
    r"^(?P<head>.*?)\s{2,}(?P<cik>\d{1,10})\s+(?P<date>\d{8})\s+"
    r"(?P<path>edgar/\S+)\s*$"
)


@dataclass
class Filing:
    form: str
    company: str
    cik: int
    filed: str          # YYYY-MM-DD
    path: str           # edgar/data/<cik>/<accession>.txt
    accession: str = ""
    # populated later by enrich.py
    items: List[str] = field(default_factory=list)
    sic: Optional[int] = None
    sic_desc: str = ""
    period: str = ""

    def __post_init__(self) -> None:
        if not self.accession:
            m = re.search(r"(\d{10}-\d{2}-\d{6})", self.path)
            self.accession = m.group(1) if m else ""

    @property
    def filing_url(self) -> str:
        return f"{config.ARCHIVES}/{self.path}"

    @property
    def index_url(self) -> str:
        """Human-facing landing page for the filing."""
        acc_nodash = self.accession.replace("-", "")
        return (
            f"https://www.sec.gov/Archives/edgar/data/{self.cik}/"
            f"{acc_nodash}/{self.accession}-index.htm"
        )

    def to_dict(self) -> Dict:
        return {
            "form": self.form,
            "company": self.company,
            "cik": self.cik,
            "filed": self.filed,
            "accession": self.accession,
            "path": self.path,
            "items": self.items,
            "sic": self.sic,
            "sic_desc": self.sic_desc,
        }


def parse_index(text: str) -> List[Filing]:
    """Parse a daily form.idx into Filing rows. Malformed lines are skipped."""
    rows: List[Filing] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("-"):
            continue
        m = _ROW.match(line)
        if not m:
            continue

        head = m.group("head")
        parts = re.split(r"\s{2,}", head.strip(), maxsplit=1)
        form = parts[0].strip()
        company = parts[1].strip() if len(parts) > 1 else ""
        if not form or form == "Form Type":
            continue

        filed_raw = m.group("date")
        rows.append(
            Filing(
                form=form,
                company=company,
                cik=int(m.group("cik")),
                filed=f"{filed_raw[:4]}-{filed_raw[4:6]}-{filed_raw[6:]}",
                path=m.group("path"),
            )
        )
    return rows


def quarter(day: dt.date) -> int:
    return (day.month - 1) // 3 + 1


def sec_reachable() -> bool:
    """Probe a URL that certainly exists, to tell 'missing' from 'blocked'."""
    today = dt.date.today()
    url = f"{config.DAILY_INDEX}/{today.year}/QTR{quarter(today)}/"
    try:
        return bool(fetch.get(url, use_cache=False, accept_404=True))
    except fetch.SECBlocked:
        return False
    except Exception:                            # noqa: BLE001
        return False


def fetch_day(day: dt.date) -> Optional[List[Filing]]:
    """Fetch one business day's index, or None when there is nothing to fetch.

    EDGAR answers a request for a daily index that does not exist with **403,
    not 404** - the same status it uses for a blocked client. Saturdays,
    Sundays, holidays and days not yet published all come back 403.

    That made the holiday skip dead code: it waited for a 404 that never
    arrives. The first genuinely unattended run asked for a day with no index,
    took the "blocked" path instead, and failed the job.

    The two cases are separated by probing a URL known to exist. If that
    answers, the index is simply absent; if it does not, we really are blocked.
    """
    if day.weekday() >= 5:
        return None
    url = (
        f"{config.DAILY_INDEX}/{day.year}/QTR{quarter(day)}/"
        f"form.{day.strftime('%Y%m%d')}.idx"
    )
    try:
        body = fetch.get(url, accept_404=True)
    except fetch.SECBlocked:
        if sec_reachable():
            log.info("no index published for %s (holiday or not yet released)", day)
            return None
        raise
    if body is None:
        log.info("no index for %s (holiday or not yet published)", day)
        return None
    rows = parse_index(body)
    log.info("%s: %d filings in index", day, len(rows))
    return rows
