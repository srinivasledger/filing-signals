"""What counts as a sequence.

The page's claim is that the order signals arrive in is informative. That only
holds if the steps are separate occasions, so the two ways they were not are
pinned here: two signals read out of one filing, and a Form 12b-25 answered by
the very report it deferred.
"""
import datetime as dt

from pipeline.render import _is_extension_of


class E:                                    # minimal stand-in for an Event
    def __init__(self, form, filed, accession="a"):
        self.form, self.filed, self.accession = form, filed, accession


def test_a_late_notice_answered_by_its_own_report_is_not_a_step():
    """Rule 12b-25 grants five calendar days for a 10-Q. The notice and the
    10-Q that follows are one reporting event split across two filings; 28 of
    48 chains on the page were this."""
    notice = E("NT 10-Q", "2026-08-14")
    report = E("10-Q", "2026-08-19")
    assert _is_extension_of(notice, [report]) is True


def test_the_annual_window_is_longer_than_the_quarterly_one():
    assert _is_extension_of(E("NT 10-K", "2026-03-02"), [E("10-K", "2026-03-20")]) is True
    # the same 18-day gap is well outside the quarterly extension
    assert _is_extension_of(E("NT 10-Q", "2026-03-02"), [E("10-Q", "2026-03-20")]) is False


def test_a_different_report_is_a_real_follow_on():
    """CreditRiskMonitor filed NT 10-Q and then a 10-K/A. That is not the
    report the notice deferred, so the progression stands."""
    assert _is_extension_of(E("NT 10-Q", "2026-08-14"), [E("10-K/A", "2026-08-19")]) is False


def test_an_8k_after_a_late_notice_is_a_real_follow_on():
    """Solesence: late filing, then a restatement 8-K a week later. An 8-K is
    not the deferred report, so this must survive."""
    assert _is_extension_of(E("NT 10-Q", "2026-08-14"), [E("8-K", "2026-08-21")]) is False


def test_a_report_filed_long_after_the_notice_is_not_the_extension():
    assert _is_extension_of(E("NT 10-Q", "2026-08-14"), [E("10-Q", "2026-11-01")]) is False


def test_a_non_notice_is_never_an_extension():
    assert _is_extension_of(E("8-K", "2026-08-14"), [E("10-Q", "2026-08-16")]) is False


def _render_rates(stats):
    from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

    from pipeline import config

    env = Environment(loader=ChoiceLoader([
        DictLoader({"base.html": "{% block content %}{% endblock %}"}),
        FileSystemLoader(config.TEMPLATES)]))
    env.filters.update(exact=str, figure=str)
    return env.get_template("sequences.html").render(
        history=stats, sequences=[], sequences_total=0,
        rates_chart="<div>published-rate-chart</div>")


def test_legacy_rates_are_withdrawn_until_the_new_method_is_computed():
    html = _render_rates({"companies": 2397, "rows": [{"rate": "41%"}]})
    assert "awaiting a rebuild" in html
    assert "41%" not in html
    assert "published-rate-chart" not in html
    assert "full filing history" not in html


def test_current_rates_disclose_unavailable_and_immature_coverage():
    html = _render_rates({
        "methodology_version": 2, "as_of": "2026-09-24", "companies": 7,
        "observation_start": "2004-08-23",
        "requested_companies": 9, "omitted_companies": 2,
        "total_historical_events": 20, "window_days": 540,
        "rows": [{"label": "Late filing followed by non-reliance", "eligible": 4,
                  "followed": 1, "immature": 3, "rate": "25%"}]})
    assert "published-rate-chart" in html
    assert "2026-09-24" in html
    assert "2 companies were" in html
    assert "window still open" in html
    assert "25%" in html
    assert "earliest observed" in html
    assert "2004-08-23" in html
    assert "earlier late notices are excluded" in html
