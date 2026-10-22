"""Plan 16 P1: 8-max UTG/UTG1 read the PokerCoaching 9-handed UTG1/UTG2 charts (same players
behind), as hero and as the opener/3-bettor in "vs X" keys; LJ..BB and 6-max are unchanged,
so the 85 indexed 6-max 100bb matchups keep their ids and flop keys."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pn import ranges
from pn.profile import charts
from pn.solve.request import TABLE_SEATS
from pn.train import flop_library as flib

# {mid: {line, board, accuracy, key}}: one indexed board per 6-max 100bb matchup, taken from
# hu_flop_library.json before the shift (the current code reproduced all 85 keys). 2026-10-17:
# the 16 lines whose seat ranges follow the preflop policy (call of a 4-bet, SB-BB limp;
# pn.solve.sources.policy_line_range) carry their new key, the indexed one under key_798b5c4.
GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "hu_flop_6max_100bb_keys.json").read_text()
)


def _pc(key: str) -> dict:
    return ranges.parse(charts.chart_entries(charts.PC_FILE)[key])


@pytest.fixture(autouse=True)
def _need_pc() -> None:
    if not (charts.charts_dir() / charts.PC_FILE).exists():
        pytest.skip(f"{charts.PC_FILE} not present")


def _cards(board: str) -> tuple[str, ...]:
    return tuple(board[i : i + 2] for i in (0, 2, 4))


def test_8max_ep_opens_read_the_9handed_seat_with_the_same_players_behind() -> None:
    assert charts.chart_range("8max", "open", "UTG") == _pc("open UTG1")
    assert charts.chart_range("8max", "open", "UTG1") == _pc("open UTG2")
    assert charts.pc_key("8max", "open", "UTG") == "open UTG1"
    for pos in ("LJ", "HJ", "CO", "BTN", "SB"):
        assert charts.pc_key("8max", "open", pos) == f"open {pos}"


def test_responses_vs_8max_ep_openers_read_the_shifted_opener() -> None:
    for hero in ("LJ", "HJ", "CO", "BTN", "SB", "BB"):
        for act in ("3bet", "call"):
            assert charts.pc_key("8max", act, hero, "UTG") == f"{act} {hero} vs UTG1"
            assert charts.pc_key("8max", act, hero, "UTG1") == f"{act} {hero} vs UTG2"
    assert charts.chart_range("8max", "3bet", "BTN", "UTG") == _pc("3bet BTN vs UTG1")
    assert charts.pc_key("8max", "3bet", "UTG1", "UTG") == "3bet UTG2 vs UTG1"
    assert charts.pc_key("8max", "call", "LJ", "HJ") == "call LJ vs HJ"


def test_4bet_lines_of_8max_ep_read_the_shifted_entries() -> None:
    for x in ("LJ", "HJ", "CO", "BTN", "SB", "BB"):
        assert charts.pc_key("8max", "4bet", "UTG1", x) == f"4bet UTG2 vs3bet {x}"
        assert charts.chart_range("8max", "4bet", "UTG1", x) == _pc(f"4bet UTG2 vs3bet {x}")
        assert charts.chart_range("8max", "call_3bet", "UTG", x) == _pc(f"call UTG1 vs3bet {x}")
    assert charts.pc_key("8max", "4bet", "UTG", "UTG1") == "4bet UTG1 vs3bet UTG2"


def test_6max_keys_are_not_shifted() -> None:
    assert charts.pc_key("6max", "open", "UTG") == "open LJ"
    assert charts.pc_key("6max", "3bet", "BTN", "UTG") == "3bet BTN vs LJ"


def test_6max_100bb_matchup_ids_and_flop_keys_unchanged() -> None:
    ms = flib.matchups(TABLE_SEATS[6], 100, "6max", sb=50, bb=100)
    assert {m.mid for m in ms} == set(GOLDEN)
    for m in ms:
        g = GOLDEN[m.mid]
        assert m.line == g["line"]
        assert flib.flop_spot(m, _cards(g["board"]), accuracy=g["accuracy"])[2] == g["key"], m.line


def test_8max_lines_without_ep_keep_the_6max_flop_keys() -> None:
    by_line = {g["line"]: g for g in GOLDEN.values()}
    ms = flib.matchups(TABLE_SEATS[8], 100, "8max", sb=50, bb=100)
    same = [m for m in ms if m.line in by_line]
    assert len(same) == 85
    for m in same:
        g = by_line[m.line]
        assert flib.flop_spot(m, _cards(g["board"]), accuracy=g["accuracy"])[2] == g["key"], m.line
