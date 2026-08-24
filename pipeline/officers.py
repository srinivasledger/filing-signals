"""CFO and other senior finance departures (8-K Item 5.02).

Item 5.02 covers every director and officer change, so the item code alone is
noise - it is one of the most common 8-K items there is. The signal is narrower:
the *finance* chief leaving, and in particular leaving with no named successor,
which is what distinguishes an orderly handover from an abrupt one.
"""
from __future__ import annotations

import re
from typing import Dict

ITEM_OFFICERS = "5.02"

# Roles whose departure bears on financial reporting. A departing head of sales
# is an Item 5.02 event too, and is not this signal.
_FINANCE_ROLE = re.compile(
    r"\bchief\s+financial\s+officer\b|\bCFO\b"
    r"|\bchief\s+accounting\s+officer\b|\bCAO\b"
    r"|\bprincipal\s+financial\s+officer\b|\bprincipal\s+accounting\s+officer\b"
    r"|\bcontroller\b|\btreasurer\b",
    re.I,
)
_CHIEF_EXEC = re.compile(r"\bchief\s+executive\s+officer\b|\bCEO\b", re.I)

_DEPARTURE = re.compile(
    r"\bresign\w*|\bstep(?:ped|ping)?\s+down\b|\bdepart\w*|\bterminat\w*"
    r"|\bwill\s+no\s+longer\s+serve\b|\bseparation\s+from\s+the\s+Company\b"
    r"|\bretire\w*|\bdismiss\w*|\brelieved\s+of\b",
    re.I,
)

# A named successor, or an explicit interim arrangement, means a handover.
_SUCCESSOR = re.compile(
    r"\bappoint\w*|\bnamed\b|\bsucceed\w*|\bwill\s+serve\s+as\b|\bhas\s+been\s+"
    r"elected\b|\bassume\s+the\s+role\b|\bnew\s+chief\s+financial\s+officer\b",
    re.I,
)
_INTERIM = re.compile(r"\binterim\b|\bacting\b|\bon\s+an\s+interim\s+basis\b", re.I)

# "has not yet named a successor" contains "named", so the positive pattern
# alone concluded the opposite of what the filing said.
_NO_SUCCESSOR = re.compile(
    r"\bnot\s+(?:yet\s+)?(?:been\s+)?(?:named|appointed|identified|selected)\b"
    r"|\bno\s+(?:successor|replacement)\b"
    r"|\bsuccessor\s+has\s+not\b|\bdoes\s+not\s+(?:yet\s+)?have\s+a\s+successor\b"
    r"|\bsearch\s+for\s+a\s+(?:permanent\s+)?(?:successor|replacement)\b",
    re.I,
)

# Language that makes a departure materially worse - narrowly defined, because
# both obvious phrasings are traps.
#
#   "restated" appears in the name of nearly every equity plan ever adopted:
#   Baxter's filing was labelled a restatement on the strength of its "Second
#   Amended and Restated 2021 Incentive Plan".
#
#   "disagreements with the Company" is the standard Item 5.02 sentence, and it
#   is almost always NEGATED - "there were no disagreements with the Company".
#   Matching it unguarded labelled routine transitions at Boeing, Xylem, Centene
#   and Baxter as departures amid disagreement.
_ADVERSE = re.compile(
    r"\bfor\s+cause\b"
    r"|\b(?:internal|independent|Audit\s+Committee)\s+investigation\b"
    r"|\brestatement\s+of\s+(?:its|the|our|previously)"
    r"|\brestat\w+\s+(?:its\s+|the\s+|our\s+)?(?:previously\s+issued\s+)?"
    r"(?:consolidated\s+)?financial\s+statements"
    r"|\bmaterial\s+weakness\b"
    r"|\bmisconduct\b|\bviolation\s+of\s+the\s+Company'?s?\s+code\b",
    re.I,
)

# A disclosed disagreement is separately meaningful, and separately negated.
_DISAGREEMENT = re.compile(
    r"\bdisagreement(?:s)?\s+with\s+(?:the\s+)?(?:Company|management|Board|registrant)", re.I)
