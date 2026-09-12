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


# The daily master index carries the same rows as form.idx in a different
# format: pipe-delimited, one line per filing, no fixed-width traps at all.
# It is generated separately, which is the point of falling back to it.
_MASTER_ROW = re.compile(
    r"^(?P<cik>\d{1,10})\|(?P<company>[^|]*)\|(?P<form>[^|]*)\|"
    r"(?P<date>\d{8})\|(?P<path>edgar/\S+)\s*$"
)


def parse_master_index(text: str) -> List[Filing]:
    """Parse a daily master.idx into the same Filing rows form.idx gives."""
    rows: List[Filing] = []
    for line in text.splitlines():
        m = _MASTER_ROW.match(line.strip())
        if not m:
            continue
        d = m.group("date")
        rows.append(Filing(
            form=m.group("form").strip(),
            company=m.group("company").strip(),
            cik=int(m.group("cik")),
            filed=f"{d[:4]}-{d[4:6]}-{d[6:]}",
            path=m.group("path"),
        ))
    return rows


class IndexUnusable(Exception):
    """A daily index exists but yields no filings.

    Distinct from "not published" on purpose. A file that is there and empty
    is EDGAR mid-write or a cut-off response, and the right answer is to leave
    the day alone and try again next run. Treating it as a day with no
    filings would mark it processed and lose everything filed that day, and
    treating it as a holiday would record it as one. Neither is recoverable.
    """


# A business day on which the SEC received fewer than this many filings has
# not happened. Below it, the file is treated as suspect and cross-checked.
MIN_PLAUSIBLE_ROWS = 200


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

    # Two files, two formats, generated separately. form.idx is read first;
    # master.idx is read when form.idx is missing, or when what it yields is
    # too small to be a real business day - EDGAR serving a file it is still
    # writing, or a response cut off part-way. Before this, a truncated
    # form.idx was parsed as far as it went and the day was marked processed
    # with whatever filings happened to precede the cut, permanently.
    stem = f"{config.DAILY_INDEX}/{day.year}/QTR{quarter(day)}/"
    tag = day.strftime("%Y%m%d")
    sources = (("form", f"{stem}form.{tag}.idx", parse_index),
               ("master", f"{stem}master.{tag}.idx", parse_master_index))

    best: Optional[List[Filing]] = None
    existed = False
    for name, url, parse in sources:
        body = _read_index(url, day)
        if body is None:
            continue
        existed = True
        rows = parse(body)
        log.info("%s: %d filings in %s index", day, len(rows), name)
        if best is None or len(rows) > len(best):
            best = rows
        if len(best) >= MIN_PLAUSIBLE_ROWS:
            break                                 # no need to read the other

    if not existed:
        log.info("no index for %s (holiday or not yet published)", day)
        return None
    if not best:
        raise IndexUnusable(f"{day}: an index exists but yields no filings")
    if len(best) < MIN_PLAUSIBLE_ROWS:
        # Both files agree it is tiny. A genuinely quiet day, then - but say
        # so, because the alternative explanation is a bad day for EDGAR.
        log.warning("%s: only %d filings across both indexes", day, len(best))
    return best


def _read_index(url: str, day: dt.date) -> Optional[str]:
    """One index file, or None when it is not there. A refusal that is really
    a block is re-raised; a refusal for a file that simply does not exist is
    the ordinary answer for a holiday."""
    try:
        return fetch.get(url, accept_404=True)
    except fetch.SECBlocked:
        if sec_reachable():
            return None
        raise
