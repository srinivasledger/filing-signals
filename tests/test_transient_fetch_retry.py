"""A temporary document failure must leave its entire filing day retryable."""
import datetime as dt
import io
import urllib.error
from unittest import mock

import pytest

from pipeline import compare, config, enrich, fetch, health, letters, run


URL = "https://www.sec.gov/Archives/edgar/data/1/2/filename1.pdf"


class _Response:
    headers = {}

    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.data


def _http_error(code):
    return urllib.error.HTTPError(URL, code, "test", {}, None)


@pytest.mark.parametrize("problem", [_http_error(503), TimeoutError("timed out")],
                         ids=["http-503", "timeout"])
def test_exhausted_transport_failure_is_typed_and_retried(problem, tmp_path):
    with mock.patch.object(config, "CACHE_DIR", tmp_path), \
         mock.patch.object(config, "require_user_agent", return_value="Test Reader reader@example.com"), \
         mock.patch.object(config, "SEC_MAX_RETRIES", 2), \
         mock.patch.object(fetch._limiter, "wait"), \
         mock.patch.object(fetch.time, "sleep"), \
         mock.patch.object(fetch.urllib.request, "urlopen", side_effect=[problem, problem]) as call:
        with pytest.raises(fetch.TransientFetchError, match="filename1.pdf"):
            fetch.get_bytes(URL)
        assert call.call_count == 2


@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
def test_corrupt_compressed_transport_retries_without_caching(encoding, tmp_path):
    response = _Response(b"incomplete compressed document")
    response.headers = {"Content-Encoding": encoding}
    with mock.patch.object(config, "CACHE_DIR", tmp_path), \
         mock.patch.object(config, "require_user_agent", return_value="Test Reader reader@example.com"), \
         mock.patch.object(config, "SEC_MAX_RETRIES", 2), \
         mock.patch.object(fetch._limiter, "wait"), \
         mock.patch.object(fetch.time, "sleep"), \
         mock.patch.object(fetch.urllib.request, "urlopen", return_value=response) as call:
        with pytest.raises(fetch.TransientFetchError):
            fetch.get_bytes(URL)
        assert call.call_count == 2
        assert not fetch._cache_path(URL, None).exists()


def test_pdf_byte_transport_caches_exact_bytes_and_preserves_403(tmp_path):
    pdf = b"%PDF-1.4\n\xff\x00raw binary"
    with mock.patch.object(config, "CACHE_DIR", tmp_path), \
         mock.patch.object(config, "require_user_agent", return_value="Test Reader reader@example.com"), \
         mock.patch.object(fetch._limiter, "wait"), \
         mock.patch.object(fetch.urllib.request, "urlopen", return_value=_Response(pdf)) as call:
        assert fetch.get_bytes(URL) == pdf
        assert fetch.get_bytes(URL) == pdf
        assert call.call_count == 1

    with mock.patch.object(config, "CACHE_DIR", tmp_path / "other"), \
         mock.patch.object(config, "require_user_agent", return_value="Test Reader reader@example.com"), \
         mock.patch.object(fetch._limiter, "wait"), \
         mock.patch.object(fetch.urllib.request, "urlopen", side_effect=_http_error(403)):
        with pytest.raises(fetch.SECBlocked):
            fetch.get_bytes(URL)

    with mock.patch.object(config, "CACHE_DIR", tmp_path / "missing"), \
         mock.patch.object(config, "require_user_agent", return_value="Test Reader reader@example.com"), \
         mock.patch.object(fetch._limiter, "wait"), \
         mock.patch.object(fetch.urllib.request, "urlopen", side_effect=_http_error(404)):
        assert fetch.get_bytes(URL, accept_404=True) is None
        with pytest.raises(fetch.NotFound):
            fetch.get_bytes(URL)


def test_pdf_reader_uses_shared_transport_and_failure_escapes():
    page = mock.Mock()
    page.extract_text.return_value = "SEC comment letter"
    reader = mock.Mock(pages=[page])
    with mock.patch.object(fetch, "get_bytes", return_value=b"pdf bytes") as transport, \
         mock.patch("pypdf.PdfReader", return_value=reader) as pdf_reader:
        assert letters.load_letter(URL) == "SEC comment letter"
        transport.assert_called_once_with(URL)
        assert isinstance(pdf_reader.call_args.args[0], io.BytesIO)
        assert pdf_reader.call_args.args[0].getvalue() == b"pdf bytes"

    with mock.patch.object(fetch, "get_bytes",
                           side_effect=fetch.TransientFetchError("503 from PDF")):
        with pytest.raises(fetch.TransientFetchError):
            letters.load_letter(URL)

    with mock.patch.object(fetch, "get_bytes", side_effect=fetch.NotFound(URL)):
        assert letters.load_letter(URL) is None


def test_pdf_failure_aborts_the_whole_filing_day():
    filing = mock.Mock(form="UPLOAD")
    with mock.patch.object(run.ingest, "fetch_day", return_value=[filing]), \
         mock.patch.object(run.universe, "filter_forms", return_value=[filing]), \
         mock.patch.object(run.enrich, "enrich", return_value=True), \
         mock.patch.object(run.universe, "filter_operating", return_value=[filing]), \
         mock.patch.object(run.letters, "analyse_letter",
                           side_effect=fetch.TransientFetchError("503 from PDF")):
        with pytest.raises(fetch.TransientFetchError):
            run.process_day(dt.date(2026, 10, 22))


