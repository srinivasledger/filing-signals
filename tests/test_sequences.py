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
