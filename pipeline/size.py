"""Company size, from the SEC's own definition.

"Fortune 500" is a revenue ranking published by a magazine; it includes private
companies and cannot be reproduced from EDGAR. The SEC has its own size test
that can: **public float**, reported on every 10-K cover page as
`dei:EntityPublicFloat`, and used by the SEC itself to decide a filer's
category. Those thresholds are the tiers used here, so "large" means the
regulator's definition of large, not an editorial one.

  >= $700M  large accelerated filer
  >= $75M   accelerated filer
  <  $75M   non-accelerated / smaller reporting company

Coverage comes from the XBRL `frames` API. Float is measured at each filer's
own fiscal mid-year, so no single period holds everyone - one frame returns a
few hundred companies, the union of twelve returns ~5,900 in eleven requests.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

from . import fetch

log = logging.getLogger(__name__)

LARGE_ACCELERATED = 700_000_000
ACCELERATED = 75_000_000
MEGA = 10_000_000_000

# Microsoft, the largest real float, is ~$3.6T. Anything past $10T is a units
# error in the filer's own XBRL (one filer reports $4.4 quadrillion), not a
# company, and would otherwise sit permanently at the top of any size sort.
IMPLAUSIBLE = 10_000_000_000_000

# Market value legitimately exceeds book assets, often by a lot for asset-light
# businesses, so this has to be generous. Past 100x it is a mis-tag, not a
# business model.
MAX_FLOAT_TO_ASSETS = 100

# The assets ratio is weak for banks and insurers, which carry enormous balance
# sheets against modest market value: Trustmark's mis-tagged $961B float is only
# 53x its assets and slips through. Dividing the float by shares outstanding is
# far sharper - it must produce a believable share price. The most expensive
# ordinary US share is a few thousand dollars (NVR trades near $8,000), so
# $10,000 clears every real company while catching Trustmark at $15,929.
MAX_IMPLIED_SHARE_PRICE = 10_000

TIER_MEGA, TIER_LARGE, TIER_MID, TIER_SMALL = "mega", "large", "mid", "small"

TIER_LABELS = {
    TIER_MEGA: "$10B+ float",
    TIER_LARGE: "Large accelerated filer",
    TIER_MID: "Accelerated filer",
    TIER_SMALL: "Smaller reporting company",
}


def tier_for(float_usd: Optional[float]) -> str:
    if float_usd is None:
        return ""
    if float_usd >= MEGA:
        return TIER_MEGA
    if float_usd >= LARGE_ACCELERATED:
        return TIER_LARGE
    if float_usd >= ACCELERATED:
        return TIER_MID
    return TIER_SMALL


def _periods() -> list:
    return [f"CY{y}Q{q}I" for y in (2024, 2025, 2026) for q in (1, 2, 3, 4)]


def _union_frame(tag: str, periods, unit: str = "USD") -> Dict[str, float]:
    """Union an instantaneous XBRL frame across periods, keeping the largest
    value seen per filer."""
    out: Dict[str, float] = {}
    for period in periods:
        url = f"https://data.sec.gov/api/xbrl/frames/{tag}/{unit}/{period}.json"
        try:
            data = fetch.get_json(url, accept_404=True)
        except fetch.SECBlocked:
            raise
        except Exception as exc:                 # noqa: BLE001
            log.warning("frame %s %s failed: %s", tag, period, exc)
            continue
        if not data:
            continue
        for row in data.get("data", []):
            cik, val = row.get("cik"), row.get("val")
            if cik is None or val is None or val <= 0:
                continue
            key = str(int(cik))
            if val > out.get(key, 0):
                out[key] = float(val)
    return out


def build_index() -> Dict[str, float]:
    """CIK (as string) -> public float in USD, verified against total assets.

    Filers make units errors in their own XBRL, and they are not detectable by
    magnitude alone: NVIDIA's reported $4.0T float is correct, while Universal
    Display's $6.8T is its real $6.8B tagged a thousand times too large. What
    separates them is the ratio to the company's own balance sheet - 15x for
    NVIDIA, 3,459x for Universal Display.

    Total assets come from a second frame union rather than a request per
    company, so verifying ~5,900 filers costs five extra requests, not 5,900.
    """
    floats = _union_frame("dei/EntityPublicFloat", _periods())
    assets = _union_frame("us-gaap/Assets",
                          ["CY2024Q4I", "CY2025Q2I", "CY2025Q3I",
                           "CY2025Q4I", "CY2026Q1I"])
    shares = _union_frame("dei/EntityCommonStockSharesOutstanding",
                          ["CY2025Q2I", "CY2025Q3I", "CY2025Q4I", "CY2026Q1I"],
                          unit="shares")

    clean: Dict[str, float] = {}
    rejected = 0
    for cik, val in floats.items():
        if val >= IMPLAUSIBLE:
            rejected += 1
            continue
        book = assets.get(cik)
        if book and val > book * MAX_FLOAT_TO_ASSETS:
            rejected += 1
            continue
        count = shares.get(cik)
        if count and val / count > MAX_IMPLIED_SHARE_PRICE:
            rejected += 1
            continue
        clean[cik] = val

    log.info("public float index: %d companies (%d rejected; %d asset records, "
             "%d share counts)", len(clean), rejected, len(assets), len(shares))
    return clean


def format_float(value: Optional[float]) -> str:
    if not value:
        return ""
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:,.1f}B"
    return f"${value / 1_000_000:,.0f}M"


# --- persistence -------------------------------------------------------------
# The index is stored and refreshed weekly rather than rebuilt every run. Public
# float only changes when a 10-K is filed, so a daily rebuild would be eleven
# wasted requests a day; and if a refresh fails the last good index is still
# used, so the size filter degrades to stale rather than disappearing.
REFRESH_AFTER_DAYS = 7


def load_or_refresh(store_path, today) -> Dict[str, float]:
    import datetime as dt
    import json

    stored, built_on = {}, None
    if store_path.exists():
        try:
            blob = json.loads(store_path.read_text())
            stored = blob.get("float_by_cik", {}) or {}
            built_on = blob.get("built_on")
        except ValueError:
            log.warning("size index corrupt; rebuilding")

    fresh_enough = False
    if stored and built_on:
        try:
            age = (today - dt.date.fromisoformat(built_on)).days
            fresh_enough = age < REFRESH_AFTER_DAYS
        except ValueError:
            fresh_enough = False

    if fresh_enough:
        log.info("public float index: %d companies (cached, built %s)",
                 len(stored), built_on)
        return stored

    try:
        rebuilt = build_index()
    except fetch.SECBlocked:
        raise
    except Exception as exc:                     # noqa: BLE001
        log.warning("float index refresh failed (%s); keeping %d cached entries",
                    exc, len(stored))
        return stored

    if not rebuilt:
        log.warning("float refresh returned nothing; keeping %d cached entries",
                    len(stored))
        return stored

    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(
        {"built_on": today.isoformat(), "count": len(rebuilt),
         "float_by_cik": rebuilt}, sort_keys=True))
    return rebuilt


# --- sanity check against the balance sheet ----------------------------------
# Filers make units errors in their own XBRL. CONX Corp reported a 2025 public
# float of $270,425 in its 10-K and $270,424,700,000 in the 10-K/A - out by a
# factor of a million - against total assets of $283M. Taking the later value
# put a SPAC among the largest companies in the United States.
#
# Market value legitimately exceeds book assets, often by a lot for asset-light
# businesses, so the ratio has to be generous. A float more than 100x total
# assets is not an asset-light company, it is a mis-tag.


def implausible_vs_assets(cik: int, float_usd: float) -> bool:
    """True when a float cannot be reconciled with the filer's own assets.

    Only worth running for companies that land in the top tiers, where a wrong
    number is both most visible and most damaging.
    """
    url = (f"https://data.sec.gov/api/xbrl/companyconcept/"
           f"CIK{cik:010d}/us-gaap/Assets.json")
    try:
        data = fetch.get_json(url, accept_404=True)
    except fetch.SECBlocked:
        raise
    except Exception:                            # noqa: BLE001
        return False
    if not data:
        return False                             # cannot disprove it

    values = [u.get("val") for arr in data.get("units", {}).values()
              for u in arr if u.get("val")]
    if not values:
        return False
    assets = max(values)
    return assets > 0 and float_usd > assets * MAX_FLOAT_TO_ASSETS


def verified_tier(cik: int, float_usd: Optional[float]):
    """(tier, float) with top-tier values re-checked per company.

    build_index() already screens the whole index against an assets frame; this
    is a second pass for the top tiers only, covering filers that were missing
    from that frame. Returns ("", None) when the float cannot be trusted.
    """
    tier = tier_for(float_usd)
    if tier in (TIER_MEGA, TIER_LARGE) and float_usd:
        if implausible_vs_assets(cik, float_usd):
            log.warning("CIK %s: public float $%.0f is implausible against its "
                        "own assets; treating size as unknown", cik, float_usd)
            return "", None
    return tier, float_usd
