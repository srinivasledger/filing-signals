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


# Entity suffixes and registrant qualifiers. Stripped for the aggregation key
# so "Simon & Edward" and "Simon & Edward LLP" count as one firm, and dropped
# from the display so the page does not carry PCAOB ids.
_SUFFIX = re.compile(
    r"\s*(?:,\s*)?\b(?:L\.?L\.?P\.?|L\.?L\.?C\.?|P\.?L\.?L\.?C\.?|P\.?C\.?"
    r"|CPAs?|CPA'?s|Chartered(?:\s+Accountants)?|US|U\.S\.|LTD|Limited|Inc\.?)\b\.?\s*$",
    re.I)
_TRAILING_NOTE = re.compile(r"\s*\((?:PCAOB|Firm)\b[^)]*\)\s*$", re.I)


def firm_key(name: str) -> str:
    """Aggregation key: one key per firm, however the filer spelled it.

    Spacing and entity suffixes vary between filings of the same change -
    "GreenGrowth CPAs", "Green Growth CPAs" and "GreenGrowth CPA" were three
    separate rows in the firm-movement table. Counting must not depend on
    which one a filer typed, so the key drops everything that is not a letter
    or a digit.
    """
    if not name:
        return ""
    for label, pat in {**BIG_FOUR, **NATIONAL}.items():
        if re.search(pat, name, re.I):
            return label.lower()
    stripped = name
    for _ in range(3):                       # "Wei, Wei & Co. LLP" -> two passes
        cut = _SUFFIX.sub("", _TRAILING_NOTE.sub("", stripped)).strip(" .,&")
        if cut == stripped:
            break
        stripped = cut
    # "Ham, Langston and Brezina, LLP" and "Ham, Langston & Brezina, LLP" are
    # one firm. Stripping punctuation removed the ampersand but left the word,
    # so the two spellings keyed differently and split the movement table.
    key = re.sub(r"\s+and\s+", " & ", stripped.lower())
    return re.sub(r"[^a-z0-9]", "", key)


