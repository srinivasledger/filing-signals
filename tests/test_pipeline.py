"""Regression tests. No network: every case runs off saved fixtures.

Most of these exist because the behaviour they pin down was wrong at some
point during development. The comments say which mistake each one prevents.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import compare, enrich, ingest, models, publish, sections, triage, universe
from pipeline.run import business_days, days_to_process

FIX = Path(__file__).parent / "fixtures"


def _text(name: str) -> str:
    with gzip.open(FIX / name, "rt", encoding="utf-8") as fh:
        return fh.read()


# --- daily index parsing -----------------------------------------------------
def test_index_parses_every_row():
    raw = (FIX / "sample_form.idx").read_text(errors="ignore")
    rows = ingest.parse_index(raw)
    data_lines = [l for l in raw.splitlines()[11:] if l.strip()]
    assert len(rows) == len(data_lines), "rows were silently dropped"
    assert all(r.accession for r in rows)
    assert all(r.company for r in rows)


def test_index_handles_form_types_containing_spaces():
    # Splitting on whitespace turns "DEF 14A" into form "DEF", company "14A".
    rows = ingest.parse_index((FIX / "sample_form.idx").read_text(errors="ignore"))
    forms = {r.form for r in rows}
    multiword = {f for f in forms if " " in f}
    assert multiword, "fixture should contain multi-word form types"
    for f in multiword:
        assert not f.split()[0] in ("DEF", "NT", "PRE") or " " in f


# --- filing headers ----------------------------------------------------------
def test_header_yields_numeric_item_codes():
    items, sic, _, name = enrich.parse_header((FIX / "solesence.hdr.sgml").read_text())
    assert "4.02" in items
    assert sic == 2844
    assert "SOLESENCE" in name.upper()


def test_triage_maps_item_codes_to_signals():
    gaucho, _, _, _ = enrich.parse_header((FIX / "gaucho.hdr.sgml").read_text())
    sole, _, _, _ = enrich.parse_header((FIX / "solesence.hdr.sgml").read_text())
    adial, _, _, _ = enrich.parse_header((FIX / "adial.hdr.sgml").read_text())
    assert triage.classify_items(gaucho) == [models.AUDITOR_CHANGE]
    assert triage.classify_items(sole) == [models.RESTATEMENT]
    assert triage.classify_items(adial) == []      # item 3.01, not ours


# --- HTML flattening ---------------------------------------------------------
def test_inline_tags_do_not_split_words():
    # Filings wrap fragments of words in spans/inline XBRL. Substituting a
    # space for those tags produced "Item 1A. Ri sk Factors" in a real 10-Q and
    # silently broke every downstream text match.
    html = "<p>Item 1A. Ri<span>sk</span> Fact<ix:nonFraction>ors</ix:nonFraction></p>"
    assert "Risk Factors" in sections.html_to_text(html)


def test_block_tags_still_separate_words():
    assert "one two" in sections.html_to_text("<p>one</p><p>two</p>").replace("\n", " ")


# --- going concern -----------------------------------------------------------
def test_conclusion_beats_nearby_alleviation_language():
    # Every ASC 205-40 note recites both outcomes as methodology. Cyclerion's
    # note says "Management's plans to alleviate the conditions..." and then
    # concludes the opposite way. Proximity matching got this backwards.
    note = ("Management's plans to alleviate the conditions that raise substantial "
            "doubt include reduced spending. Accordingly, the Company has concluded "
            "that substantial doubt exists about its ability to continue.")
    assert sections.classify_going_concern(note) == sections.GC_SUBSTANTIAL_DOUBT


def test_genuine_alleviation_is_recognised():
    note = ("These conditions raised substantial doubt. Management's plans "
            "alleviate the substantial doubt about the Company's ability to "
            "continue as a going concern.")
    assert sections.classify_going_concern(note) == sections.GC_DOUBT_ALLEVIATED


def test_cyclerion_pair_produces_no_transition():
    # THE regression test. Both filings mention "going concern" ~17 times and
    # both conclude substantial doubt: nothing changed, so nothing is reported.
    cur = sections.going_concern_state(_text("cyclerion_2026q2.txt.gz"))
    pri = sections.going_concern_state(_text("cyclerion_2025q3.txt.gz"))
    assert cur["state"] == sections.GC_SUBSTANTIAL_DOUBT
    assert pri["state"] == sections.GC_SUBSTANTIAL_DOUBT
    assert sections.gc_bucket(cur["state"]) == sections.gc_bucket(pri["state"])


def test_going_concern_read_from_the_note_not_risk_factors():
    st = sections.going_concern_state(_text("cyclerion_2026q2.txt.gz"))
    assert st["source"] == "going-concern note"


def test_bullet_fragments_are_not_headings():
    # A Flux Power 10-K matched the bullet "ability to continue as a going
    # concern;" as a note heading and read the wrong 4,000 characters.
    assert not sections._looks_like_heading("ability to continue as a going concern;")
    assert not sections._looks_like_heading("ability to continue operating as a going concern.")
    assert sections._looks_like_heading("Going Concern")
    assert sections._looks_like_heading(
        "Doubt About the Company's Ability to Continue as a Going Concern")
    assert sections._looks_like_heading("Liquidity and Going Concern")


def test_no_conclusion_states_collapse():
    # "none" -> "risk factor only" means boilerplate moved, not that an
    # accounting conclusion was reached.
    assert sections.gc_bucket(sections.GC_NONE) == sections.gc_bucket(
        sections.GC_RISK_FACTOR_ONLY)
    assert sections.gc_bucket(sections.GC_SUBSTANTIAL_DOUBT) != sections.gc_bucket(
        sections.GC_NONE)


def test_new_registrant_headline_matches_actual_state():
    # This headline was hardcoded to "disclosed substantial doubt", which was
    # false for filings whose conclusion was "alleviated" and for one with no
    # conclusion at all.
    alleviated = compare._gc_headline_new_registrant("Acme", sections.GC_DOUBT_ALLEVIATED)
    assert "alleviate" in alleviated
    assert "disclosed substantial doubt about its ability" not in alleviated
    doubt = compare._gc_headline_new_registrant("Acme", sections.GC_SUBSTANTIAL_DOUBT)
    assert "substantial doubt" in doubt


def test_quotes_do_not_start_mid_word():
    body = ("Some earlier sentence ends here. The Company has concluded that "
            "substantial doubt exists about its ability to continue as a going "
            "concern for one year after these statements are issued.")
    st = sections.going_concern_state("Going Concern\n" + body + " " * 300)
    q = st["quote"]
    assert q, "expected a quote"
    assert q[0].isupper() or q[0] in "\u201c(", f"quote starts mid-word: {q[:40]!r}"


# --- accounting standards ----------------------------------------------------
def test_issued_standard_is_not_an_adoption():
    # Listing standards the FASB has published is boilerplate in nearly every
    # annual report; treating it as a policy change flagged 8 of 18 filings.
    asus = sections.extract_asus(
        "In November 2024, the FASB issued ASU 2024-03, Expense Disaggregation.")
    assert asus["2024-03"]["status"] == "pending"


def test_actual_adoption_is_detected():
    asus = sections.extract_asus(
        "Effective July 1, 2026, the Company adopted ASU 2023-08, Crypto Assets.")
    assert asus["2023-08"]["status"] == "adopted"


# --- similarity --------------------------------------------------------------
def test_similarity_ignores_figures_and_dates():
    a = "Revenue is recognized when control transfers, as of March 31, 2026, totaling 1,234."
    b = "Revenue is recognized when control transfers, as of June 30, 2026, totaling 9,876."
    assert compare.similarity(a, b) > 0.95


def test_similarity_detects_rewrites():
    a = "Revenue is recognized when control of the goods transfers to the customer."
    b = "The Company acts as an agent and recognizes revenue net of amounts paid to suppliers."
    assert compare.similarity(a, b) < 0.3


# --- universe ----------------------------------------------------------------
def test_fund_paperwork_is_excluded():
    assert not universe.form_is_candidate("N-PX")
    assert not universe.form_is_candidate("NPORT-P")
    assert universe.form_is_candidate("8-K")
    assert universe.form_is_candidate("10-Q")
    assert not universe.sic_is_operating(6722)
    assert universe.sic_is_operating(2834)
    assert universe.sic_is_operating(None)      # unknown must not be dropped


# --- storage / scheduling ----------------------------------------------------
def test_event_id_is_stable_across_serialisation():
    e = models.Event(signal_type=models.RESTATEMENT, confidence=models.CONFIRMED,
                     company="X", cik=1, form="8-K", filed="2026-08-21",
                     accession="0001-26-000001", filing_url="u", headline="h")
    assert e.id == models.Event.from_dict(e.to_dict()).id


def test_append_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(publish.config, "EVENTS_DIR", tmp_path)
    e = models.Event(signal_type=models.RESTATEMENT, confidence=models.CONFIRMED,
                     company="X", cik=1, form="8-K", filed="2026-08-21",
                     accession="0001-26-000001", filing_url="u", headline="h")
    assert publish.append_events("2026-08-21", [e]) == 1
    assert publish.append_events("2026-08-21", [e]) == 0, "re-run duplicated an event"


def test_pipeline_backfills_missed_days():
    # The self-healing property: a gap is repaired by the next run.
    days = days_to_process({"last_processed": "2026-08-14"}, dt.date(2026, 8, 22))
    assert days[0] == dt.date(2026, 8, 17)
    assert days[-1] == dt.date(2026, 8, 21)
    assert all(d.weekday() < 5 for d in days)


def test_no_work_when_current():
    assert days_to_process({"last_processed": "2026-08-21"}, dt.date(2026, 8, 22)) == []
