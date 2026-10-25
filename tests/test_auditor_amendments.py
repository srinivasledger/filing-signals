"""A later auditor response letter must not duplicate the original change."""
from types import SimpleNamespace

import pytest

from pipeline import auditor, compare, fetch, triage
from pipeline.ingest import Filing
from pipeline.models import AUDITOR_CHANGE, RESTATEMENT


LETTER_ONLY = """Explanatory Note
An earlier report announced the replacement of our accounting firm.
This amendment is being filed solely to include the auditor's response letter
as Exhibit 16.1. Everything else in the earlier report is unchanged.
Item 4.01 Changes in Registrant's Certifying Accountant
The original report described our dismissal of Firm A and appointment of Firm B.
"""


@pytest.mark.parametrize("purpose", [
    "This amendment is being filed solely to file, as Exhibit 16.1, the letter from Firm A.",
    "This report is filed only to furnish the letter as Exhibit 16.1.",
    "This amendment is being filed to update the disclosure to reference such letter "
    "and to file it as an exhibit. No other changes have been made to the original report. "
    "Exhibit 16.1: Letter from Firm A.",
    "This report is being filed to disclose that we received a copy of the letter, "
    "filed as Exhibit 16.1. Except as disclosed here, there were no other changes made.",
    "This report is being filed to disclose that we received a copy of the letter, "
    "filed as Exhibit 16.1. Except as disclosed here, there were no other changes "
    "to the original report.",
])
def test_explicit_letter_only_purposes_are_suppressed(purpose):
    assert triage.auditor_letter_only_amendment(purpose, "8-K/A")


@pytest.mark.parametrize("text", [
    # BioRestorative-style added details must survive, even with an Exhibit 16.
    "This amendment is being filed to include a letter as Exhibit 16.1 and provide "
    "additional information about the auditor transition. No other changes were made.",
    # Waste Energy and BestGofer add substantive explanations beside the letter.
    "This amendment is being filed to file a letter as Exhibit 16.1 and provide "
    "supplemental disclosure about a disagreement. No other changes have been made.",
    "This amendment is being filed to include Exhibit 16.1, the letter from Firm A, "
    "and correct the earlier disclosure about our request for that letter. "
    "No other changes have been made.",
    "This amendment is being filed solely to disclose the appointment of Firm B. "
    "An auditor letter is filed as Exhibit 16.1.",
    # Merely attaching the customary letter does not establish an administrative amendment.
    "The company dismissed Firm A and engaged Firm B. A letter is Exhibit 16.1.",
    "This report is being filed to include the letter as Exhibit 16.1.",
    "This amendment is being filed solely to correct a company name on the signature page. "
    "The original auditor letter remains Exhibit 16.1.",
    "",
])
def test_substantive_or_uncertain_amendments_remain_reportable(text):
    assert not triage.auditor_letter_only_amendment(text, "8-K/A")


def test_repeated_historical_changes_do_not_defeat_the_explanatory_note():
    assert triage.auditor_letter_only_amendment(LETTER_ONLY, "8-K/A")
    assert not triage.auditor_letter_only_amendment(LETTER_ONLY, "8-K")


def test_only_the_amendment_auditor_signal_is_removed(monkeypatch):
    monkeypatch.setattr(compare, "current_document", lambda *args: "https://sec.example/filing.htm")
    monkeypatch.setattr(compare, "load_text", lambda url: LETTER_ONLY)
    filing = Filing(form="8-K/A", company="Example", cik=123, filed="2026-10-23",
                    path="edgar/data/123/0000000123-26-000002.txt", items=["4.01", "4.02"])
    events = triage.events_from_filing(filing)
    assert [event.signal_type for event in events] == [RESTATEMENT]
    filing.form = "8-K"
    assert {event.signal_type for event in triage.events_from_filing(filing)} == {
        AUDITOR_CHANGE, RESTATEMENT}


def test_unreadable_amendment_is_not_silently_dropped(monkeypatch):
    monkeypatch.setattr(compare, "current_document", lambda *args: None)
    filing = Filing(form="8-K/A", company="Example", cik=123, filed="2026-10-23",
                    path="edgar/data/123/0000000123-26-000002.txt", items=["4.01"])
    assert len(triage.events_from_filing(filing)) == 1


@pytest.mark.parametrize("function", ["_subclassify", "_officer_event"])
def test_transient_read_failure_propagates_for_retry(monkeypatch, function):
    def unavailable(*args):
        raise fetch.TransientFetchError("SEC temporarily unavailable")

    monkeypatch.setattr(compare, "current_document", unavailable)
    filing = SimpleNamespace(cik=123, accession="0000000123-26-000002", form="8-K/A")
    with pytest.raises(fetch.TransientFetchError):
        if function == "_subclassify":
            triage._subclassify(AUDITOR_CHANGE, filing)
        else:
            triage._officer_event(filing)


