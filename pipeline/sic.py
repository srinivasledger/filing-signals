"""SIC code to industry label.

The per-filing header carries a bare numeric SIC. Rather than ship the full
several-hundred-entry SEC table, we name the specific codes that come up
constantly in this dataset and fall back to the standard SIC division ranges,
which is enough to give every event an honest industry label.
"""
from __future__ import annotations

from typing import Optional

SPECIFIC = {
    2834: "Pharmaceutical preparations",
    2836: "Biological products",
    2844: "Cosmetics and toiletries",
    3674: "Semiconductors",
    3841: "Medical instruments",
    4813: "Telecommunications",
    5812: "Restaurants",
    6021: "National commercial banks",
    6022: "State commercial banks",
    6199: "Finance services",
    6221: "Commodity contracts brokers",
    6311: "Life insurance",
    6500: "Real estate",
    6552: "Land subdividers and developers",
    6770: "Blank checks",
    6798: "Real estate investment trusts",
    7372: "Prepackaged software",
    7370: "Computer services",
    7389: "Business services",
    8731: "Commercial physical and biological research",
}

DIVISIONS = (
    (100, 999, "Agriculture, forestry and fishing"),
    (1000, 1499, "Mining"),
    (1500, 1799, "Construction"),
    (2000, 3999, "Manufacturing"),
    (4000, 4999, "Transportation and utilities"),
    (5000, 5199, "Wholesale trade"),
    (5200, 5999, "Retail trade"),
    (6000, 6799, "Finance, insurance and real estate"),
    (7000, 8999, "Services"),
    (9100, 9999, "Public administration"),
)


def describe(sic: Optional[int]) -> str:
    if not sic:
        return ""
    if sic in SPECIFIC:
        return SPECIFIC[sic]
    for lo, hi, label in DIVISIONS:
        if lo <= sic <= hi:
            return label
    return f"SIC {sic}"
