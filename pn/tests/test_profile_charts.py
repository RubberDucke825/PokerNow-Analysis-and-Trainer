"""Preflop charts and hand ranking load and are sane."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pn import ranges
from pn.cli.main import app
from pn.profile import charts
from pn.profile.paths import config_dir


@pytest.mark.parametrize("table", ["hu", "6max", "8max"])
def test_every_chart_line_parses(table: str) -> None:
    for key, text in charts.load_table(table).items():
        assert ranges.parse(text) is not None, key


def test_open_widths_are_ordered() -> None:
    order = ["UTG", "UTG1", "LJ", "HJ", "CO", "BTN"]
    widths = [ranges.width(charts.chart_range("8max", "open", p)) for p in order]
    assert widths == sorted(widths)
    assert 0.14 < widths[0] < 0.15 and 0.50 < widths[-1] < 0.52  # PC 9h UTG1 14.3% / BTN 51.1%


def test_lookup_by_groups() -> None:
    assert charts.chart_range("6max", "3bet", "BTN", "CO")
    assert charts.chart_range("6max", "call", "BB", "SB")
    assert charts.chart_range("8max", "4bet", "UTG")
    assert charts.chart_range("6max", "5bet", "BB", "BTN")
    assert charts.chart_range("hu", "3bet", "BB", "BTN")
    assert charts.chart_range("hu", "open", "SB")  # HU SB label maps to BTN
    with pytest.raises(charts.ChartError):
        charts.chart_range("6max", "open", "BB")


def test_prior_range_averages_openers() -> None:
    bb_call = charts.prior_range("6max", "BB", "call")
    assert 0.2 < ranges.width(bb_call) < 0.45
    with pytest.raises(charts.ChartError):
        charts.prior_range("6max", "LJ", "call")  # nobody opens before LJ


def test_value_hands_in_every_open_range() -> None:
    for table in ("6max", "8max"):
        for key, text in charts.load_table(table).items():
            if key.startswith("open"):
                assert {"AsAh", "KsKh", "AsKs"} <= set(ranges.parse(text)), key


def test_hand_ranking() -> None:
    rk = charts.hand_ranking()
    assert len(rk) == 169 and rk[0] == "AA" and rk[-1] in ("32o", "42o", "52o", "62o", "72o")
    assert rk.index("AKs") < rk.index("KQs") < rk.index("72o")
    pct = charts.class_percentiles()
    assert pct["AA"] == pytest.approx(6 / 1326) and max(pct.values()) == pytest.approx(1.0)


def test_all_charts_valid_and_pure() -> None:
    rows = charts.validate_charts()
    assert rows and not [r for r in rows if r["error"]]


def test_raise_and_call_ranges_do_not_overlap() -> None:
    for table in ("hu", "6max", "8max"):
        d = charts.load_table(table)
        for key, text in d.items():
            if key[:5] in ("3bet ", "4bet ", "5bet "):
                call = d.get("call " + key.split(" ", 1)[1])
                if call:
                    assert not set(ranges.parse(text)) & set(ranges.parse(call)), key


@pytest.mark.parametrize(
    "text,expected",
    [
        ("AA:1,KK:1,AKs:0.75,AQs:0.4", "KK+,AKs"),  # Pio / GTO Wizard export
        ("AA, KK,\nAKs", "KK+,AKs"),  # plain list, newlines
        ("[60]JJ,TT[/60],[30]99[/30],AA", "AA,JJ-TT"),  # bracket weights
        ("AhKd:1,AsKs:1,AhKh:1,AdKd:1,AcKc:1", "AKs"),  # 1/12 AKo combos -> out
        ("QQ:0.5", "QQ"),  # exactly 0.5 -> in
    ],
)
def test_pure_import(text: str, expected: str) -> None:
    assert ranges.to_text(charts.pure_range(text)) == expected


@pytest.fixture()
def cfg_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    shutil.copytree(config_dir(), tmp_path / "config")
    monkeypatch.setenv("PN_CONFIG_DIR", str(tmp_path / "config"))
    charts._read.cache_clear()
    yield tmp_path / "config"
    charts._read.cache_clear()


def test_cli_import_show_validate(cfg_copy: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    src = tmp_path / "gtow.txt"
    src.write_text("AA:1,KK:1,QQ:0.9,AKs:0.6,AKo:0.2")
    r = runner.invoke(app, ["charts", "import", str(src), "--key", "open UTG"])
    assert r.exit_code == 2  # in gtowizard_100bb, gtowizard_200bb and 8max: ambiguous
    r = runner.invoke(
        app, ["charts", "import", str(src), "--key", "open UTG", "--file", "8max.txt"]
    )
    assert r.exit_code == 0, r.output
    assert "open UTG = QQ+,AKs" in (cfg_copy / "preflop_charts" / "8max.txt").read_text()
    r = runner.invoke(app, ["charts", "import", str(src), "--key", "open BTN"])
    assert r.exit_code == 2  # ambiguous: in 6max, 8max and hu
    r = runner.invoke(app, ["charts", "show", "open UTG"])
    assert r.exit_code == 0 and "QQ+,AKs" in r.output
    r = runner.invoke(app, ["charts", "validate"])
    assert r.exit_code == 0, r.output
    with (cfg_copy / "preflop_charts" / "6max.txt").open("a") as fh:
        fh.write("open XX = AA:0.5\n")
    r = runner.invoke(app, ["charts", "validate"])
    assert r.exit_code == 1 and "weighted" in r.output
