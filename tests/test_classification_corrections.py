"""Regression cases taken from reviewed SEC filings and correction records."""
import datetime as dt
import json
from pathlib import Path

from pipeline import compare, health, history, models, publish, sections


def test_no_conditions_or_events_raise_doubt_is_a_negative_conclusion():
    # Byrna, AirSculpt, Zentalis and IperionX each negated the *conditions*
    # rather than the nearby words "substantial doubt".
    conclusions = [
        "Management concluded that no conditions or events exist that, "
        "when considered together, raise substantial doubt about its ability "
        "to continue as a going concern.",
        "Management concluded there are no conditions or events, considered "
        "in the aggregate, that raise substantial doubt.",
        "The company determined there are no conditions or events that raise "
        "substantial doubt about its ability to continue.",
        "No events or conditions which raise substantial doubt were found.",
    ]
    for conclusion in conclusions:
        assert sections.classify_going_concern(conclusion) == sections.GC_NONE

    note = ("Going Concern\nManagement evaluates whether conditions or events "
            "raise substantial doubt about its ability to continue. "
            + conclusions[0] + "\nNOTE 2 - REVENUE\nRevenue is recognized later.")
    result = sections.going_concern_state(note)
    assert result["state"] == sections.GC_NONE
    assert "no conditions or events exist" in result["quote"]


def test_conditions_negation_does_not_mask_a_later_real_doubt_conclusion():
    negative = ("Management concluded there are no conditions or events that "
                "raise substantial doubt. ")
    affirmative = ("A subsequent loss of financing raises substantial doubt "
                   "about our ability to continue as a going concern.")
    assert sections.classify_going_concern(negative + affirmative) == sections.GC_SUBSTANTIAL_DOUBT
    assert sections.classify_going_concern(
        "No events of default occurred. " + affirmative) == sections.GC_SUBSTANTIAL_DOUBT


def test_conditional_future_doubt_does_not_become_present_doubt():
    optimum = ("A failure to secure funding by April 2026 may raise substantial "
               "doubt about our ability to continue as a going concern in the future.")
    assert sections.classify_going_concern(optimum) == sections.GC_NONE
    assert sections.classify_going_concern(
        optimum + " Current losses raise substantial doubt about our ability "
        "to continue as a going concern.") == sections.GC_SUBSTANTIAL_DOUBT


def test_fallback_keeps_trigger_after_its_own_note_heading():
    # The fallback starts 400 characters before the first match. Searching for
    # the next note from offset zero cut at Note 1 before the actual conclusion.
    text = ("Cover page text.\n" * 50 + "\nNOTE 1 - BASIS OF PRESENTATION\n"
            "The financial statements have been prepared on a going concern "
            "basis. Our recurring losses raise substantial doubt about our "
            "ability to continue as a going concern. We have not obtained "
            "sufficient financing.\nNOTE 2 - REVENUE\nRevenue is recognized "
            "upon delivery.")
    state = sections.going_concern_state(text)
    assert state["state"] == sections.GC_SUBSTANTIAL_DOUBT
    assert "recurring losses" in state["quote"]
    assert "Revenue is recognized" not in state["quote"]


def test_alleviation_after_initial_conditional_sentence_governs():
    capstone = ("The conditions could raise substantial doubt about the "
                "Company's ability to continue as a going concern. "
                "Management evaluated liquidity and operating plans. " * 20 +
                "Based on this evaluation, management believes these plans "
                "alleviate the substantial doubt.")
    result = sections.going_concern_state("NOTE 1 - BASIS OF PRESENTATION\n" + capstone)
    assert result["state"] == sections.GC_DOUBT_ALLEVIATED
    assert "alleviate the substantial doubt" in result["quote"]