# The denial rarely sits next to the noun. Real filings say "is not related to
# any disagreement", "was not due to any disagreement", "is not the result of
# any disagreement" - so the negator has to be allowed to sit up to a clause
# away, while staying inside the same sentence.
_NO_DISAGREEMENT = re.compile(
    r"\b(?:no|not|never|without)\b[^.;]{0,70}?\bdisagreement(?:s)?\b"
    r"|\bdisagreement(?:s)?\b[^.;]{0,70}?\b(?:did|does|was|were|is|are)\s+not\b",
    re.I,
)

ROLE_LABELS = {
    "cfo": "Chief Financial Officer",
    "cao": "Chief Accounting Officer",
    "controller": "Controller",
    "treasurer": "Treasurer",
}


def _role(text: str) -> str:
    if re.search(r"\bchief\s+financial\s+officer\b|\bCFO\b|\bprincipal\s+financial\s+officer\b",
                 text, re.I):
        return "cfo"
    if re.search(r"\bchief\s+accounting\s+officer\b|\bCAO\b|\bprincipal\s+accounting\s+officer\b",
                 text, re.I):
        return "cao"
    if re.search(r"\bcontroller\b", text, re.I):
        return "controller"
    return "treasurer"


def classify(text: str) -> Dict[str, object]:
    """Decide whether an Item 5.02 filing is a finance-chief departure."""
    if not _FINANCE_ROLE.search(text) or not _DEPARTURE.search(text):
        return {"is_finance_departure": False}

    # The role and the departure must appear in the SAME sentence. A 400
    # character window was wide enough to join "a director resigned" to the
    # CFO named in the signature block two sentences later.
    sentences = re.split(r"(?<=[.;])\s+", text)
    hit = next((s for s in sentences
                if _FINANCE_ROLE.search(s) and _DEPARTURE.search(s)), None)
    if hit is None:
        return {"is_finance_departure": False}

    # An explicit denial governs, and an interim appointment is not a
    # succession however it is worded.
    interim = bool(_INTERIM.search(text))
    if _NO_SUCCESSOR.search(text):
        successor = False
    elif interim:
        successor = False
    else:
        successor = bool(_SUCCESSOR.search(text))
    # Adverse language only counts near the departure. Searched across the
    # whole filing it picked up equity-plan names and unrelated boilerplate.
    idx = text.find(hit)
    window = text[max(0, idx - 600): idx + len(hit) + 1800] if idx >= 0 else hit

    # Mask negated disagreement statements before looking for a real one, the
    # same way the going-concern classifier handles "is not raised".
    unnegated = _NO_DISAGREEMENT.sub(lambda m: " " * len(m.group(0)), window)
    disagreement = bool(_DISAGREEMENT.search(unnegated))

    adverse = _ADVERSE.search(window)

    return {
        "is_finance_departure": True,
        "role": _role(hit),
        "successor_named": successor,
        "interim_only": interim,
        "adverse_language": adverse.group(0) if adverse else "",
        "disagreement_disclosed": disagreement,
        "also_ceo": bool(_CHIEF_EXEC.search(text) and _DEPARTURE.search(text)),
    }


def severity(detail: Dict) -> str:
    """No named successor is the classic red flag; adverse language beats it."""
    if detail.get("adverse_language") or detail.get("disagreement_disclosed"):
        return "high"
    if not detail.get("successor_named") and not detail.get("interim_only"):
        return "high"
    if detail.get("interim_only"):
        return "elevated"
    return "normal"


def headline(company: str, detail: Dict) -> str:
    role = ROLE_LABELS.get(detail.get("role", "cfo"), "finance officer")
    if detail.get("disagreement_disclosed"):
        return f"{company} disclosed a disagreement in connection with its {role}'s departure"
    if detail.get("adverse_language"):
        return (f"{company}'s {role} departed, with the filing citing "
                f"{detail['adverse_language'].lower()}")
    if not detail.get("successor_named") and not detail.get("interim_only"):
        return f"{company}'s {role} departed with no named successor"
    if detail.get("interim_only"):
        return f"{company}'s {role} departed, replaced on an interim basis"
    return f"{company}'s {role} departed and a successor was named"
