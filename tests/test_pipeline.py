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

from pipeline import (auditor, compare, enrich, health, ingest, late, letters,
                      models, officers, publish, sections, triage, universe)
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
    items, sic, _, name, _period = enrich.parse_header(
        (FIX / "solesence.hdr.sgml").read_text())
    assert "4.02" in items
    assert sic == 2844
    assert "SOLESENCE" in name.upper()


def test_triage_maps_item_codes_to_signals():
    gaucho, *_ = enrich.parse_header((FIX / "gaucho.hdr.sgml").read_text())
    sole, *_ = enrich.parse_header((FIX / "solesence.hdr.sgml").read_text())
    adial, *_ = enrich.parse_header((FIX / "adial.hdr.sgml").read_text())
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


# --- negation, one case per form, with positive controls -------------------
# ChronoScale Holdings wrote "substantial doubt ... is not raised" and was
# published as disclosing substantial doubt: the opposite of its filing. The
# controls matter as much as the negations - the first fix caught the negation
# and destroyed seven true positives.
import pytest


@pytest.mark.parametrize("text", [
    "Management has concluded that substantial doubt about our ability to "
    "continue as a going concern is not raised.",
    "These conditions do not raise substantial doubt about the Company's ability "
    "to continue as a going concern.",
    "The conditions did not raise substantial doubt about its ability to continue.",
    "There is no substantial doubt about the Company's ability to continue as a "
    "going concern.",
    "Management concluded that substantial doubt does not exist about its "
    "ability to continue as a going concern.",
])
def test_negated_going_concern_is_not_a_disclosure(text):
    assert sections.classify_going_concern(text) == sections.GC_NONE


@pytest.mark.parametrize("text", [
    "These conditions raise substantial doubt about the Company's ability to "
    "continue as a going concern.",
    "Accordingly, the Company has concluded that substantial doubt exists about "
    "its ability to continue as a going concern.",
    "The financial statements disclose that substantial doubt existed about the "
    "Company's ability to continue as a going concern.",
    "These conditions raise substantial doubt that has not been alleviated by "
    "management's plans.",
])
def test_real_going_concern_disclosures_survive(text):
    assert sections.classify_going_concern(text) == sections.GC_SUBSTANTIAL_DOUBT


def test_negation_beats_the_phrase_it_governs():
    # "do not raise substantial doubt": the negation starts BEFORE the phrase,
    # so a last-match-wins rule lets the positive reading win. Masking first is
    # what makes this come out right.
    assert sections.classify_going_concern(
        "The conditions described do not raise substantial doubt.") == sections.GC_NONE


def test_unreadable_wording_is_not_reported_as_doubt():
    # The old default turned "cannot tell" into an affirmative claim about a
    # named company.
    assert sections.classify_going_concern(
        "The Company evaluated going concern matters under ASC 205-40."
    ) == sections.GC_NONE


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


# --- revenue policy isolation -------------------------------------------------
# Three real false positives drove these: Certiplex and CVD Equipment reported
# MD&A performance commentary as policy changes, and Avnet reported the
# auditor's critical-audit-matter paragraph.
_ASC606_NOTE = (
    "Revenue Recognition\n"
    "In accordance with ASC 606 - Revenue from Contracts with Customers, the "
    "Company records revenue in an amount reflecting the consideration it "
    "expects. The Company identifies each performance obligation and allocates "
    "the transaction price using the standalone selling price. Revenue is "
    "recognized when control of the goods transfers to the customer. "
) * 4


def test_mda_commentary_is_not_a_policy_note():
    doc = ("Management's Discussion and Analysis\n"
           "Revenue Recognition\n"
           "The increase in 2026 was due principally to higher professional fees. "
           "The increased losses were primarily attributable to lower gross profit. " * 12)
    assert sections.revenue_section(doc) == ""


def test_critical_audit_matter_is_not_a_policy_note():
    doc = ("Report of Independent Registered Public Accounting Firm\n"
           "Critical Audit Matters\n"
           "Revenue Recognition\n"
           "The principal consideration for our determination that performing "
           "procedures relating to revenue recognition is a critical audit matter "
           "is a high degree of auditor effort. " * 12)
    assert sections.revenue_section(doc) == ""