def test_investment_recoverability_doubt_is_not_going_concern():
    # Penguin's 2026-01-06 10-Q and 2026-10-20 10-K discuss ASC 321
    # investment impairment. Neither filing contains a going-concern claim.
    investment = (
        "Other Non-Marketable Equity Investments\nThe Company identified "
        "significant qualitative impairment indicators related to an equity "
        "investment. Collectively, these factors raised substantial doubt "
        "regarding the Company's ability to recover the carrying value of the "
        "investment. In accordance with ASC 321, the Company assessed its "
        "fair value."
    )
    assert sections.going_concern_state(investment)["state"] == sections.GC_NONE
    assert sections.going_concern_state(investment)["quote"] == ""

    # An unrelated investment sentence cannot hide a later real conclusion.
    real_doubt = ("Current losses raise substantial doubt about our ability "
                  "to continue as a going concern.")
    result = sections.going_concern_state(investment + " " + real_doubt)
    assert result["state"] == sections.GC_SUBSTANTIAL_DOUBT
    assert real_doubt in result["quote"]


def test_no_assurance_plans_will_alleviate_preserves_present_doubt():
    # Dankon's 2026-10-15 10-Q and ZRCN's 2026-09-28 10-K both expressly
    # concluded substantial doubt, then warned that their plans might fail.
    doubt = ("These conditions raise substantial doubt about the Company's "
             "ability to continue as a going concern. ")
    caveats = (
        "There can be no assurance that such financing will be available on "
        "acceptable terms, or at all, or that management's plans will "
        "alleviate the substantial doubt.",
        "There can be no assurance these actions will alleviate the "
        "substantial doubt.",
    )
    for caveat in caveats:
        note = "Note 2 - Going Concern\n" + doubt + caveat + " " * 200
        result = sections.going_concern_state(note)
        assert result["state"] == sections.GC_SUBSTANTIAL_DOUBT
        assert "raise substantial doubt" in result["quote"]
        assert sections.classify_going_concern(caveat) == sections.GC_NONE

    affirmative = ("Management concluded that its completed financing has "
                   "alleviated the substantial doubt.")
    assert sections.classify_going_concern(doubt + caveats[0] + " " + affirmative) == (
        sections.GC_DOUBT_ALLEVIATED)


def test_substantial_doubt_remains_is_an_affirmative_conclusion():
    # GROOVY's 2026-03-12 10-Q says doubt about its survival "remains".
    # Its prior 10-Q also disclosed doubt, so there was no easing.
    conclusion = ("Substantial doubt about the Company's ability to continue "
                  "as a going concern remains.")
    assert sections.classify_going_concern(conclusion) == sections.GC_SUBSTANTIAL_DOUBT
    assert sections.going_concern_state(
        "Going Concern\n" + conclusion + " " * 200)["state"] == sections.GC_SUBSTANTIAL_DOUBT
    for word in ("persists", "continues to exist"):
        assert sections.classify_going_concern(conclusion.replace("remains", word)) == (
            sections.GC_SUBSTANTIAL_DOUBT)
    assert sections.classify_going_concern(
        "There remains substantial doubt about our ability to continue as a going concern.") == (
            sections.GC_SUBSTANTIAL_DOUBT)
    for phrase in ("no longer remains", "may remain"):
        assert sections.classify_going_concern(conclusion.replace("remains", phrase)) == (
            sections.GC_NONE)


def test_negative_alleviation_remains_doubt_and_later_completed_alleviation_wins():
    # Envoy's current 10-Q says management's plans "do not alleviate" doubt.
    doubt = ("These matters raise substantial doubt about the Company's "
             "ability to continue as a going concern and although management "
             "has developed plans, such plans do not alleviate the substantial "
             "doubt.")
    assert sections.classify_going_concern(doubt) == sections.GC_SUBSTANTIAL_DOUBT
    result = sections.going_concern_state("Going Concern\n" + doubt + " " * 200)
    assert result["state"] == sections.GC_SUBSTANTIAL_DOUBT
    assert "do not alleviate" in result["quote"]
    later = " Subsequent completed financing alleviates the substantial doubt."
    assert sections.classify_going_concern(doubt + later) == sections.GC_DOUBT_ALLEVIATED


