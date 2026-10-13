"""A render-only Pages deploy must obey the same integrity gates as a scan."""

import copy

import pytest

from pipeline import config, health, models, publish, render, sections


@pytest.fixture
def isolated_render(tmp_path, monkeypatch):
    """Render small, synthetic data without changing the tracked data/state."""
    public = tmp_path / "public"
    box = {"report": {"checks": [], "summary": health._summarise([])}}
    monkeypatch.setattr(config, "PUBLIC", public)
    monkeypatch.setattr(publish, "load_all_events", lambda: [])
    monkeypatch.setattr(publish, "load_state", lambda: {
        "last_processed": "2026-10-09", "runs": []})
    monkeypatch.setattr(publish, "load_health",
                        lambda: copy.deepcopy(box["report"]))
    monkeypatch.setattr(publish, "save_health",
                        lambda report: box.update(report=copy.deepcopy(report)))
    monkeypatch.setattr(publish, "load_history", lambda: {})
    monkeypatch.setattr(render.preview, "build", lambda _: None)
    return public, box


def test_current_quote_contradiction_stops_render_before_output(isolated_render,
                                                                 monkeypatch):
    public, box = isolated_render
    public.mkdir()
    sentinel = public / "existing-page.html"
    sentinel.write_text("previous good build")
    contradictory = models.Event(
        signal_type=models.MATERIAL_WEAKNESS, confidence=models.DERIVED,
        company="Example Corp", cik=1, form="10-K", filed="2026-10-08",
        accession="0001-26-000001", filing_url="https://www.sec.gov/x",
        headline="Control effectiveness changed",
        evidence={"current_state": sections.ICFR_EFFECTIVE},
        quote="Management cannot conclude that internal control over "
              "financial reporting is effective.",
    )
    assert health._known_quote_contradictions([contradictory]) == [contradictory]
    monkeypatch.setattr(publish, "load_all_events", lambda: [contradictory])

    with pytest.raises(RuntimeError, match="Known quote contradictions"):
        render.build()

    assert sentinel.read_text() == "previous good build"
    assert not (public / "index.html").exists()
    assert box["report"]["checks"] == []  # no stale health report was needed


def test_saved_health_failure_stops_render_before_output(isolated_render):
    public, box = isolated_render
    box["report"] = {"checks": [{"name": "Every entry cites its filing",
                                   "status": health.FAIL, "detail": "bad link"}],
                     "summary": {"overall": health.FAIL, "fail": 1}}
    with pytest.raises(RuntimeError, match="Saved health checks are failing"):
        render.build()
    assert not public.exists()


def test_empty_synthetic_render_still_succeeds(isolated_render):
    public, box = isolated_render
    render.build()
    assert (public / "index.html").exists()
    assert box["report"]["summary"]["fail"] == 0


def test_generated_health_failure_blocks_standalone_render(isolated_render,
                                                          monkeypatch):
    _, box = isolated_render
    monkeypatch.setattr(health, "page_weight_check", lambda _: {
        "name": "Page weight", "status": health.FAIL, "detail": "too large"})
    with pytest.raises(RuntimeError, match="Generated site health checks failed: Page weight"):
        render.build()
    assert box["report"]["summary"]["fail"] == 1


def test_final_page_agreement_failure_blocks_standalone_render(isolated_render,
                                                                monkeypatch):
    _, box = isolated_render
    answers = iter([{"name": "Pages agree", "status": health.OK,
                     "detail": "first pass agrees"},
                    {"name": "Pages agree", "status": health.FAIL,
                     "detail": "final pages disagree"}])
    monkeypatch.setattr(health, "pages_agree_check", lambda _: next(answers))
    with pytest.raises(RuntimeError, match="Generated site health checks failed: Pages agree"):
        render.build()
    assert box["report"]["summary"]["fail"] == 1


def test_generated_check_exception_is_not_logged_and_ignored(isolated_render,
                                                               monkeypatch):
    def cannot_check(_):
        raise ValueError("page inspection failed")

    monkeypatch.setattr(health, "period_options_check", cannot_check)
    with pytest.raises(ValueError, match="page inspection failed"):
        render.build()