def test_genuine_asc606_note_is_found():
    assert sections._asc606_score(_ASC606_NOTE) >= sections.MIN_ASC606_MARKERS
    assert "performance obligation" in sections.revenue_section(_ASC606_NOTE)


def test_policy_note_requires_asc606_vocabulary():
    thin = "Revenue Recognition\n" + ("The Company sells products to customers. " * 40)
    assert sections.revenue_section(thin) == ""


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


# --- late filings (Form 12b-25) ----------------------------------------------
def test_checkbox_read_in_either_order():
    # Filers render the question both ways; handling one order silently
    # returned "unknown" for half the population.
    before = ("is it anticipated that any significant change in results of "
              "operations will be reflected? \u2610 Yes \u2612 No")
    after = ("is it anticipated that any significant change in results of "
             "operations will be reflected? Yes \u2612 No \u2610")
    assert late._answer_near(before, late._ANTICIPATED_Q) is False
    assert late._answer_near(after, late._ANTICIPATED_Q) is True


def test_checkbox_unreadable_returns_none():
    both = ("significant change in results of operations? "
            "\u2612 Yes \u2612 No")
    assert late._answer_near(both, late._ANTICIPATED_Q) is None


def test_late_reason_strips_form_instructions():
    # html_to_text keeps single newlines, so the form's own instructions arrive
    # broken across lines and previously slipped past the boilerplate filter.
    doc = ("PART III\n\u2014 NARRATIVE State below\nin reasonable detail the "
           "reasons why Form 10-Q could not be filed within the prescribed time "
           "period. The Company is unable to file its Quarterly Report because "
           "its auditors have not completed their review of the interim "
           "financial statements.\nPART IV")
    reason = late.extract_reason(doc)
    assert "State below" not in reason
    assert "auditors have not completed" in reason


def test_severity_is_driven_by_the_stated_reason_not_the_checkbox():
    # Most filers tick "significant change": it was True for Cambium's
    # boilerplate notice and for Infleqtion's disclosed revenue-recognition
    # error alike, so on its own it separates nothing.
    substantive = late._severity("NT 10-Q", {
        "reason": "the Company identified an error related to revenue recognition",
        "anticipates_significant_change": True})
    boilerplate = late._severity("NT 10-Q", {
        "reason": "unable to file without unreasonable effort or expense",
        "anticipates_significant_change": True})
    plain = late._severity("NT 10-Q", {
        "reason": "requires additional time", "anticipates_significant_change": False})
    assert substantive == "high"
    assert boilerplate == "elevated"      # checkbox contributes, does not decide
    assert plain == "normal"


def test_statutory_deadline_matches_the_filing_calendar():
    # 30 June quarter, non-accelerated filer: 45 days -> 14 August, the date
    # 101 of one day's notices clustered on.
    import datetime as _dt
    assert late.deadline_for("2026-06-30", "NT 10-Q", "small") == _dt.date(2026, 8, 14)
    assert late.deadline_for("2026-06-30", "NT 10-Q", "mega") == _dt.date(2026, 8, 9)
    assert late.deadline_for("", "NT 10-Q", "small") is None


def test_reason_survives_abbreviation_in_company_name():
    # A naive sentence split cuts after "Corp." and orphans the verb, so the
    # published quote began "is unable to file...".
    doc = ("PART III\n\u2014 NARRATIVE GridAI Technologies Corp. is unable, without "
           "unreasonable effort or expense, to timely file its Quarterly Report on "
           "Form 10-Q for the quarter ended June 30, 2026.\nPART IV")
    reason = late.extract_reason(doc)
    assert reason.startswith("GridAI Technologies Corp."), reason[:60]
    assert "NARRATIVE" not in reason


def test_reason_strips_narrative_heading():
    doc = ("PART III - NARRATIVE The Registrant could not complete the filing of its "
           "Quarterly Report on Form 10-Q for the period ended June 30, 2026 due to "
           "delays in obtaining information.\nPART IV")
    assert not late.extract_reason(doc).lstrip().startswith(("-", "\u2014", "NARRATIVE"))