def test_icfr_denial_and_historical_weakness_do_not_become_remediation():
    abundia = ("ITEM 9A. CONTROLS AND PROCEDURES\nManagement concluded that "
               "internal control over financial reporting was not effective. "
               "Remediation efforts are ongoing. Management cannot conclude "
               "that our internal control over financial reporting is effective.\n"
               "ITEM 9B. OTHER INFORMATION\nNone.")
    state = sections.internal_control_state(abundia)
    assert state["state"] == sections.ICFR_MATERIAL_WEAKNESS
    assert not state["remediated"]

    ocugen = ("ITEM 9A. CONTROLS AND PROCEDURES\nBased on this assessment, "
              "management concluded that our internal control over financial "
              "reporting was effective as of December 31, 2024. As previously "
              "disclosed, management identified a material weakness in 2023 "
              "and determined it was remediated in 2024.\n"
              "ITEM 9B. OTHER INFORMATION\nNone.")
    assert sections.internal_control_state(ocugen)["state"] == sections.ICFR_EFFECTIVE


def test_10q_item4_works_without_reading_risk_factor_boilerplate():
    filing = ("ITEM 1A. RISK FACTORS\nIf we are unable to conclude that our "
              "internal control over financial reporting is effective, our "
              "share price could decline.\n"
              "ITEM 4. CONTROLS AND PROCEDURES\nManagement concluded that "
              "internal control over financial reporting was not effective.\n"
              "PART II\nITEM 1. LEGAL PROCEEDINGS\nNone.")
    assert sections.internal_control_state(filing)["state"] == sections.ICFR_MATERIAL_WEAKNESS
    assert sections.internal_control_state(
        "ITEM 1A. RISK FACTORS\nIf we are unable to conclude that our internal "
        "control over financial reporting is effective, investors may lose confidence.")[
            "state"] == sections.ICFR_UNKNOWN


def test_name_change_alone_does_not_break_comparability():
    old = "Prior annual report describing the same broadband business."
    renamed = "Current annual report describing the same broadband business."
    assert not compare.business_replaced_between("Altice USA, Inc.", renamed, old)
    reverse = "For accounting purposes, the share exchange is treated as a reverse acquisition."
    assert compare.business_replaced_between("Old Name", reverse, old)
    assert not compare.business_replaced_between("Old Name", reverse, reverse)


def _event(signal, quote, state, accession):
    return models.Event(signal_type=signal, confidence=models.DERIVED,
                        company="Acme", cik=1, form="10-K", filed="2026-09-25",
                        accession=accession, filing_url="https://www.sec.gov/x",
                        headline="claim", evidence={"current_state": state}, quote=quote)


def test_known_quote_check_resolves_mixed_past_and_present_conclusions():
    mixed = _event(models.GOING_CONCERN,
                   "Previously there was no substantial doubt. Current losses "
                   "raise substantial doubt about our ability to continue as a going concern.",
                   sections.GC_SUBSTANTIAL_DOUBT, "0001-26-000001")
    denied = _event(models.MATERIAL_WEAKNESS,
                    "Management cannot conclude that internal control over "
                    "financial reporting is effective.",
                    sections.ICFR_EFFECTIVE, "0001-26-000002")
    assert health._known_quote_contradictions([mixed]) == []
    assert health._known_quote_contradictions([denied]) == [denied]


def test_known_quote_check_rejects_investment_and_uncertain_alleviation_claims():
    investment = _event(models.GOING_CONCERN,
                        "These factors raised substantial doubt regarding the "
                        "Company's ability to recover the carrying value of "
                        "the investment. In accordance with ASC 321, the "
                        "investment was impaired.",
                        sections.GC_SUBSTANTIAL_DOUBT, "0001-26-000005")
    uncertain = _event(models.GOING_CONCERN,
                       "These conditions raise substantial doubt about the "
                       "Company's ability to continue as a going concern. "
                       "There can be no assurance these actions will alleviate "
                       "the substantial doubt.",
                       sections.GC_DOUBT_ALLEVIATED, "0001-26-000006")
    real = _event(models.GOING_CONCERN,
                  "These conditions raise substantial doubt about the "
                  "Company's ability to continue as a going concern.",
                  sections.GC_SUBSTANTIAL_DOUBT, "0001-26-000007")
    assert health._known_quote_contradictions([investment, uncertain, real]) == [
        investment, uncertain]


