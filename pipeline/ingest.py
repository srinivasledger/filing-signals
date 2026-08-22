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


def fetch_day(day: dt.date) -> Optional[List[Filing]]:
    """Fetch one business day's index. Returns None for weekends/holidays,
    which EDGAR simply does not publish (404)."""
    if day.weekday() >= 5:
        return None
    url = (
        f"{config.DAILY_INDEX}/{day.year}/QTR{quarter(day)}/"
        f"form.{day.strftime('%Y%m%d')}.idx"
    )
    body = fetch.get(url, accept_404=True)
    if body is None:
        log.info("no index for %s (holiday or not yet published)", day)
        return None
    rows = parse_index(body)
    log.info("%s: %d filings in index", day, len(rows))
    return rows