def test_leading_fragment_is_dropped_not_quoted():
    # "PART III" appears mid-sentence too, so the extracted body can start
    # part-way through one. That fragment has nothing to attach to.
    doc = ("the disclosure in PART III is framed around the Company's own review "
           "process and internal timetable. The Company could not file its Quarterly "
           "Report on Form 10-Q because its auditors have not finished.\nPART IV")
    reason = late.extract_reason(doc)
    assert not reason.startswith("is framed"), reason[:60]
    assert "could not file" in reason


# --- 8-K sub-classification ---------------------------------------------------
def test_402_limb_b_is_the_auditor_telling_the_company():
    b = ("On August 1, 2026 the Company was advised by its independent "
         "registered public accounting firm that the statements should not be relied upon.")
    a = ("On August 1, 2026 the Audit Committee of the Board concluded that the "
         "financial statements should no longer be relied upon.")
    assert auditor.classify_402(b)["limb"] == "b"
    assert auditor.classify_402(a)["limb"] == "a"
    assert auditor.classify_402("this Item 4.02(a) was discussed")["limb"] == "a"


def test_401_direction_and_disagreements():
    text = ("Dismissal of BDO USA, P.C. On August 10, 2026, the Registrant notified "
            "BDO USA, P.C. that it will no longer be retaining BDO. There were no (i) "
            "disagreements with BDO on any matter of accounting principles. "
            "Engagement of Schneider Downs & Co., Inc. The Registrant engaged "
            "Schneider Downs & Co., Inc. as its new accounting firm.")
    d = auditor.classify_401(text)
    assert d["direction"] == "dismissed"
    assert d["disagreements_disclosed"] is False
    assert d["predecessor_auditor"] == "BDO"
    assert d["tier_downgrade"] is True
    assert auditor.rank_401(d) == "high"


def test_401_resignation_outranks_rotation():
    resigned = auditor.classify_401("The auditor resigned effective August 1, 2026.")
    assert auditor.rank_401(resigned) == "high"
    routine = {"direction": "dismissed", "disagreements_disclosed": False,
               "tier_downgrade": False}
    assert auditor.rank_401(routine) == "normal"


def test_same_firm_both_sides_is_treated_as_unresolved():
    # "dismissed PwC and engaged PwC" is an extraction failure, not an event.
    text = ("dismissed PricewaterhouseCoopers LLP ... engaged "
            "PricewaterhouseCoopers LLP as its accounting firm")
    d = auditor.classify_401(text)
    assert d["predecessor_auditor"] == "" and d["successor_auditor"] == ""


def test_firm_tiers():
    assert auditor.firm_tier("KPMG LLP") == auditor.TIER_BIG4
    assert auditor.firm_tier("BDO USA, P.C.") == auditor.TIER_NATIONAL
    assert auditor.firm_tier("Smith & Co CPAs PLLC") == auditor.TIER_OTHER
    assert auditor.canonical_firm("Ernst & Young LLP") == "EY"


# --- browser scripts --------------------------------------------------------
def test_site_scripts_execute_without_reference_errors():
    """Run the page scripts against a DOM stub.

    `node --check` only parses. A ReferenceError shipped to production once -
    filter.js read an undeclared variable and died on load, so every filter on
    the live site was dead while the page looked perfectly normal. Parsing
    cannot see that; executing can.
    """
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not available")

    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [node, "tests/js/smoke.mjs"], cwd=root,
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


# --- SEC comment letters ----------------------------------------------------
def test_only_periodic_report_reviews_count():
    # Of thirty sampled staff letters, seventeen reviewed registration
    # statements and four reviewed a periodic report. Only the latter is the
    # accounting signal.
    assert letters.reviews_a_periodic_report(
        "Chewy, Inc. Form 10-K for Fiscal Year Ended February 2, 2025")
    assert not letters.reviews_a_periodic_report(
        "AEVEX Corp. Draft Registration Statement on Form S-1 Submitted May 11, 2026")
    assert not letters.reviews_a_periodic_report(
        "AvidXchange Holdings, Inc. Schedule 13E-3 filed June 18, 2025")
    assert not letters.reviews_a_periodic_report("")


def test_topic_matching_is_word_bounded():
    # "lease" matched every letter in the first sample, because "please"
    # contains it.
    assert "Leases" not in letters.classify_topics(
        "Please respond to this letter within ten business days.")
    assert "Leases" in letters.classify_topics(
        "Tell us how you evaluated the lease term under ASC 842.")


