"""Compare a filing against the same company's previous comparable filing.

This is what makes the tracker novel. Publishing "this filing mentions going
concern" would be worthless - thousands do, every quarter, unchanged for years.
Publishing "this company's going-concern conclusion changed since last quarter"
is a genuine event, and it needs the previous filing to establish.

Comparisons are always like-for-like (10-Q against 10-Q, 10-K against 10-K).
A 10-Q's abbreviated policy note against a 10-K's full note would differ
enormously for purely structural reasons and produce constant false positives.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple

from . import config, fetch, sections
from .models import (CONFIRMED, DERIVED, GOING_CONCERN, POLICY_CHANGE,
                     REVENUE_RECOGNITION, SIGNAL_BLURBS, Event)

log = logging.getLogger(__name__)

# Below this Jaccard similarity a policy note counts as materially rewritten.
# Policy notes are near-verbatim year to year, so genuine edits show up as a
# clear drop rather than gradual drift.
# Deliberately strict. A revenue note that is 60% similar to last quarter's has
# genuinely been rewritten; anything looser is dominated by extraction noise.
POLICY_SIMILARITY_THRESHOLD = float(
    __import__("os").getenv("POLICY_SIMILARITY_THRESHOLD", "0.60")
)

# An extracted difference must actually be about revenue policy, not a stray
# table that drifted into the section.
_REVENUE_VOCAB = re.compile(
    r"performance obligation|transaction price|variable consideration|"
    r"point in time|over time|contract (?:asset|liabilit|with customer)|"
    r"revenue is recognized|recognizes revenue|standalone selling price|"
    r"principal|agent|gross|net", re.I)
SHINGLE_SIZE = 5
MIN_SECTION_CHARS = 400

# A genuine rewrite surfaces more than a single novel sentence; one is noise.
MIN_NOVEL_SENTENCES = 2


def _base_form(form: str) -> str:
    """10-Q/A -> 10-Q, so amendments compare against the original series."""
    return re.sub(r"/A$", "", form.upper()).strip()


def submissions(cik: int) -> Optional[Dict]:
    url = f"{config.SUBMISSIONS}/CIK{cik:010d}.json"
    try:
        return fetch.get_json(url, accept_404=True)
    except Exception as exc:                     # noqa: BLE001
        log.warning("submissions lookup failed for CIK %s: %s", cik, exc)
        return None


def _recent_rows(sub: Dict) -> List[Dict]:
    recent = sub.get("filings", {}).get("recent", {})
    if not recent.get("accessionNumber"):
        return []
    keys = ("accessionNumber", "filingDate", "form", "primaryDocument")
    n = len(recent["accessionNumber"])
    return [{k: recent.get(k, [None] * n)[i] for k in keys} for i in range(n)]


def document_url(cik: int, accession: str, primary_doc: str) -> str:
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik}/"
        f"{accession.replace('-', '')}/{primary_doc}"
    )


def find_prior_filing(cik: int, accession: str, form: str) -> Optional[Dict]:
    """The most recent filing of the same base form preceding this one."""
    sub = submissions(cik)
    if not sub:
        return None

    rows = _recent_rows(sub)
    target = _base_form(form)
    current = next((r for r in rows if r["accessionNumber"] == accession), None)
    if not current:
        return None

    candidates = [
        r for r in rows
        if _base_form(r["form"] or "") == target
        and r["accessionNumber"] != accession
        and (r["filingDate"] or "") <= (current["filingDate"] or "")
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda r: r["filingDate"] or "", reverse=True)
    prior = candidates[0]
    prior["url"] = document_url(cik, prior["accessionNumber"], prior["primaryDocument"] or "")
    return prior


# A reverse merger or SPAC de-SPACing keeps the CIK but replaces the business.
# The prior "filing" is then an empty shell with no going-concern note, and the
# comparison reports a dramatic transition that never happened. Verified on
# BOXABL Inc. (CIK 1906364, formerly FG Merger II Corp.), whose "none ->
# substantial doubt" was entirely an artefact of the merger.
# Scoped to the going-concern note only. Searching the whole document matched
# a SPAC describing its pending deal and a shell "evaluating a merger, reverse
# merger, sale" - a hypothetical - and wrongly marked both non-comparable.
_SUCCESSOR_LANGUAGE = re.compile(
    r"predecessor registrant|successor registrant|reverse recapitali[sz]ation",
    re.I,
)


def registrant_change_between(sub: Dict, prior_date: str, current_date: str) -> Optional[str]:
    """The former name, if the registrant was renamed between two filings."""
    for former in sub.get("formerNames") or []:
        changed_on = (former.get("to") or "")[:10]
        if changed_on and prior_date <= changed_on <= current_date:
            return former.get("name")
    return None


def current_document(cik: int, accession: str) -> Optional[str]:
    sub = submissions(cik)
    if not sub:
        return None
    row = next(
        (r for r in _recent_rows(sub) if r["accessionNumber"] == accession), None
    )
    if not row or not row.get("primaryDocument"):
        return None
    return document_url(cik, accession, row["primaryDocument"])


def load_text(url: str) -> Optional[str]:
    try:
        raw = fetch.get(url, accept_404=True)
    except Exception as exc:                     # noqa: BLE001
        log.warning("could not load %s: %s", url, exc)
        return None
    if not raw:
        return None
    return sections.html_to_text(raw)


# --- text similarity ---------------------------------------------------------
_NORMALISE_STRIP = re.compile(r"[^a-z ]+")


def normalise_for_diff(text: str) -> str:
    """Strip everything that legitimately changes every quarter - figures,
    dates, quarter labels - so only genuine wording changes remain."""
    text = text.lower()
    text = re.sub(r"\b\d[\d,\.]*\b", " ", text)
    text = re.sub(
        r"\b(january|february|march|april|may|june|july|august|september|"
        r"october|november|december)\b", " ", text)
    text = re.sub(r"\b(first|second|third|fourth)\s+quarter\b", " ", text)
    text = _NORMALISE_STRIP.sub(" ", text)
    return " ".join(text.split())


def shingles(text: str, size: int = SHINGLE_SIZE):
    words = text.split()
    if len(words) < size:
        return set()
    return {" ".join(words[i:i + size]) for i in range(len(words) - size + 1)}


def similarity(a: str, b: str) -> float:
    """Jaccard similarity over word shingles. 1.0 means identical wording."""
    sa, sb = shingles(normalise_for_diff(a)), shingles(normalise_for_diff(b))
    if not sa or not sb:
        return 1.0          # not comparable; treat as unchanged, never as news
    return len(sa & sb) / len(sa | sb)


def _diff_sample(current: str, prior: str, limit: int = 3) -> List[str]:
    """A few sentences present now and absent before - the actual change."""
    prior_sh = shingles(normalise_for_diff(prior))
    out: List[str] = []
    for sentence in re.split(r"(?<=[.;])\s+", current):
        s = sentence.strip()
        if len(s) < 60:
            continue
        # The splitter breaks on any period, including ones inside numbers and
        # abbreviations, which yields fragments starting mid-sentence. A quote
        # published verbatim should read as a sentence.
        if not (s[0].isupper() or s[0] in "\u201c(\""):
            continue
        sh = shingles(normalise_for_diff(s))
        if sh and not (sh & prior_sh):
            out.append(sections.truncate_words(s, 320))
        if len(out) >= limit:
            break
    return out


# --- event construction ------------------------------------------------------
def _severity_direction(prior_state: str, current_state: str) -> str:
    p = sections.GC_LADDER.index(prior_state)
    c = sections.GC_LADDER.index(current_state)
    return "escalated" if c > p else "eased"


def _gc_headline_new_registrant(company: str, current_state: str) -> str:
    """Headline for a filing whose prior filing describes a different business.

    Must be derived from the actual current state: hardcoding "substantial
    doubt" here published that claim about filings whose conclusion was
    "alleviated", and about one with no conclusion at all.
    """
    tail = " in its first report following a change of registrant"
    if current_state == sections.GC_SUBSTANTIAL_DOUBT:
        return (f"{company} disclosed substantial doubt about its ability to "
                f"continue as a going concern{tail}")
    if current_state == sections.GC_DOUBT_ALLEVIATED:
        return (f"{company} said management's plans alleviate substantial doubt "
                f"about its ability to continue as a going concern{tail}")
    return f"{company} changed its going-concern disclosure{tail}"


def _gc_headline(company: str, prior_state: str, current_state: str) -> str:
    if current_state == sections.GC_SUBSTANTIAL_DOUBT:
        return f"{company} disclosed substantial doubt about its ability to continue as a going concern"
    if current_state == sections.GC_DOUBT_ALLEVIATED:
        return f"{company} said management's plans alleviate substantial doubt about going concern"
    if current_state == sections.GC_NONE and prior_state in (
        sections.GC_SUBSTANTIAL_DOUBT, sections.GC_DOUBT_ALLEVIATED
    ):
        return f"{company} no longer discloses a going-concern conclusion"
    return f"{company}'s going-concern disclosure changed"


def analyse_periodic(filing) -> List[Event]:
    """Produce going-concern and policy-change events for one periodic report.

    Returns [] whenever we cannot establish a genuine change - including when
    there is no prior filing to compare against. Absence of proof is not an
    event.
    """
    events: List[Event] = []

    current_url = current_document(filing.cik, filing.accession)
    if not current_url:
        return events
    current_text = load_text(current_url)
    if not current_text or len(current_text) < 2000:
        return events

    prior = find_prior_filing(filing.cik, filing.accession, filing.form)
    if not prior:
        log.info("no prior %s for %s; cannot establish a change", filing.form, filing.company)
        return events
    prior_text = load_text(prior["url"])
    if not prior_text or len(prior_text) < 2000:
        return events

    sub = submissions(filing.cik)
    ticker = ""
    if sub and sub.get("tickers"):
        ticker = sub["tickers"][0]

    def base(signal, headline, evidence, quote, beta):
        return Event(
            signal_type=signal,
            confidence=DERIVED,
            company=filing.company,
            cik=filing.cik,
            ticker=ticker,
            form=filing.form,
            filed=filing.filed,
            accession=filing.accession,
            filing_url=filing.index_url,
            headline=headline,
            sic_desc=filing.sic_desc,
            evidence=evidence,
            quote=quote,
            prior_accession=prior["accessionNumber"],
            prior_url=prior["url"],
            beta=beta,
        )

    # --- going concern: a change of rung, not a keyword hit ---
    cur_gc = sections.going_concern_state(current_text)
    pri_gc = sections.going_concern_state(prior_text)

    former_name = registrant_change_between(
        sub or {}, prior["filingDate"] or "", filing.filed) if sub else None
    successor = bool(_SUCCESSOR_LANGUAGE.search(str(cur_gc.get("quote", ""))))
    comparable = not (former_name or successor)

    # A change of state is required in every case. Comparability only changes
    # how the event is *described*, never whether it is reported: if the prior
    # filing already said the same thing, nothing happened, and that holds
    # whether or not the registrant changed.
    changed = sections.gc_bucket(cur_gc["state"]) != sections.gc_bucket(pri_gc["state"])
    # When the registrant changed there is no meaningful "before", so only a
    # live conclusion is worth reporting. "The new business has no
    # going-concern disclosure" is not news, and reporting it produced an
    # event headlined "disclosed substantial doubt" for a filing that
    # disclosed nothing of the kind.
    if not comparable:
        changed = changed and cur_gc["state"] in (
            sections.GC_SUBSTANTIAL_DOUBT, sections.GC_DOUBT_ALLEVIATED)

    if changed:
        headline = (_gc_headline(filing.company, pri_gc["state"], cur_gc["state"])
                    if comparable
                    else _gc_headline_new_registrant(filing.company, cur_gc["state"]))
        events.append(base(
            GOING_CONCERN,
            headline,
            {
                "source": "ASC 205-40 going-concern note comparison",
                "comparable": comparable,
                "prior_state": pri_gc["state"] if comparable else None,
                "prior_state_label": (sections.GC_STATE_LABELS[pri_gc["state"]]
                                      if comparable else None),
                "current_state": cur_gc["state"],
                "current_state_label": sections.GC_STATE_LABELS[cur_gc["state"]],
                "direction": (_severity_direction(pri_gc["state"], cur_gc["state"])
                              if comparable else "not comparable"),
                "registrant_changed_from": former_name,
                "caveat": (None if comparable else
                           "The prior filing was made by a different business under the "
                           "same CIK, so this is a new disclosure rather than a change."),
                "located_in": cur_gc.get("source", ""),
                "prior_form": prior["form"],
                "prior_filed": prior["filingDate"],
                "why": SIGNAL_BLURBS[GOING_CONCERN],
            },
            cur_gc["quote"],
            beta=False,
        ))

    # --- newly referenced accounting standards ---
    # Diffing the SET of ASU identifiers, rather than the prose around them.
    # Whole-note text similarity was tried first and abandoned: filings have no
    # consistent note structure, so the extractor swept adjacent notes into the
    # comparison and the "change" it measured was mostly moving dollar figures.
    # It flagged 15 of 18 periodic filings in a single day, including
    # Parker-Hannifin and Sysco, whose policies had not changed.
    cur_asu = sections.extract_asus(current_text)
    pri_asu = sections.extract_asus(prior_text)
    new_codes = sorted(set(cur_asu) - set(pri_asu))
    adopted = [c for c in new_codes if cur_asu[c]["status"] == "adopted"]
    # Only an actual adoption is reported. A newly *issued* standard the filer
    # merely lists is boilerplate that appears in nearly every annual report -
    # emitting on it flagged 8 of 18 filings in one day, all of them routine.
    if adopted:
        headline_codes = adopted
        events.append(base(
            POLICY_CHANGE,
            f"{filing.company} referenced accounting standard"
            f"{'s' if len(headline_codes) > 1 else ''} "
            + ", ".join("ASU " + c for c in headline_codes)
            + " for the first time",
            {
                "source": "accounting standards update (ASU) reference comparison",
                "new_standards": new_codes,
                "adopted": adopted,
                "contexts": [cur_asu[c]["context"] for c in headline_codes[:3]],
                "prior_form": prior["form"],
                "prior_filed": prior["filingDate"],
                "why": SIGNAL_BLURBS[POLICY_CHANGE],
            },
            cur_asu[headline_codes[0]]["context"],
            beta=False,
        ))

    # --- revenue recognition wording (beta) ---
    cur_sec, pri_sec = sections.revenue_section(current_text), sections.revenue_section(prior_text)
    # If either section ran to the extraction cap we never found the end of the
    # note, so the comparison is sweeping in unrelated notes and the score is
    # meaningless. Silence beats a confident wrong answer.
    truncated = (len(cur_sec) >= sections.REVENUE_MAX_CHARS - 2
                 or len(pri_sec) >= sections.REVENUE_MAX_CHARS - 2)
    if (not truncated
            and len(cur_sec) >= MIN_SECTION_CHARS and len(pri_sec) >= MIN_SECTION_CHARS):
        # Compare only filer-specific wording: the ASC 606 recitation is
        # identical across thousands of filers and dominates both the score and
        # the "new language" list.
        cur_own = sections.strip_asc606_boilerplate(cur_sec)
        pri_own = sections.strip_asc606_boilerplate(pri_sec)
        score = similarity(cur_own, pri_own)
        if score < POLICY_SIMILARITY_THRESHOLD:
            added = [a for a in _diff_sample(cur_own, pri_own)
                     if _REVENUE_VOCAB.search(a)
                     and not sections.is_asc606_boilerplate(a)]
            # One stray sentence is extraction noise. A rewritten policy shows
            # up as several.
            if len(added) >= MIN_NOVEL_SENTENCES:
                events.append(base(
                    REVENUE_RECOGNITION,
                    f"{filing.company} changed its revenue recognition disclosure",
                    {
                        "source": "revenue recognition note comparison",
                        "similarity": round(score, 3),
                        "threshold": POLICY_SIMILARITY_THRESHOLD,
                        "new_language": added,
                        "prior_form": prior["form"],
                        "prior_filed": prior["filingDate"],
                        "why": SIGNAL_BLURBS[REVENUE_RECOGNITION],
                    },
                    added[0],
                    beta=True,
                ))

    return events
