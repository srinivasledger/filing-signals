"""The home page holds a window, not the whole record.

Truncating is fine; truncating silently is not. The page carries a search box
and filter chips, so a reader who filters a quietly-shortened set gets an answer
that looks complete and is wrong. This pins the notice to the truncation.
"""
import re

from jinja2 import Environment, FileSystemLoader, select_autoescape

from pipeline import config


def _render(total, truncated):
    env = Environment(loader=FileSystemLoader(str(config.TEMPLATES)),
                      autoescape=select_autoescape(["html"]))
    src = (config.TEMPLATES / "index.html").read_text()
    # Render the record header alone: the surrounding page needs the whole
    # event model, and the contract under test is only this block.
    block = src[src.index('<p class="eyebrow"><span>The record'):
                src.index('<section class="controls">')]
    return env.from_string(block).render(total_events=total, truncated=truncated, rel="")


def test_a_truncated_home_page_says_so():
    html = _render(344, 244)
    assert "not the whole record" in html
    assert "most recent 100 of" in html and "344" in html
    assert "244" in html
    # and it must point somewhere complete
    assert "signals.html" in html and "events.json" in html


def test_an_untruncated_home_page_stays_quiet():
    html = _render(344, 0)
    assert "not the whole record" not in html
    assert "344 entries" in html


def test_the_notice_is_singular_for_one_hidden_entry():
    assert "1 older entry is not" in re.sub(r"\s+", " ", _render(401, 1))