def test_comment_topics_and_citations():
    text = ("We have reviewed your filing and have the following comments. "
            "Note 2. Basis of Presentation and Significant Accounting Policies "
            "Revenue Recognition, page 60 We note you generate revenue from the "
            "sale of pet products.")
    assert "Revenue recognition" in letters.classify_topics(text)
    assert any("page 60" in c for c in letters.citations(text))


# --- finance chief departures ----------------------------------------------
def test_no_disagreements_boilerplate_is_not_a_disagreement():
    # "There were no disagreements with the Company" is the standard Item 5.02
    # sentence. Matched unguarded it labelled routine transitions at Boeing,
    # Xylem, Centene and Baxter as departures amid disagreement.
    d = officers.classify(
        "On August 1, 2026 the Chief Financial Officer resigned. There were no "
        "disagreements with the Company on any matter relating to its "
        "operations, policies or practices. The Board appointed a successor.")
    assert d["is_finance_departure"] is True
    assert d["disagreement_disclosed"] is False
    assert officers.severity(d) == "normal"


@pytest.mark.parametrize("denial", [
    "His departure from the Company is not related to any disagreement with the Company",
    "Mr Rizvi's resignation was not due to any disagreement with the Company",
    "Ms Barkema's departure is not the result of any disagreement with the Company",
    "There were no disagreements with the Company on any matter",
])
def test_denials_do_not_read_as_disagreements(denial):
    # Real filings put the negator a clause away from the noun, not next to it.
    # Requiring adjacency labelled Boston Beer, Synaptics and Marqeta as
    # departures amid disagreement when each filing said the opposite.
    d = officers.classify("The Chief Financial Officer resigned. " + denial + ".")
    assert d["disagreement_disclosed"] is False


def test_a_real_disagreement_still_registers():
    d = officers.classify(
        "The Chief Financial Officer resigned following a disagreement with the "
        "Company regarding revenue recognition policies.")
    assert d["disagreement_disclosed"] is True
    assert officers.severity(d) == "high"


def test_amended_and_restated_plan_is_not_a_restatement():
    # "restated" is in the name of nearly every equity plan ever adopted.
    d = officers.classify(
        "The Chief Financial Officer resigned. He remains eligible for grants "
        "under the Company's Second Amended and Restated 2021 Incentive Plan.")
    assert d["adverse_language"] == ""


def test_departure_must_share_a_sentence_with_the_role():
    # A 400-character window joined "a director resigned" to the CFO named in
    # the signature block.
    d = officers.classify(
        "On August 1, 2026, a director resigned from the Board. The report was "
        "signed by the Chief Financial Officer.")
    assert d["is_finance_departure"] is False


def test_missing_successor_outranks_an_orderly_handover():
    gone = officers.classify(
        "The Chief Financial Officer resigned. The Company has not yet named a successor.")
    handover = officers.classify(
        "The Chief Financial Officer resigned. The Board appointed Jane Doe as "
        "Chief Financial Officer.")
    assert officers.severity(gone) == "high"
    assert officers.severity(handover) == "normal"


# --- internal control -------------------------------------------------------
def test_material_weakness_definition_is_not_a_disclosure():
    # Every filer recites the definition; reciting it is not reporting one.
    state = sections.internal_control_state(
        "Item 9A. A material weakness is a deficiency, or combination of "
        "deficiencies, such that there is a reasonable possibility that a "
        "material misstatement will not be prevented. Management concluded that "
        "internal control over financial reporting was effective.")
    assert state["state"] == sections.ICFR_EFFECTIVE


def test_material_weakness_is_read_when_reported():
    state = sections.internal_control_state(
        "Item 9A. Controls and Procedures. Management concluded that our "
        "internal control over financial reporting was not effective because we "
        "identified a material weakness relating to revenue recognition.")
    assert state["state"] == sections.ICFR_MATERIAL_WEAKNESS


def test_unreadable_internal_control_is_unknown_not_severe():
    assert sections.internal_control_state(
        "Item 9A. Controls and Procedures. The Company evaluated its disclosure "
        "controls."
    )["state"] == sections.ICFR_UNKNOWN


