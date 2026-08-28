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


def test_filter_controls_are_never_rendered_without_their_script():
    """The size filter shipped on three pages with no filter.js behind it: the
    chips rendered, clicked, and did nothing, because the script tag lived in
    the home page's own template. Loading it from the base template is what
    makes a new filtered page work without a second edit."""
    from pipeline import config

    base = (config.TEMPLATES / "base.html").read_text()
    assert "static/filter.js" in base

    # and no page template may re-declare it, or it would load twice
    others = [p for p in config.TEMPLATES.glob("*.html") if p.name != "base.html"]
    dupes = [p.name for p in others if "static/filter.js" in p.read_text()]
    assert not dupes, f"filter.js also included by {dupes}"
