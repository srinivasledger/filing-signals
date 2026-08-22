"""Sub-classification of 8-K Items 4.01 and 4.02.

The item code alone flattens very different events into one bucket. The
sub-classification is where the actual signal lives:

  Item 4.02(a) - management or the board concluded the statements cannot be
                 relied upon.
  Item 4.02(b) - the *auditor* told the company. Materially more serious: the
                 company did not find it itself.

  Item 4.01    - "dismissed" and "resigned" are not the same event. An auditor
                 resigning is a much stronger signal than a company rotating
                 firms, and a disclosed disagreement stronger still. A move
                 from a Big Four firm to a small one is a downgrade worth
                 ranking above a routine rotation between equals.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

# --- audit firm tiers --------------------------------------------------------
BIG_FOUR = {
    "Deloitte": r"Deloitte",
    "PwC": r"PricewaterhouseCoopers|\bPwC\b",
    "EY": r"Ernst\s*&\s*Young|\bEY\b(?!\w)",
    "KPMG": r"KPMG",
}
NATIONAL = {
    "BDO": r"\bBDO\b", "Grant Thornton": r"Grant\s+Thornton", "RSM": r"\bRSM\b",
    "Crowe": r"\bCrowe\b", "Baker Tilly": r"Baker\s+Tilly", "Forvis": r"Forvis|FORVIS",
    "CohnReznick": r"CohnReznick", "Moss Adams": r"Moss\s+Adams",
    "EisnerAmper": r"EisnerAmper", "Marcum": r"\bMarcum\b", "Withum": r"Withum",
    "Plante Moran": r"Plante\s+Moran", "CBIZ": r"\bCBIZ\b", "Grassi": r"\bGrassi\b",
}

TIER_BIG4, TIER_NATIONAL, TIER_OTHER = "big_four", "national", "other"
_TIER_RANK = {TIER_BIG4: 3, TIER_NATIONAL: 2, TIER_OTHER: 1}


def firm_tier(name: str) -> str:
    if not name:
        return TIER_OTHER
    for _, pat in BIG_FOUR.items():
        if re.search(pat, name, re.I):
            return TIER_BIG4
    for _, pat in NATIONAL.items():
        if re.search(pat, name, re.I):
            return TIER_NATIONAL
    return TIER_OTHER


def canonical_firm(name: str) -> str:
    """Normalise to a comparable label so concentration counts aggregate."""
    for label, pat in {**BIG_FOUR, **NATIONAL}.items():
        if re.search(pat, name, re.I):
            return label
    cleaned = re.sub(r"\s*[,(].*$", "", name).strip(" .,")
    return re.sub(r"\s+", " ", cleaned)[:60]


# --- Item 4.02 ---------------------------------------------------------------
_EXPLICIT_402 = re.compile(r"Item\s*4\.02\s*\(\s*([ab])\s*\)", re.I)
# (b) means the auditor raised it. These phrasings are the tell.
_AUDITOR_TOLD_US = re.compile(
    r"(?:was\s+)?advised\s+by\s+(?:its|the|our)\s+(?:independent\s+)?"
    r"(?:registered\s+public\s+)?account\w*"
    r"|(?:its|the)\s+independent\s+(?:registered\s+public\s+)?accounting\s+firm\s+"
    r"(?:advised|notified|informed)",
    re.I,
)
_WE_CONCLUDED = re.compile(
    r"(?:the\s+)?(?:Board|Audit\s+Committee|management|Chief\s+Financial\s+Officer)"
    r"[^.]{0,120}?(?:concluded|determined|identified)",
    re.I,
)


def classify_402(text: str) -> Dict[str, object]:
    """Return which limb of Item 4.02 applies and why."""
    explicit = _EXPLICIT_402.search(text)
    if explicit:
        limb = explicit.group(1).lower()
        return {"limb": limb, "basis": "explicit item reference"}
    if _AUDITOR_TOLD_US.search(text):
        return {"limb": "b", "basis": "filing says the auditor advised the company"}
    if _WE_CONCLUDED.search(text):
        return {"limb": "a", "basis": "filing says management or the board concluded"}
    return {"limb": None, "basis": ""}


LIMB_LABELS = {
    "a": "Item 4.02(a) — management or the board reached the conclusion",
    "b": "Item 4.02(b) — the auditor advised the company",
}


# --- Item 4.01 ---------------------------------------------------------------
_RESIGNED = re.compile(r"\bresign(?:ed|ation|s)\b", re.I)
_DISMISSED = re.compile(r"\bdismiss(?:ed|al|es)\b|\bterminat(?:ed|ion)\b"
                        r"|will\s+no\s+longer\s+be\s+(?:retain|engag)", re.I)
_DECLINED = re.compile(r"declined\s+to\s+stand\s+for\s+re-?appointment", re.I)

# The negative form is boilerplate in almost every 4.01; the positive form is
# the newsworthy one, so it must be distinguished rather than keyword-matched.
_NO_DISAGREEMENTS = re.compile(
    r"there\s+(?:were|have\s+been)\s+no\s*(?:\([i]+\))?\s*disagreements"
    r"|no\s+disagreements\s+with", re.I)
_HAD_DISAGREEMENTS = re.compile(
    r"there\s+(?:were|was|have\s+been)\s+(?:one\s+or\s+more|certain|the\s+following)"
    r"\s+disagreement|a\s+disagreement\s+(?:arose|occurred|existed)", re.I)

_FIRM_TOKEN = (r"[A-Z][A-Za-z&.,'’\- ]{2,60}?"
               r"(?:LLP|L\.L\.P\.|LLC|L\.L\.C\.|P\.?C\.?|PLLC|CPAs?|"
               r"& Co\.|Inc\.|Chartered)")
_DISMISS_CTX = re.compile(
    r"(?:Dismissal of|dismissed|notified)\s+(" + _FIRM_TOKEN + r")", re.I)
_ENGAGE_CTX = re.compile(
    r"(?:Engagement of|engaged|appointed|retained)\s+(?:the\s+firm\s+)?("
    + _FIRM_TOKEN + r")", re.I)


def _first_firm(pattern: re.Pattern, text: str) -> str:
    m = pattern.search(text)
    if not m:
        return ""
    name = " ".join(m.group(1).split())
    # Trim leading connective words the loose token can absorb.
    return re.sub(r"^(?:the|its|our|as|new|former)\s+", "", name, flags=re.I).strip(" .,")


def classify_401(text: str) -> Dict[str, object]:
    """Direction of the change, disagreement status, and the two firms."""
    if _RESIGNED.search(text):
        direction = "resigned"
    elif _DECLINED.search(text):
        direction = "declined_reappointment"
    elif _DISMISSED.search(text):
        direction = "dismissed"
    else:
        direction = None

    if _HAD_DISAGREEMENTS.search(text):
        disagreements = True
    elif _NO_DISAGREEMENTS.search(text):
        disagreements = False
    else:
        disagreements = None

    outgoing = _first_firm(_DISMISS_CTX, text)
    incoming = _first_firm(_ENGAGE_CTX, text)
    # Identical firms on both sides means the loose firm-name pattern matched
    # the same mention twice, not that a company re-hired its own auditor.
    # Publishing "dismissed PwC and engaged PwC" is worse than saying nothing.
    if outgoing and incoming and canonical_firm(outgoing) == canonical_firm(incoming):
        outgoing = incoming = ""
    out_tier, in_tier = firm_tier(outgoing), firm_tier(incoming)

    downgrade = bool(
        outgoing and incoming
        and _TIER_RANK[out_tier] > _TIER_RANK[in_tier]
    )

    return {
        "direction": direction,
        "disagreements_disclosed": disagreements,
        "predecessor_auditor": canonical_firm(outgoing) if outgoing else "",
        "successor_auditor": canonical_firm(incoming) if incoming else "",
        "predecessor_tier": out_tier if outgoing else "",
        "successor_tier": in_tier if incoming else "",
        "tier_downgrade": downgrade,
    }


DIRECTION_LABELS = {
    "resigned": "The auditor resigned",
    "dismissed": "The company dismissed the auditor",
    "declined_reappointment": "The auditor declined to stand for reappointment",
}


def rank_401(parsed: Dict) -> str:
    """Severity. A resignation or a disclosed disagreement outranks a routine
    rotation; a Big Four to small firm move outranks an exchange of equals."""
    if parsed.get("disagreements_disclosed") is True:
        return "high"
    if parsed.get("direction") in ("resigned", "declined_reappointment"):
        return "high"
    if parsed.get("tier_downgrade"):
        return "high"
    return "normal"
