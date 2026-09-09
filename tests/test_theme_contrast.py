"""Colour that is present but not readable.

The nine series hues were picked to be distinguishable as chart FILLS, then
reused as the colour of the small uppercase tag on every card. As fills they
are fine. As text on a light background, auditor changes sat at 1.9:1 and CFO
departures at 2.3:1 - the reason light mode read as an afterthought. Dark was
better but not clean either: going concern was 3.5:1 on a card.

Each theme therefore carries a second set, `--series-N-ink`: the same hues with
the lightness moved until they clear the WCAG AA threshold for normal text.
This pins that, because a palette is exactly the kind of thing that gets
extended later by copying the line above it.
"""
import re
from pathlib import Path

import pytest

CSS = (Path(__file__).resolve().parent.parent / "site" / "static" / "style.css").read_text()
AA_NORMAL_TEXT = 4.5

SLOTS = [f"series-{n}" for n in range(1, 9)] + ["series-muted"]


def _luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    channels = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _block(name: str) -> str:
    patterns = {
        "light": r"^:root\{(.*?)^\}",
        "dark": r':root\[data-theme="dark"\]\{(.*?)^\}',
    }
    m = re.search(patterns[name], CSS, re.S | re.M)
    assert m, f"{name} theme block not found"
    return m.group(1)


def _tokens(block: str) -> dict:
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{3,6})", block))


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("slot", SLOTS)
def test_every_tag_colour_is_readable_as_text(theme, slot):
    tokens = _tokens(_block(theme))
    ink = tokens.get(f"{slot}-ink")
    assert ink, f"{theme} has no --{slot}-ink"
    # A tag appears on a card and on the page behind it, so both have to pass.
    for ground in ("bg", "surface"):
        got = contrast(ink, tokens[ground])
        assert got >= AA_NORMAL_TEXT, (
            f"{theme} --{slot}-ink {ink} is {got:.2f}:1 on --{ground} "
            f"{tokens[ground]}, below {AA_NORMAL_TEXT}")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_secondary_text_is_readable(theme):
    """The mono labels carry this design's structure; light had them at 4.24."""
    tokens = _tokens(_block(theme))
    for token in ("muted", "ink-2", "ink"):
        got = contrast(tokens[token], tokens["bg"])
        assert got >= AA_NORMAL_TEXT, (
            f"{theme} --{token} is {got:.2f}:1 on the page background")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_a_tag_keeps_its_hue(theme):
    """Legibility is not licence to recolour a signal. The ink is the same hue
    as the fill, only darker or lighter - so a tag and its chart segment still
    read as the same thing."""
    import colorsys

    tokens = _tokens(_block(theme))
    for slot in SLOTS:
        fill, ink = tokens[slot], tokens[f"{slot}-ink"]
        hues = []
        for value in (fill, ink):
            h = value.lstrip("#")
            r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
            hues.append(colorsys.rgb_to_hls(r, g, b)[0])
        drift = min(abs(hues[0] - hues[1]), 1 - abs(hues[0] - hues[1]))
        assert drift < 0.03, f"{theme} {slot}: {fill} -> {ink} changed hue"


# --- the masthead is its own surface -----------------------------------------
def test_the_masthead_palette_is_readable():
    """Light mode was a white bar on a near-white page: correct colours, no
    anchor. The bar is dark in both themes now, which means everything sitting
    on it - nav, brand, the self-check dot - is measured against the bar and
    not against the page."""
    tokens = _tokens(_block("light"))          # header tokens live in :root
    head = tokens["head-bg"]
    text = {"head-ink": AA_NORMAL_TEXT, "head-ink-2": AA_NORMAL_TEXT,
            "head-muted": AA_NORMAL_TEXT,
            # the status dot is a 5px graphic, not text
            "head-ok": 3.0, "head-warn": 3.0, "head-fail": 3.0,
            "series-2": 3.0}                   # brand dot and active-tab rule
    for token, need in text.items():
        got = contrast(tokens[token], head)
        assert got >= need, (
            f"--{token} {tokens[token]} is {got:.2f}:1 on the masthead {head}, "
            f"below {need}")


def test_the_masthead_separates_from_the_page_in_both_themes():
    """A bar the same value as the page behind it is not a bar."""
    head = _tokens(_block("light"))["head-bg"]
    light_page = _tokens(_block("light"))["bg"]
    dark_page = _tokens(_block("dark"))["bg"]
    assert contrast(head, light_page) >= 3, "invisible against the light page"
    # Against the dark page the values are close by design, so the hairline
    # carries the separation; assert the hairline can be seen instead.
    assert contrast(_tokens(_block("light"))["head-line"], dark_page) >= 1.3
