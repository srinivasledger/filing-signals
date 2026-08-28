"""Page weight is watched, not assumed.

Every list page grows with the record and nothing prunes them, so "it stays
fast" has to be something the build observes rather than something asserted
once. These pin both directions of that check.
"""
import gzip

from pipeline import health


def _page(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(body)
    return p


def test_a_light_build_passes_and_names_the_heaviest_page(tmp_path):
    _page(tmp_path, "index.html", "<p>small</p>")
    _page(tmp_path, "signals.html", "<p>" + "x" * 5000 + "</p>")
    check = health.page_weight_check(tmp_path)
    assert check["status"] == health.OK
    assert "signals.html" in check["detail"]      # the heaviest, not the first
    assert "2 pages checked" in check["detail"]


def test_a_heavy_page_warns_rather_than_failing(tmp_path):
    # Incompressible bytes, so the gzipped size really does exceed the limit.
    import os
    big = os.urandom(health.MAX_PAGE_WIRE_BYTES + 50_000).hex()
    _page(tmp_path, "signals.html", big)
    check = health.page_weight_check(tmp_path)
    assert check["status"] == health.WARN
    assert "split it by year" in check["detail"]
    # a slow page is not a wrong page: it must never fail the run
    assert check["status"] != health.FAIL


def test_an_empty_build_is_unknown_not_ok(tmp_path):
    assert health.page_weight_check(tmp_path)["status"] == health.UNKNOWN
