"""Graph coverage and forms must match its filterable data."""
import json
from types import SimpleNamespace

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

from pipeline import charts, config


def _event(day, form="10-Q"):
    return SimpleNamespace(filed=day, signal_type="restatement", size_tier="small",
                           routine=False, form=form)


def test_chart_payload_uses_the_same_recent_days_and_form_families():
    events = [_event(f"2026-09-{i:02d}") for i in range(1, 26)]
    events += [_event("2026-09-25", "10-Q/A"), _event("2026-09-25", "CORRESP")]
    html = charts.chart_data(events)
    data = json.loads(html[html.index(">") + 1:html.rindex("</script>")])
    assert data["days"] == charts.activity_days(events)
    assert len(data["days"]) == 20 and data["days"][0] == "2026-09-06"
    assert {r["d"] for r in data["rows"]} == set(data["days"])
    assert [r["f"] for r in data["rows"][-2:]] == ["10-Q", "LETTER"]


def test_home_graph_is_visible_and_labels_the_plotted_window_without_javascript():
    env = Environment(loader=ChoiceLoader([
        DictLoader({"base.html": "{% block content %}{% endblock %}",
                    "_event.html": "{% macro card(e, rel) %}{% endmacro %}"}),
        FileSystemLoader(config.TEMPLATES)]))
    env.filters.update(exact=str, figure=str)
    html = env.get_template("index.html").render(
        activity_chart="<svg></svg>", activity_days=["2026-09-30", "2026-10-01"],
        days_covered=196, events=[], total_events=0, companies=0, asset_v={})
    assert '<details class="activity-panel" open>' in html
    assert '<span id="chart-scope">2 filing days</span>' in html
    assert "2026-09-30 through 2026-10-01" in html
