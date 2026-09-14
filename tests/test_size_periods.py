"""The size index asks for the right years without being told them.

The XBRL frame periods were a written-down list ending in 2026. Nothing would
have failed in 2027: the cached index loads, the weekly refresh runs, the
2026 frames answer as before, and every company's size quietly stops moving.
The periods are now derived from the date, and no year is written into the
pipeline anywhere.
"""
import ast
import datetime as dt
import pathlib
import re
from unittest import mock

from pipeline import size


def test_quarter_ends_are_the_completed_ones_newest_first():
    got = size._quarter_ends(12, dt.date(2026, 9, 14))
    assert got[0] == "CY2026Q2I", "September: Q2 is the last completed quarter"
    assert got[-1] == "CY2023Q3I"
    assert len(got) == len(set(got)) == 12


def test_the_list_rolls_into_the_new_year_on_its_own():
    # Mid-January 2027: the last completed quarter is 2026 Q4, and the list
    # walks back across the year boundary.
    assert size._quarter_ends(4, dt.date(2027, 1, 15)) == [
        "CY2026Q4I", "CY2026Q3I", "CY2026Q2I", "CY2026Q1I"]
    # A quarter is not asked for until it has ended: on 31 December Q4 is
    # still open.
    assert size._quarter_ends(2, dt.date(2027, 12, 31)) == ["CY2027Q3I", "CY2027Q2I"]
    assert size._quarter_ends(1, dt.date(2028, 4, 1)) == ["CY2028Q1I"]


def test_build_index_asks_for_the_current_years_frames():
    asked = {}

    def fake_union(tag, periods, unit="USD"):
        asked[tag] = list(periods)
        return {}

    with mock.patch.object(size, "_union_frame", side_effect=fake_union):
        size.build_index(today=dt.date(2027, 4, 2))

    floats = asked["dei/EntityPublicFloat"]
    assert floats[0] == "CY2027Q1I"
    assert len(floats) == size.FLOAT_QUARTERS
    assert any(p.startswith("CY2024") for p in floats), "three years back"
    assert not any(p.startswith("CY2023") for p in floats)
    assert asked["us-gaap/Assets"][0] == "CY2027Q1I"
    assert len(asked["dei/EntityCommonStockSharesOutstanding"]) == size.CHECK_QUARTERS


def test_load_or_refresh_passes_its_date_through(tmp_path):
    seen = []
    with mock.patch.object(size, "build_index",
                           side_effect=lambda today=None: seen.append(today) or {"1": 1.0}):
        size.load_or_refresh(tmp_path / "size.json", dt.date(2027, 6, 1))
    assert seen == [dt.date(2027, 6, 1)]


def test_no_year_is_written_into_the_pipeline():
    """Every module, as code rather than prose: no integer literal that is a
    plausible year, and no frame period spelled out. Docstrings and comments
    may cite dates; the logic may not depend on one."""
    root = pathlib.Path(size.__file__).parent
    offenders = []
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant):
                continue
            v = node.value
            if isinstance(v, bool):
                continue
            if isinstance(v, int) and 2020 <= v <= 2035:
                offenders.append(f"{path.name}:{node.lineno} {v}")
            if isinstance(v, str) and re.fullmatch(r"CY20\d\dQ[1-4]I", v):
                offenders.append(f"{path.name}:{node.lineno} {v!r}")
    assert not offenders, offenders