def test_missing_index_is_not_a_block(monkeypatch):
    """EDGAR answers a request for a daily index that does not exist with 403,
    not 404 - the same status it uses for a blocked client. Weekends, holidays
    and days not yet published all come back 403, so the holiday skip was dead
    code waiting for a 404 that never arrives. The first unattended run asked
    for a day with no index and failed the job."""
    import datetime as _dt

    def blocked(*a, **k):
        raise ingest.fetch.SECBlocked("HTTP 403")

    monkeypatch.setattr(ingest.fetch, "get", blocked)
    monkeypatch.setattr(ingest, "sec_reachable", lambda: True)
    assert ingest.fetch_day(_dt.date(2026, 8, 24)) is None


def test_real_block_still_raises(monkeypatch):
    import datetime as _dt
    import pytest as _pytest

    def blocked(*a, **k):
        raise ingest.fetch.SECBlocked("HTTP 403")

    monkeypatch.setattr(ingest.fetch, "get", blocked)
    monkeypatch.setattr(ingest, "sec_reachable", lambda: False)
    with _pytest.raises(ingest.fetch.SECBlocked):
        ingest.fetch_day(_dt.date(2026, 8, 24))


def test_a_block_is_reported_not_hidden():
    import datetime as _dt

    state = {"last_processed": "2026-08-21",
             "runs": [{"date": "2026-08-24", "blocked": True}]}
    report = health.run_checks([], state, _dt.date(2026, 8, 25))
    access = next(c for c in report["checks"] if c["name"] == "SEC access")
    assert access["status"] == "warn"


def test_comment_letters_are_dated_by_publication():
    """A letter carries the date it was written; EDGAR publishes it months
    later. Dating the event by the letter put January entries in an August
    feed, which reads as a seven-month miss rather than a same-day catch."""
    class _F:
        form, cik, accession = "UPLOAD", 1, "0001-26-000001"
        company, filed, sic_desc = "Acme", "2026-01-15", ""

        @property
        def index_url(self):
            return "https://www.sec.gov/x"

    # analyse_letter needs a document; assert the dating contract directly.
    import inspect
    src = inspect.getsource(letters.analyse_letter)
    assert "disclosed_on or filing.filed" in src
    assert "filed=appeared" in src
    assert '"letter_dated": letter_dated' in src


def test_chart_data_and_svg_use_the_same_palette():
    """Server SVG and browser redraw must agree on colours.

    They were built from two separate mappings. The second numbered all nine
    slots, so late filings asked for --series-9, which is not defined, and the
    largest segment of every bar rendered black.
    """
    import json as _json
    import re as _re

    from pipeline import charts

    class _E:
        def __init__(self, filed, sig):
            self.filed, self.signal_type = filed, sig
            self.size_tier, self.routine = "", False

    events = [_E("2026-08-21", s) for s in charts.SERIES_ORDER]
    payload = _json.loads(_re.search(r">(.*)</script>",
                                     charts.chart_data(events), _re.S).group(1))
    for sig, var in charts.SERIES_VAR.items():
        assert payload["vars"][sig] == var[4:-1], sig


def test_every_chart_colour_is_defined_in_every_theme():
    """A slot missing from a theme block makes SVG fall back to black.

    Three slots were absent from the light palette after the series list grew,
    so the largest segment on every chart rendered pure black in light mode.
    Nothing failed; the colour simply did not exist.
    """
    import re

    css = (Path(__file__).resolve().parent.parent
           / "site" / "static" / "style.css").read_text()
    blocks = {
        "light": re.search(r"^:root\{(.*?)^\}", css, re.S | re.M),
        "media-dark": re.search(
            r"@media \(prefers-color-scheme:dark\)\{(.*?)\n\}", css, re.S),
        "attr-dark": re.search(
            r':root\[data-theme="dark"\]\{(.*?)^\}', css, re.S | re.M),
    }
    from pipeline.charts import SERIES_VAR
    needed = sorted({v[4:-1] for v in SERIES_VAR.values()})

    for name, match in blocks.items():
        assert match, f"{name} theme block not found"
        body = match.group(1)
        missing = [v for v in needed if f"{v}:" not in body]
        assert not missing, f"{name} is missing {missing}"
