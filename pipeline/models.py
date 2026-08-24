"""The published record.

One Event is one thing that happened to one filer, backed by a citation. The
`evidence` field always holds deterministic, machine-checkable proof; the `ai`
field is optional enrichment that may be absent entirely (it is, whenever the
tracker runs without an API key). Nothing in the site may depend on `ai`.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Signal types, ordered by how much interpretation they require.
RESTATEMENT = "restatement"
AUDITOR_CHANGE = "auditor_change"
GOING_CONCERN = "going_concern"
POLICY_CHANGE = "policy_change"
REVENUE_RECOGNITION = "revenue_recognition"
LATE_FILING = "late_filing"
COMMENT_LETTER = "comment_letter"
OFFICER_DEPARTURE = "officer_departure"
MATERIAL_WEAKNESS = "material_weakness"

SIGNAL_LABELS = {
    RESTATEMENT: "Restatement",
    AUDITOR_CHANGE: "Auditor change",
    GOING_CONCERN: "Going concern",
    POLICY_CHANGE: "Accounting policy change",
    REVENUE_RECOGNITION: "Revenue recognition change",
    LATE_FILING: "Late filing",
    COMMENT_LETTER: "SEC comment letter",
    OFFICER_DEPARTURE: "Finance chief departure",
    MATERIAL_WEAKNESS: "Material weakness",
}

SIGNAL_BLURBS = {
    RESTATEMENT: (
        "The company told investors that previously issued financial "
        "statements should no longer be relied upon."
    ),
    AUDITOR_CHANGE: "The company's independent registered accounting firm changed.",
    GOING_CONCERN: (
        "Disclosure about the company's ability to continue as a going "
        "concern changed compared with its previous report."
    ),
    POLICY_CHANGE: (
        "The summary of significant accounting policies changed materially "
        "from the previous comparable filing."
    ),
    REVENUE_RECOGNITION: (
        "The revenue recognition policy disclosure changed materially from "
        "the previous comparable filing."
    ),
    LATE_FILING: (
        "The company told the SEC it could not file a periodic report on time, "
        "using Form 12b-25."
    ),
    MATERIAL_WEAKNESS: (
        "The company's conclusion on internal control over financial reporting "
        "changed: a material weakness was newly reported, or a previously "
        "reported one no longer appears."
    ),
    OFFICER_DEPARTURE: (
        "The company's chief financial or accounting officer departed, "
        "disclosed under 8-K Item 5.02."
    ),
    COMMENT_LETTER: (
        "SEC staff reviewed the company's periodic report and raised written "
        "comments on its accounting, or the company replied to them. Letters "
        "are published on EDGAR only after the review closes."
    ),
}

# How much the finding rests on pattern-matching vs interpretation.
CONFIRMED = "confirmed"   # SEC's own structured item code
DERIVED = "derived"       # our text comparison of two filings

CONFIDENCE_LABELS = {
    CONFIRMED: "Confirmed by SEC item code",
    DERIVED: "Derived from filing comparison",
}


@dataclass
class Event:
    signal_type: str
    confidence: str
    company: str
    cik: int
    form: str
    filed: str
    accession: str
    filing_url: str
    headline: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    quote: str = ""
    ticker: str = ""
    sic_desc: str = ""
    size_tier: str = ""
    public_float: float = 0.0
    prior_accession: str = ""
    prior_url: str = ""
    beta: bool = False
    routine: bool = False
    ai: Dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        raw = f"{self.accession}:{self.signal_type}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    @property
    def label(self) -> str:
        return SIGNAL_LABELS.get(self.signal_type, self.signal_type)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "id": self.id,
            "signal_type": self.signal_type,
            "label": self.label,
            "confidence": self.confidence,
            "beta": self.beta,
            "routine": self.routine,
            "company": self.company,
            "cik": self.cik,
            "ticker": self.ticker,
            "sic_desc": self.sic_desc,
            "size_tier": self.size_tier,
            "public_float": self.public_float,
            "form": self.form,
            "filed": self.filed,
            "accession": self.accession,
            "filing_url": self.filing_url,
            "headline": self.headline,
            "evidence": self.evidence,
            "quote": self.quote,
            "prior_accession": self.prior_accession,
            "prior_url": self.prior_url,
        }
        if self.ai:
            d["ai"] = self.ai
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Event":
        known = {
            "signal_type", "confidence", "company", "cik", "form", "filed",
            "accession", "filing_url", "headline", "evidence", "quote",
            "ticker", "sic_desc", "prior_accession", "prior_url", "beta", "ai", "routine",
            "size_tier", "public_float",
        }
        return cls(**{k: v for k, v in d.items() if k in known})
