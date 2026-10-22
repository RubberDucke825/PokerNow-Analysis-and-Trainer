"""pn.ranges: parse / to_text / to_dense and combo indexing (contracts/range_format.md)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pn import ranges

COMBO_FIXTURE = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "combo_index.json"


def test_combo_index_is_dense_lexicographic() -> None:
    seen = []
    for a in range(52):
        for b in range(a + 1, 52):
            seen.append(ranges.combo_index(a, b))
            assert ranges.combo_index(b, a) == ranges.combo_index(a, b)
    assert seen == list(range(1326))


def test_combo_strings() -> None:
    combos = ranges.all_combos()
    assert len(set(combos)) == 1326
    assert combos[0] == "2s2h"  # cards 0,1: pair, lower suit first
    assert combos[2] == "2s2c"
    assert combos[3] == "3s2s"  # higher rank first
    assert combos[-1] == "AdAc"
    assert ranges.combo_str_index("KdAh") == ranges.combo_str_index("AhKd")
    assert ranges.canonical_combo("KdAh") == "AhKd"


def test_matches_cards_crate_fixture() -> None:
    if not COMBO_FIXTURE.exists():
        pytest.skip(f"{COMBO_FIXTURE} not generated yet (cards crate: dump_combo_index example)")
    data = json.loads(COMBO_FIXTURE.read_text())
    combos = data["combos"] if isinstance(data, dict) else data
    assert list(ranges.all_combos()) == combos


@pytest.mark.parametrize(
    "text,n",
    [("AA", 6), ("AKs", 4), ("AKo", 12), ("AK", 16), ("22+", 78), ("A2s+", 48), ("T9s-54s", 24),
     ("77-44", 24), ("A9o-A6o", 48), ("AhKh", 1), ("", 0), (" AA , KK ", 12)],
)  # fmt: skip
def test_parse_counts(text: str, n: int) -> None:
    assert len(ranges.parse(text)) == n


def test_weights_and_override() -> None:
    d = ranges.parse("QQ:0.5,AKs,AhKh:0.7,QQ")
    assert d["QsQh"] == 1.0  # later entry overrides
    assert d["AhKh"] == 0.7 and d["AsKs"] == 1.0
    assert ranges.parse("AA:0") == {}


@pytest.mark.parametrize("bad", ["AAA", "AKx", "AA:2", "AsAs", "AK-T8", "Zs2h", "AKs-KQo"])
def test_parse_errors(bad: str) -> None:
    with pytest.raises(ranges.RangeParseError):
        ranges.parse(bad)


@pytest.mark.parametrize(
    "text",
    ["22+,A2s+,K9s+,QTs+,JTs,ATo+,KQo", "AA,KK,QQ:0.5,AKs,AQo:0.25,T9s-54s,A5s+,22+,AhKh:0.7",
     "77-44,A9s-A6s,K2o+", "AsKs,AhKh:0.3,7c6c"],
)  # fmt: skip
def test_to_text_roundtrip(text: str) -> None:
    d = ranges.parse(text)
    assert ranges.parse(ranges.to_text(d)) == d


def test_to_text_compresses() -> None:
    assert ranges.to_text(ranges.parse("22+")) == "22+"
    assert ranges.to_text(ranges.parse("A2s+,KQo")) == "A2s+,KQo"
    assert ranges.to_text(ranges.parse("TT-77,A9s-A6s")) == "TT-77,A9s-A6s"


def test_to_dense() -> None:
    dense = ranges.to_dense(ranges.parse("AA:0.5"))
    assert len(dense) == 1326 and sum(dense) == pytest.approx(3.0)
    assert dense[ranges.combo_str_index("AsAh")] == 0.5
    assert ranges.from_dense(dense) == ranges.parse("AA:0.5")


def test_classes() -> None:
    classes = ranges.all_classes()
    assert len(classes) == 169
    assert sum(ranges.class_n_combos(c) for c in classes) == 1326
    assert ranges.combo_class("7c7d") == "77" and ranges.combo_class("KdAd") == "AKs"