@pytest.mark.parametrize("read", [
    lambda: compare.load_text(URL),
    lambda: compare.submissions(1),
    lambda: enrich.enrich(mock.Mock(cik=1, accession="0000000000-26-000001")),
])
def test_document_readers_propagate_temporary_failure(read):
    failure = fetch.TransientFetchError("503")
    with mock.patch.object(fetch, "get", side_effect=failure), \
         mock.patch.object(fetch, "get_json", side_effect=failure):
        with pytest.raises(fetch.TransientFetchError):
            read()


def test_failed_older_explicit_day_is_queued_and_next_scheduled_run_retries():
    old = dt.date(2026, 10, 22)
    state = {"last_processed": "2026-10-23", "earliest_processed": "2026-01-02",
             "runs": []}
    report = {"checks": [], "summary": {"overall": "ok", "ok": 0, "warn": 0, "fail": 0}}
    with mock.patch.object(config, "require_user_agent", return_value="Test Reader reader@example.com"), \
         mock.patch.object(run.analyze, "get_analyzer"), \
         mock.patch.object(run.size, "load_or_refresh", return_value={}), \
         mock.patch.object(run, "process_day", side_effect=fetch.TransientFetchError("503 PDF")), \
         mock.patch.object(run.publish, "load_state", return_value=state), \
         mock.patch.object(run.publish, "load_all_events", return_value=[]), \
         mock.patch.object(run.publish, "append_events") as append_events, \
         mock.patch.object(run.publish, "save_state"), \
         mock.patch.object(run.publish, "repair_all", return_value=0), \
         mock.patch.object(run.health, "run_checks", return_value=report), \
         mock.patch.object(run.publish, "save_health"):
        assert run.main(["--date", old.isoformat(), "--no-render"]) == 0
        append_events.assert_not_called()

    assert state["last_processed"] == "2026-10-23"
    assert state["pending_dates"] == [old.isoformat()]
    assert state["runs"][-1]["failure_kind"] == "transient_fetch"
    assert not state["runs"][-1].get("blocked")  # a 503 is not a 403 refusal
    assert run.days_to_process(state, dt.date(2026, 10, 25),
                               end=dt.date(2026, 10, 23)) == [old]

    check = next(c for c in health.run_checks([], state, dt.date(2026, 10, 25))["checks"]
                 if c["name"] == "Incomplete filing days")
    assert check["status"] == "warn"
    assert old.isoformat() in check["detail"]


@pytest.mark.parametrize("result", [
    ([], {"date": "2026-10-22", "index_rows": 12, "candidates": 2,
          "operating": 2, "events": 0}),
    ([], None),
])
def test_queued_day_is_cleared_only_after_complete_scan(result):
    day = dt.date(2026, 10, 22)
    state = {"last_processed": "2026-10-23", "pending_dates": [day.isoformat()],
             "runs": []}
    report = {"checks": [], "summary": {"overall": "ok", "ok": 0, "warn": 0, "fail": 0}}
    with mock.patch.object(config, "require_user_agent", return_value="Test Reader reader@example.com"), \
         mock.patch.object(run.analyze, "get_analyzer"), \
         mock.patch.object(run.size, "load_or_refresh", return_value={}), \
         mock.patch.object(run, "process_day", return_value=result), \
         mock.patch.object(run.publish, "load_state", return_value=state), \
         mock.patch.object(run.publish, "load_all_events", return_value=[]), \
         mock.patch.object(run.publish, "append_events", return_value=0), \
         mock.patch.object(run.publish, "save_state"), \
         mock.patch.object(run.publish, "repair_all", return_value=0), \
         mock.patch.object(run.health, "run_checks", return_value=report), \
         mock.patch.object(run.publish, "save_health"):
        assert run.main(["--date", day.isoformat(), "--no-render"]) == 0
    assert state["pending_dates"] == ([] if result[1] is not None else [day.isoformat()])
    if result[1] is None:
        assert day.isoformat() not in state.get("no_filings", [])


def test_explicit_retry_overrides_an_old_incorrect_no_index_mark():
    day = dt.date(2026, 10, 22)
    state = {"last_processed": "2026-10-23", "no_filings": [day.isoformat()],
             "pending_dates": [day.isoformat()]}
    assert run.days_to_process(state, dt.date(2026, 10, 25),
                               end=dt.date(2026, 10, 23)) == [day]


def test_long_outage_catches_up_in_batches_without_losing_earlier_days(monkeypatch):
    monkeypatch.setattr(config, "MAX_BACKFILL_DAYS", 3)
    state = {"last_processed": "2026-10-01"}
    end = dt.date(2026, 10, 23)
    expected = run.business_days(dt.date(2026, 10, 2), end)
    recovered = []
    while True:
        batch = run.days_to_process(state, dt.date(2026, 10, 25), end=end)
        if not batch:
            break
        assert 1 <= len(batch) <= 3
        recovered.extend(batch)
        state["last_processed"] = batch[-1].isoformat()
    assert recovered == expected
    assert len(recovered) == len(set(recovered))
