"""The link-preview card (Open Graph image).

What WhatsApp, Slack, iMessage and the rest show when the URL is shared. Three
constraints shape it:

  * It must be a raster image at an absolute URL. SVG is not rendered by
    WhatsApp, so this is a PNG.
  * WhatsApp declines to show previews over roughly 300 KB. Flat colour keeps
    this well under 60 KB.
  * The URL has to stay stable. Sharing services cache aggressively by URL, so
    a hashed filename would orphan every previously shared link.

The card is regenerated every run, so the figures on it are the live ones.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import config

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 1200, 630

# The site's dark surface, so the card matches the page it opens.
BG = (17, 22, 27)
PANEL = (21, 27, 33)
INK = (231, 236, 241)
MUTED = (133, 147, 160)
RULE = (42, 52, 61)

# The validated signal palette, dark steps.
STRIP = [(57, 135, 229), (217, 89, 38), (25, 158, 112), (201, 133, 0),
         (213, 81, 129), (0, 131, 0), (144, 133, 233), (230, 103, 103)]

# Rendered by CI, not by a developer's machine, so Linux paths come first.
_FONT_CANDIDATES = {
    "bold": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ],
    "regular": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ],
    "mono": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Courier.ttc",
    ],
}


def _font(kind: str, size: int):
    """Load a face, or raise. A silent fallback to Pillow's bitmap font would
    publish an unreadable card rather than fail visibly."""
    from PIL import ImageFont

    for path in _FONT_CANDIDATES[kind]:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    raise RuntimeError(
        f"no {kind} font found for the preview card; tried "
        + ", ".join(_FONT_CANDIDATES[kind])
    )


def build(stats: Dict[str, object], out: Optional[Path] = None) -> Optional[Path]:
    """Draw the card. Returns the path, or None if Pillow is unavailable."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        log.warning("Pillow not installed; skipping the link-preview image")
        return None

    out = out or (config.PUBLIC / "og.png")
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    d = ImageDraw.Draw(img)

    pad = 72
    d.rectangle([0, 0, WIDTH, 8], fill=PANEL)
    x = 0
    for colour in STRIP:
        w = WIDTH / len(STRIP)
        d.rectangle([x, 0, x + w, 8], fill=colour)
        x += w

    d.text((pad, 92), "FILING SIGNALS", font=_font("mono", 30), fill=MUTED)

    headline = "Restatements, auditor changes and\ngoing-concern language, tracked daily"
    d.multiline_text((pad, 150), headline, font=_font("bold", 58), fill=INK, spacing=16)

    d.line([(pad, 330), (WIDTH - pad, 330)], fill=RULE, width=1)

    figures: List[Tuple[str, str]] = [
        (f"{stats.get('events', 0):,}", "EVENTS"),
        (f"{stats.get('companies', 0):,}", "COMPANIES"),
        (f"{stats.get('days', 0)}", "FILING DAYS"),
        (str(stats.get("flag_rate", "—")), "FLAGGED"),
    ]
    col = (WIDTH - pad * 2) / len(figures)
    for i, (value, label) in enumerate(figures):
        cx = pad + col * i
        d.text((cx, 372), value, font=_font("bold", 62), fill=INK)
        d.text((cx, 452), label, font=_font("mono", 22), fill=MUTED)

    d.line([(pad, 520), (WIDTH - pad, 520)], fill=RULE, width=1)
    footer = f"Source: SEC EDGAR  ·  through {stats.get('through', '')}  ·  updated automatically"
    d.text((pad, 548), footer, font=_font("mono", 24), fill=MUTED)

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG", optimize=True)

    size_kb = out.stat().st_size / 1024
    if size_kb > 300:
        log.warning("preview card is %.0f KB; WhatsApp declines previews over ~300 KB",
                    size_kb)
    log.info("link-preview card: %s (%.0f KB)", out.name, size_kb)
    return out
