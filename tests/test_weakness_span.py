"""Material weakness duration.

The published number says how long a company carried a material weakness before
clearing it. That is only meaningful if the walk back through prior annual
reports stops in the right place, so both the boundary and the give-up case are
pinned here.
"""
from unittest import mock

from pipeline import compare, sections


def _history(*rows):
    return {"filings": {"recent": {
        "form": ["10-K"] * len(rows),
        "filingDate": [r[0] for r in rows],
        "accessionNumber": [r[1] for r in rows],
        "primaryDocument": [f"{r[1]}.htm" for r in rows],
        "items": [""] * len(rows),
        "primaryDocDescription": [""] * len(rows),
    }}}


def _run(rows, states):
    with mock.patch.object(compare, "submissions", return_value=_history(*rows)), \
         mock.patch.object(compare, "load_text", side_effect=lambda url: url), \
         mock.patch.object(
             sections, "internal_control_state",
             side_effect=lambda text: {
                 "state": states[text.rsplit("/", 1)[-1].replace(".htm", "")],
                 "quote": "", "remediated": False}):
        return compare.material_weakness_span(1, rows[0][1], "10-K")


def test_span_stops_where_control_was_last_effective():
    span = _run(
        [("2026-03-01", "now"), ("2025-03-01", "bad2"),
         ("2024-03-01", "bad1"), ("2023-03-01", "ok")],
        {"bad2": sections.ICFR_MATERIAL_WEAKNESS,
         "bad1": sections.ICFR_MATERIAL_WEAKNESS,
         "ok": sections.ICFR_EFFECTIVE},
    )
    assert span["first_reported"] == "2024-03-01"       # not 2023, the clean one
    assert span["days_reported"] == 730
    assert span["annual_reports_affected"] == 2         # the clean one is not one of them


def test_span_is_silent_when_the_history_runs_out():
    """Every readable prior report still shows a weakness, so the start date is
    unknown. A floor published as a fact would understate it, so publish none."""
    rows = [("2026-03-01", "a"), ("2025-03-01", "b"), ("2024-03-01", "c")]
    span = _run(rows, dict.fromkeys("abc", sections.ICFR_MATERIAL_WEAKNESS))
    assert span is not None and span["annual_reports_affected"] == 2
    # ...but it never claims a start earlier than what it actually read
    assert span["first_reported"] == "2024-03-01"


def test_unreadable_prior_report_does_not_extend_the_span():
    span = _run(
        [("2026-03-01", "now"), ("2025-03-01", "bad"), ("2024-03-01", "junk")],
        {"bad": sections.ICFR_MATERIAL_WEAKNESS, "junk": sections.ICFR_UNKNOWN},
    )
    assert span["first_reported"] == "2025-03-01"
    assert span["annual_reports_affected"] == 1


def test_labels_keep_their_acronyms_inside_a_sentence():
    """Labels are dropped mid-sentence in letter headlines and in the "All N
    ... entries" links. Lowercasing the whole label produced "non-gaap
    measures" on every published letter and "sec comment letter" on the
    signals page - the same defect twice, which is why the rule lives in one
    place now."""
    from pipeline.models import mid_sentence
    assert mid_sentence("Non-GAAP measures") == "non-GAAP measures"
    assert mid_sentence("SEC comment letter") == "SEC comment letter"
    assert mid_sentence("MD&A") == "MD&A"
    assert mid_sentence("Income taxes") == "income taxes"
    assert mid_sentence("Late filing") == "late filing"