def canonical_firm(name: str) -> str:
    """The label shown on the page. Comparison uses firm_key, not this.

    A bare `[,(]` truncation used to stand in for both jobs, and it published
    "Wei, Wei & Co. LLP" as "Wei" - a comma is as likely to be inside a firm's
    name as to introduce a qualifier after it.
    """
    for label, pat in {**BIG_FOUR, **NATIONAL}.items():
        if re.search(pat, name, re.I):
            return label
    cleaned = _TRAILING_NOTE.sub("", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,;")
    return cleaned[:60]


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
# "terminated" alone fires on anything a company ends. Greenland Mines filed
# an Item 4.01 whose text terminated an At-the-Market Sales Agreement with a
# placement agent, and it was recorded as an auditor dismissal. The word now
# has to be near the accountants.
_AUDITOR_NOUN = (r"(?:independent\s+)?(?:registered\s+public\s+)?"
                 r"(?:accounting\s+firm|accountants?|auditors?)")
_DISMISSED = re.compile(
    r"\bdismiss(?:ed|al|es)\b|\bdisengag(?:ed|e|ement)\b"
    r"|\bterminat(?:ed|ion)\b[^.]{0,80}?" + _AUDITOR_NOUN +
    r"|" + _AUDITOR_NOUN + r"[^.]{0,80}?\bterminat(?:ed|ion)\b"
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

# A firm name starts with a capital. The whole pattern used to be compiled
# with re.I, which makes [A-Z] match lowercase too, so the token happily began
# on the connective before the name: "notified that Simon & Edward LLP" was
# captured as "that Simon & Edward LLP". Only the trigger words are
# case-insensitive now; the name itself is not.
# Longest form first: with "CPAs?" the lazy quantifier stopped at "CPA" and
# left the S behind ("M&K CPAS" -> "M&K CPA").
_SUFFIX_ALT = (r"(?:PLLC|P\.L\.L\.C\.|LLP|L\.L\.P\.?|LLC|L\.L\.C\.|"
               r"CPA[sS]|CPA|P\.?C\.?|& Co\.|Inc\.|Chartered)")
# Suffixes chain: "Victor Mokuolu, CPA PLLC" is one firm, and stopping at the
# first of them published a legitimate sole-practitioner firm as a person's
# name. The trailing run is greedy so the whole entity is captured.
_FIRM_TOKEN = (r"[A-Z][A-Za-z&.,'’\- ]{2,60}?" + _SUFFIX_ALT
               + r"(?:\s*,?\s*" + _SUFFIX_ALT + r")*")
_LEAD = r"(?:\s+(?:that|by|of|with|the|its|our|as|a|an|new|former|previous))*\s+"
_DISMISS_CTX = re.compile(
    r"(?i:Dismissal of|dismissed|disengage[d]?|notified|terminated)" + _LEAD
    + r"(" + _FIRM_TOKEN + r")")
# The outgoing firm is often the one doing the telling: "was advised by Ham,
# Langston & Brezina, LLP ... that HL&B completed a transaction". Two audit
# firm mergers were recorded with neither side named because of this.
_ADVISED_BY_CTX = re.compile(
    r"(?i:advised|notified|informed)\s+by" + _LEAD + r"(" + _FIRM_TOKEN + r")")
_ENGAGE_CTX = re.compile(
    r"(?i:Engagement of|engaged|appointed|retained)" + _LEAD
    + r"(?:(?i:firm)\s+)?(" + _FIRM_TOKEN + r")")

# Words that mark a name as something other than the audit firm. A law firm,
# counsel or an underwriter is frequently named in the same Item 4.01 text,
# and one - Lewis Brisbois - was published as an incoming auditor.
_NOT_AN_AUDITOR = re.compile(
    r"\b(?:LLP|LLC|P\.?C\.?)?\s*(?:is|as|,)?\s*(?:our|its|the)?\s*"
    r"(?:legal\s+counsel|counsel|attorneys?|law\s+firm|underwriters?|"
    r"placement\s+agent|transfer\s+agent|bank(?:ers?)?|advisors?\s+to)\b", re.I)
_LAW_FIRM_HINT = re.compile(
    r"Bisgaard|Brisbois|&\s*Knight|Sullivan\s*&|Skadden|Latham|Cooley|"
    r"Wilson\s+Sonsini|Loeb\s*&|Duane\s+Morris|Sichenzia|Lucosky", re.I)


def _looks_like_an_auditor(name: str, text: str, company: str = "") -> bool:
    """Reject candidates that are plainly not the accountants.

    The firm token is deliberately loose - audit firms are named every way
    imaginable - so the check is on what the candidate IS, not on tightening
    the pattern until real firms stop matching.
    """
    if not name or len(name) < 4:
        return False
    if _LAW_FIRM_HINT.search(name):
        return False
    # named as counsel/underwriter in the sentence it was taken from
    at = text.find(name)
    if at != -1 and _NOT_AN_AUDITOR.search(text[at + len(name): at + len(name) + 80]):
        return False
    # the issuer is not its own auditor
    if company and firm_key(name) and firm_key(name) == firm_key(company):
        return False
    return True


def _first_firm(pattern: re.Pattern, text: str, company: str = "") -> str:
    """The first candidate in the text that survives the rejections."""
    for m in pattern.finditer(text):
        name = " ".join(m.group(1).split()).strip(" .,")
        if _looks_like_an_auditor(name, text, company):
            return name
    return ""


def classify_401(text: str, company: str = "") -> Dict[str, object]:
    """Direction of the change, disagreement status, and the two firms.

    `company` lets the issuer's own name be rejected as a candidate - one
    filing published "Starfighters Space" as Starfighters Space's predecessor
    auditor.
    """
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

    outgoing = (_first_firm(_DISMISS_CTX, text, company)
                or _first_firm(_ADVISED_BY_CTX, text, company))
    incoming = _first_firm(_ENGAGE_CTX, text, company)
    # Identical firms on both sides means the loose firm-name pattern matched
    # the same mention twice, not that a company re-hired its own auditor.
    # Publishing "dismissed PwC and engaged PwC" is worse than saying nothing.
    if outgoing and incoming and firm_key(outgoing) == firm_key(incoming):
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