REVERSED_CHANGE = '''EXPLANATORY NOTE
The original report disclosed the dismissal of Barton CPA PLLC and appointment
of Sadler, Gibb & Associates, LLC. This amendment reports a reversal.
Item 4.01 Changes in Registrant's Certifying Accountant
(a) Termination of Sadler, Gibb & Associates, LLC
On April 14, 2026, the Board terminated the engagement of Sadler, Gibb &
Associates, LLC ("Sadler Gibb") as the Company's independent registered public
accounting firm. Sadler Gibb was appointed on April 8, 2026.
There were no disagreements with Sadler Gibb.
(b) Reappointment of Barton CPA PLLC
The Board reappointed Barton CPA PLLC ("Barton") as the Company's independent
registered public accounting firm, effective immediately.
Item 5.02 Departure of Officers
The chief executive officer resigned.
'''


def test_reversed_amendment_uses_current_item_not_old_explanatory_note():
    detail = auditor.classify_401(REVERSED_CHANGE)
    assert detail['direction'] == 'dismissed'
    assert detail['predecessor_auditor'] == 'Sadler, Gibb & Associates, LLC'
    assert detail['successor_auditor'] == 'Barton CPA PLLC'
    assert not detail['disagreements_disclosed']


WITHDRAWN_ITEMS = '''Item 4.01 Changes in Registrant's Certifying Accountant
The Registrant hereby rescinds and withdraws all disclosures contained in
Item 4.01 of the prior report. Readers should no longer rely on that disclosure.
Item 5.02 Departure of Officers
The Registrant hereby rescinds and withdraws all disclosures contained in
Item 5.02 of the prior report, including prior statements that its chief
financial officer resigned.
Item 8.01 Other Events
This report solely gives notice of withdrawal of the prior report.
'''


def test_withdrawal_is_transparent_notice_and_never_a_new_officer_departure(monkeypatch):
    monkeypatch.setattr(compare, 'current_document', lambda *args: 'https://sec.example/filing.htm')
    monkeypatch.setattr(compare, 'load_text', lambda url: WITHDRAWN_ITEMS)
    filing = Filing(form='8-K/A', company='Example', cik=123, filed='2026-05-28',
                    path='edgar/data/123/0000000123-26-000002.txt', items=['4.01', '5.02'])
    events = triage.events_from_filing(filing)
    assert len(events) == 1
    event = events[0]
    assert event.signal_type == AUDITOR_CHANGE
    assert event.headline == 'Example withdrew its previous auditor-change disclosure'
    assert event.evidence['direction'] == 'disclosure_withdrawn'
    assert event.evidence['severity'] == 'normal'
    assert 'withdrew' in event.evidence['why']
    assert 'predecessor_auditor' not in event.evidence


def test_withdrawing_an_auditor_letter_does_not_mean_all_disclosures_withdrawn():
    text = 'Item 4.01\nThe Company withdraws the letter in Exhibit 16.1. Its auditor resigned.'
    assert not auditor.item_disclosure_withdrawn(text, '4.01')
    assert auditor.classify_401(text)['direction'] == 'resigned'


SIGNATURE_CORRECTION = '''Explanatory Note
This report is being filed to amend the original solely for the purpose of
correcting a scrivener's error regarding the name of the Registrant included on
the signature page of the Original Form 8-K. Except as described above, all
other information in, and the exhibits to, the Original Form 8-K, remain unchanged.
Item 4.01
The Company dismissed Firm A LLP and appointed Firm B LLP.
'''


def test_signature_name_correction_is_suppressed_but_original_survives(monkeypatch):
    monkeypatch.setattr(compare, 'current_document', lambda *args: 'https://sec.example/filing.htm')
    monkeypatch.setattr(compare, 'load_text', lambda url: SIGNATURE_CORRECTION)
    filing = Filing(form='8-K/A', company='Example', cik=123, filed='2026-07-30',
                    path='edgar/data/123/0000000123-26-000002.txt', items=['4.01'])
    assert triage.events_from_filing(filing) == []
    filing.form = '8-K'
    assert len(triage.events_from_filing(filing)) == 1


@pytest.mark.parametrize('text', [
    SIGNATURE_CORRECTION.replace('solely for the purpose of\n', ''),
    SIGNATURE_CORRECTION.replace('remain unchanged', 'are also updated'),
    SIGNATURE_CORRECTION.replace('name of the Registrant', 'date of the dismissal'),
    SIGNATURE_CORRECTION.replace('Original Form 8-K. Except',
                                 'Original Form 8-K and update the dismissal date. Except'),
])
def test_uncertain_or_substantive_corrections_are_retained(text):
    assert not triage.signature_name_only_amendment(text, '8-K/A')


def test_merger_intro_does_not_misidentify_the_outgoing_firm():
    text = '''Item
4.01. Changes in Registrant's Certifying Accountant.
The Company was notified that Simon & Edward LLP (“S&E”) acquired the attest
business of BCRG Group (“BCRG”). As a result, the Board simultaneously dismissed
BCRG as its independent registered public accounting firm and approved the
appointment of S&E as the new independent registered public accounting firm.
Item 9.01 Financial Statements and Exhibits
'''
    detail = auditor.classify_401(text)
    assert detail['direction'] == 'dismissed'
    assert detail['predecessor_auditor'] == 'BCRG Group'
    assert detail['successor_auditor'] == 'Simon & Edward LLP'


def test_undefined_abbreviation_does_not_invent_a_firm_name():
    detail = auditor.classify_401('The Company dismissed BCRG as its auditor.')
    assert detail['predecessor_auditor'] == ''
