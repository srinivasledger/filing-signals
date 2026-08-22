"""Inline SVG charts.

No chart library and no build step: the pipeline emits SVG strings that the
templates drop straight into the page. Colours are referenced as CSS custom
properties, so one stylesheet swaps the whole palette between light and dark
without regenerating anything.

Palette is the validated categorical set (six slots, fixed order, never
cycled). Light mode puts three slots under 3:1 against the surface, so the
relief rule applies and every series carries a visible legend label; the
sequences page also keeps its table.
"""
from __future__ import annotations

import html
from collections import Counter, OrderedDict
from typing import Dict, List, Sequence

from .models import (AUDITOR_CHANGE, GOING_CONCERN, LATE_FILING, POLICY_CHANGE,
                     RESTATEMENT, REVENUE_RECOGNITION, SIGNAL_LABELS)

# Fixed slot order. A signal keeps its colour regardless of how many appear.
SERIES_ORDER = [RESTATEMENT, AUDITOR_CHANGE, LATE_FILING, GOING_CONCERN,
                POLICY_CHANGE, REVENUE_RECOGNITION]
SERIES_VAR = {s: f"var(--series-{i + 1})" for i, s in enumerate(SERIES_ORDER)}

GAP = 2          # surface gap between stacked segments
RADIUS = 4       # rounded data-end


def _esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def _rounded_top(x: float, y: float, w: float, h: float, r: float) -> str:
    """Bar with rounded top corners only, anchored to the baseline."""
    r = max(0.0, min(r, h, w / 2))
    return (f"M{x:.1f},{y + h:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
            f"H{x + w - r:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} "
            f"V{y + h:.1f} Z")


def _rounded_right(x: float, y: float, w: float, h: float, r: float) -> str:
    r = max(0.0, min(r, w, h / 2))
    return (f"M{x:.1f},{y:.1f} H{x + w - r:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} "
            f"V{y + h - r:.1f} Q{x + w:.1f},{y + h:.1f} {x + w - r:.1f},{y + h:.1f} "
            f"H{x:.1f} Z")


def activity_chart(events, max_days: int = 20) -> str:
    """Stacked columns: events per filing day, split by signal type."""
    by_day: "OrderedDict[str, Counter]" = OrderedDict()
    for e in sorted(events, key=lambda x: x.filed):
        by_day.setdefault(e.filed, Counter())[e.signal_type] += 1
    days = list(by_day.items())[-max_days:]
    if not days:
        return ""

    present = [s for s in SERIES_ORDER if any(c.get(s) for _, c in days)]
    peak = max(sum(c.values()) for _, c in days) or 1

    W, H = 1000, 260
    pad_l, pad_r, pad_t, pad_b = 44, 12, 18, 42
    plot_w, plot_h = W - pad_l - pad_r, H - pad_t - pad_b
    slot = plot_w / len(days)
    bar_w = min(46, slot * 0.62)

    parts: List[str] = []

    # Recessive gridlines with a value axis.
    steps = 4
    for i in range(steps + 1):
        val = round(peak * i / steps)
        y = pad_t + plot_h - (plot_h * i / steps)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{W - pad_r}" y2="{y:.1f}" '
                     f'class="grid"/>')
        parts.append(f'<text x="{pad_l - 10}" y="{y + 4:.1f}" class="axis" '
                     f'text-anchor="end">{val}</text>')

    for idx, (day, counts) in enumerate(days):
        cx = pad_l + slot * idx + slot / 2
        x = cx - bar_w / 2
        total = sum(counts.values())
        y_cursor = pad_t + plot_h
        for sig in present:
            n = counts.get(sig, 0)
            if not n:
                continue
            seg_h = plot_h * n / peak
            drawn = max(1.0, seg_h - GAP)
            y_cursor -= seg_h
            top_seg = sig == [s for s in present if counts.get(s)][-1]
            path = (_rounded_top(x, y_cursor, bar_w, drawn, RADIUS) if top_seg
                    else f"M{x:.1f},{y_cursor:.1f} h{bar_w:.1f} v{drawn:.1f} h-{bar_w:.1f} Z")
            parts.append(
                f'<path d="{path}" fill="{SERIES_VAR[sig]}" class="seg">'
                f'<title>{_esc(day)} · {_esc(SIGNAL_LABELS[sig])}: {n}</title></path>')
        if total:
            parts.append(f'<text x="{cx:.1f}" y="{pad_t + plot_h - plot_h * total / peak - 7:.1f}" '
                         f'class="bar-total" text-anchor="middle">{total}</text>')
        label = day[5:]                       # MM-DD; the year is on the page
        parts.append(f'<text x="{cx:.1f}" y="{H - pad_b + 20:.1f}" class="axis" '
                     f'text-anchor="middle">{_esc(label)}</text>')

    legend = "".join(
        f'<span class="key"><i style="background:{SERIES_VAR[s]}"></i>'
        f'{_esc(SIGNAL_LABELS[s])}</span>' for s in present)

    return (f'<figure class="chart"><div class="chart-legend">{legend}</div>'
            f'<div class="chart-scroll">'
            f'<svg viewBox="0 0 {W} {H}" role="img" preserveAspectRatio="xMidYMid meet" '
            f'aria-label="Events per filing day by signal type">'
            f'{"".join(parts)}</svg></div></figure>')


def rates_chart(rows: Sequence[Dict]) -> str:
    """Follow-on rates as HTML bars.

    Deliberately not SVG. A 1000-unit viewBox scaled to a 375px phone renders
    13px labels at ~5px; HTML bars reflow instead, so the long sentence labels
    stay readable at every width. One measure, so one hue.
    """
    rows = [r for r in rows if r.get("eligible")]
    if not rows:
        return ""
    out = ['<div class="rates">']
    for r in rows:
        pct = (r["followed"] / r["eligible"] * 100) if r["eligible"] else 0
        out.append(
            f'<div class="rate">'
            f'<div class="rate-head"><span class="rate-label">{_esc(r["label"])}</span>'
            f'<span class="rate-value">{_esc(r["rate"])}</span></div>'
            f'<div class="rate-track"><div class="rate-fill" style="width:{pct:.1f}%"></div></div>'
            f'<div class="rate-foot">{r["followed"]} of {r["eligible"]} companies</div>'
            f'</div>')
    out.append("</div>")
    return "".join(out)


def mix_bar(events) -> str:
    """One-row stacked bar: the overall signal mix, used under the hero."""
    counts = Counter(e.signal_type for e in events)
    total = sum(counts.values())
    if not total:
        return ""
    present = [s for s in SERIES_ORDER if counts.get(s)]
    W, H = 1000, 14
    parts, x = [], 0.0
    for i, sig in enumerate(present):
        w = W * counts[sig] / total
        draw = max(2.0, w - (GAP if i < len(present) - 1 else 0))
        parts.append(f'<rect x="{x:.1f}" y="0" width="{draw:.1f}" height="{H}" rx="3" '
                     f'fill="{SERIES_VAR[sig]}"><title>{_esc(SIGNAL_LABELS[sig])}: '
                     f'{counts[sig]}</title></rect>')
        x += w
    return (f'<svg class="mixbar" viewBox="0 0 {W} {H}" preserveAspectRatio="none" '
            f'role="img" aria-label="Share of events by signal type">'
            f'{"".join(parts)}</svg>')
