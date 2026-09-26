"""Regression cases taken from reviewed SEC filings and correction records."""
import datetime as dt
import json
from pathlib import Path

from pipeline import compare, health, history, models, publish, sections


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
