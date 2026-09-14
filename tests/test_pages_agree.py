"""Every published page describes the same build of the same data.

One render pass makes one artifact, so the pages cannot disagree - but a
reader who finds the home page and /status quoting different totals has no
way to know that, and no way to tell which one is the site. So the build reads
its own stamp back off every page, and a run whose pages do not agree is a
run that ships nothing.
"""
from unittest import mock

from pipeline import health


def _page(root, name, built="2026-09-12 17:01 UTC", through="2026-09-11",
          events="4085", stamped=True):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    attrs = (f' data-built="{built}" data-through="{through}" data-events="{events}"'
             if stamped else "")
    p.write_text(f'<!doctype html>\n<html lang="en"{attrs}>\n<head><title>{name}'
                 f'</title></head><body><p>page</p></body></html>\n')
    return p


def test_one_build_passes_and_says_what_it_found(tmp_path):
    _page(tmp_path, "index.html")
    _page(tmp_path, "status.html")
    _page(tmp_path, "company/1000228.html")          # nested pages count too
    check = health.pages_agree_check(tmp_path)
    assert check["status"] == health.OK
    assert "all 3 pages" in check["detail"]
    assert "2026-09-12 17:01 UTC" in check["detail"]
    assert "2026-09-11" in check["detail"]
    assert "4,085 events" in check["detail"]         # the count, made readable


def test_a_page_from_an_older_build_fails_and_is_named(tmp_path):
    _page(tmp_path, "index.html", built="2026-09-02 16:31 UTC",
          through="2026-09-01", events="910")
    _page(tmp_path, "status.html")
    _page(tmp_path, "signals.html")
    check = health.pages_agree_check(tmp_path)
    assert check["status"] == health.FAIL
    assert "2 different builds" in check["detail"]
    assert "index.html" in check["detail"]
    assert "910 events" in check["detail"] and "4085 events" in check["detail"]


def test_a_page_without_the_stamp_fails(tmp_path):
    # A template that stopped extending base.html would produce exactly this.
    _page(tmp_path, "index.html")
    _page(tmp_path, "odd.html", stamped=False)
    check = health.pages_agree_check(tmp_path)
    assert check["status"] == health.FAIL
    assert "1 of 2 pages carry no build stamp" in check["detail"]
    assert "odd.html" in check["detail"]


def test_an_empty_build_is_unknown(tmp_path):
    assert health.pages_agree_check(tmp_path)["status"] == health.UNKNOWN


def test_the_stamp_is_on_the_real_base_template():
    # The check is only as good as the stamp it reads. This is the line that
    # every page inherits.
    from pipeline import config
    base = (config.TEMPLATES / "base.html").read_text()
    assert ('<html lang="en" data-built="{{ built_at }}" '
            'data-through="{{ data_through }}" data-events="{{ event_total }}">'
            in base)


def test_a_failing_post_render_check_fails_the_run(tmp_path):
    """The exit code has to see what the build measured.

    The checks that need the finished pages are appended by the build and saved
    to the health file. Judged from the pre-render report alone, one of them
    failing turned the status badge red and deployed anyway.
    """
    from pipeline import config, run

    pre_render = {"checks": [{"name": "Every entry cites a filing",
                              "status": "ok", "detail": "fine"}],
                  "summary": {"overall": "ok", "ok": 1, "warn": 0, "fail": 0,
                              "total": 1}}
    after_build = {"checks": pre_render["checks"] + [
        {"name": "Pages agree", "status": "fail",
         "detail": "2 pages come from 2 different builds"}]}

    def fake_build(*a, **k):
        fake_build.called = True
    fake_build.called = False

    with mock.patch.object(config, "require_user_agent", lambda: None), \
         mock.patch.object(config, "HISTORY_FROM", None), \
         mock.patch.object(run, "days_to_process", return_value=[]), \
         mock.patch.object(run, "days_to_backfill", return_value=[]), \
         mock.patch.object(run.analyze, "get_analyzer"), \
         mock.patch.object(run.size, "load_or_refresh", return_value={}), \
         mock.patch.object(run.history, "sequence_rates", return_value={}), \
         mock.patch.object(run.publish, "save_history"), \
         mock.patch.object(run.publish, "load_state",
                           return_value={"runs": [], "last_processed": "2026-09-11"}), \
         mock.patch.object(run.publish, "save_state"), \
         mock.patch.object(run.health, "run_checks", return_value=pre_render), \
         mock.patch.object(run.publish, "save_health"), \
         mock.patch.object(run.publish, "load_health", return_value=after_build):
        import pipeline.render as render
        with mock.patch.object(render, "build", fake_build):
            code = run.main([])

    assert fake_build.called
    assert code == 1, "a post-render failure must stop the commit"