def test_known_quote_check_rejects_false_easing_but_accepts_ongoing_doubt():
    remains = ("Substantial doubt about the Company's ability to continue "
               "as a going concern remains.")
    not_alleviated = ("These matters raise substantial doubt about the Company's "
                      "ability to continue as a going concern. Management's "
                      "plans do not alleviate the substantial doubt.")
    groovy = _event(models.GOING_CONCERN, remains, sections.GC_NONE,
                    "0001-26-000008")
    false_alleviation = _event(models.GOING_CONCERN, not_alleviated,
                               sections.GC_DOUBT_ALLEVIATED, "0001-26-000009")
    envoy = _event(models.GOING_CONCERN, not_alleviated,
                   sections.GC_SUBSTANTIAL_DOUBT, "0001-26-000010")
    negative_control = _event(models.GOING_CONCERN,
                              remains.replace("remains", "no longer remains"),
                              sections.GC_NONE, "0001-26-000011")
    assert health._known_quote_contradictions(
        [groovy, false_alleviation, envoy, negative_control]) == [
            groovy, false_alleviation]


def test_withdrawn_ids_cannot_reappear_after_rescan_or_merge(tmp_path, monkeypatch):
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    monkeypatch.setattr(publish.config, "EVENTS_DIR", events_dir)
    monkeypatch.setattr(publish, "CORRECTIONS_FILE", tmp_path / "corrections.jsonl")
    event = _event(models.GOING_CONCERN, "quote", sections.GC_NONE,
                   "0001-26-000003")
    publish.CORRECTIONS_FILE.write_text(json.dumps({
        "action": "withdraw", "event_id": event.id}) + "\n")
    assert publish.append_events(event.filed, [event]) == 0
    assert not (events_dir / f"{event.filed}.jsonl").exists()
    # A later union merge may put the old JSONL line back on disk. The loader
    # must suppress it, and the next append pass repairs the file.
    path = events_dir / f"{event.filed}.jsonl"
    path.write_text(json.dumps(event.to_dict()) + "\n")
    assert publish.load_events_for_day(event.filed) == []
    assert publish.append_events(event.filed, []) == 0
    assert path.read_text() == ""


def test_published_corrections_agree_with_the_event_record():
    root = Path(__file__).resolve().parent.parent
    log = [json.loads(line) for line in (root / "data/corrections.jsonl").read_text().splitlines()]
    assert log
    assert len({row["event_id"] for row in log}) == len(log)
    assert all(row["source_url"].startswith("https://www.sec.gov/") for row in log)
    events = {e.id: e for e in publish.load_all_events()}
    for correction in log:
        id = correction["event_id"]
        if correction["action"] == "withdraw":
            assert id not in events
        else:
            assert events[id].headline == correction["new_headline"]
            if correction.get("new_evidence"):
                for key, value in correction["new_evidence"].items():
                    assert events[id].evidence[key] == value
            else:
                assert events[id].evidence["current_state"] == correction["new_state"]
    assert health._known_quote_contradictions(events.values()) == []


def test_history_health_warns_on_version_and_omissions(tmp_path, monkeypatch):
    monkeypatch.setattr(health.config, "STATE_DIR", tmp_path)
    state = {"last_processed": "2026-09-25", "runs": []}
    data = {"methodology_version": history.METHODOLOGY_VERSION - 1,
            "companies": 1, "total_historical_events": 2,
            "rows": [{"eligible": 1}]}
    path = tmp_path / "history.json"
    path.write_text(json.dumps(data))
    sample = _event(models.RESTATEMENT, "A cited filing excerpt.", "",
                    "0001-26-000004")
    checks = health.run_checks([sample], state, dt.date(2026, 9, 25))["checks"]
    check = next(c for c in checks if c["name"] == "Follow-on rates computed")
    assert check["status"] == health.WARN and "outdated" in check["detail"]
    data.update(methodology_version=history.METHODOLOGY_VERSION,
                requested_companies=2, omitted_companies=1)
    path.write_text(json.dumps(data))
    checks = health.run_checks([sample], state, dt.date(2026, 9, 25))["checks"]
    check = next(c for c in checks if c["name"] == "Follow-on rates computed")
    assert check["status"] == health.WARN and "available" in check["detail"]