def test_a_clean_post_render_report_keeps_the_run_green():
    from pipeline import config, run

    report = {"checks": [{"name": "Pages agree", "status": "ok", "detail": "all"}],
              "summary": {"overall": "ok", "ok": 1, "warn": 0, "fail": 0, "total": 1}}

    with mock.patch.object(config, "require_user_agent", lambda: None), \
         mock.patch.object(config, "HISTORY_FROM", None), \
         mock.patch.object(run, "days_to_process", return_value=[]), \
         mock.patch.object(run, "days_to_backfill", return_value=[]), \
         mock.patch.object(run.analyze, "get_analyzer"), \
         mock.patch.object(run.size, "load_or_refresh", return_value={}), \
         mock.patch.object(run.history, "sequence_rates", return_value={}), \
         mock.patch.object(run.publish, "save_history"), \
         mock.patch.object(run.publish, "load_state",
                           return_value={"runs": [], "last_processed": "2026-09-11"}), \
         mock.patch.object(run.publish, "save_state"), \
         mock.patch.object(run.health, "run_checks", return_value=report), \
         mock.patch.object(run.publish, "save_health"), \
         mock.patch.object(run.publish, "load_health", return_value=report):
        import pipeline.render as render
        with mock.patch.object(render, "build", lambda *a, **k: None):
            assert run.main([]) == 0


def test_a_real_build_removes_pages_it_no_longer_makes(tmp_path):
    """Pages were overwritten, never removed. A company whose last entry was
    dropped kept its page from the build that made it - a different date and
    a different total on a page still linked from search. The output has to be
    one build wherever it is made, so the render clears what it made before."""
    import copy

    from pipeline import config, publish, render

    if not (config.DATA / "events").exists():
        import pytest
        pytest.skip("no data to build from")

    stale = tmp_path / "company" / "9999999.html"
    stale.parent.mkdir(parents=True)
    stale.write_text('<!doctype html>\n<html lang="en" data-built="2026-08-28 '
                     '17:42 UTC" data-through="2026-08-27" data-events="910">'
                     '<body>old</body></html>\n')
    (tmp_path / "orphan.html").write_text("<html lang=\"en\"><body>x</body></html>")

    # Health is read and written by the build; keep both away from data/state.
    scratch = {"report": copy.deepcopy(publish.load_health())}
    with mock.patch.object(config, "PUBLIC", tmp_path), \
         mock.patch.object(publish, "load_health",
                           side_effect=lambda: copy.deepcopy(scratch["report"])), \
         mock.patch.object(publish, "save_health",
                           side_effect=lambda r: scratch.update(report=r)):
        render.build()

    assert not stale.exists(), "a page from an earlier build survived"
    assert not (tmp_path / "orphan.html").exists()
    assert (tmp_path / "index.html").exists()
    final = health.pages_agree_check(tmp_path)
    assert final["status"] == health.OK, final["detail"]
    saved = {c["name"]: c for c in scratch["report"]["checks"]}
    assert saved["Pages agree"]["status"] == health.OK
