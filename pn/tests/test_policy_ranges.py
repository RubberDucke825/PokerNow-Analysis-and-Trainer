"""Seat ranges that follow the trainer's preflop policy (pn.solve.sources.policy_line_range):
the 3-bettor calling a 4-bet and the SB-BB limped pot; every other seat keeps the range text,
hence the flop key, it had at 798b5c4 (before the change)."""

from __future__ import annotations

import hashlib

import pytest

from pn.solve import build
from pn.train import flop_library as flib
from pn.train.library_fill import PROBE_BOARD, lib_matchups

# sha256(range text)[:16] per seat at 798b5c4, on PROBE_BOARD (dead cards removed)
HEAD_TEXT = [
    ("6x100", "srp BB-BTN 100bb: BTN raise, BB call", "BB", "ca33f0df40538a15"),
    ("6x100", "srp BB-BTN 100bb: BTN raise, BB call", "BTN", "971d2ea98b588310"),
    ("6x100", "3bp SB-BTN 100bb: BTN raise, SB raise, BTN call", "SB", "b4d1eafeda21029c"),
    ("6x100", "3bp SB-BTN 100bb: BTN raise, SB raise, BTN call", "BTN", "72ff65419e84428c"),
    ("6x100", "4bp+ CO-BTN 100bb: CO raise, BTN raise, CO raise, BTN call", "CO", "4eb2c3c9c893d6f9"),
    ("6x200", "4bp+ BB-BTN 200bb: BTN raise, BB raise, BTN raise, BB call", "BTN", "c40ab1071712d09a"),
    ("8x100", "4bp+ UTG-LJ 100bb: UTG raise, LJ raise, UTG raise, LJ call", "UTG", "ad15b07620cb1441"),
    ("8x100", "4bp+ UTG-LJ 100bb: UTG raise, LJ raise, UTG raise, LJ call", "LJ", "3d0c34094700be3f"),
    ("6x100", "limped BB-LJ 100bb: LJ call, BB check", "BB", "ebb410149f015b14"),
    ("6x100", "limped BB-LJ 100bb: LJ call, BB check", "LJ", "55e1c122b6156350"),
    ("6x200", "3bp BB-CO 200bb: CO raise, BB raise, CO call", "BB", "a9876c02e6ff2e34"),
    ("6x200", "3bp BB-CO 200bb: CO raise, BB raise, CO call", "CO", "2378d88873f3a952"),
    ("8x200", "srp UTG1-BTN 200bb: UTG1 raise, BTN call", "UTG1", "3a6c4f64fbbf502a"),
    ("8x200", "srp UTG1-BTN 200bb: UTG1 raise, BTN call", "BTN", "fa8f45e5b03ed344"),
    ("8x100", "limped BB-UTG 100bb: UTG call, BB check", "BB", "ebb410149f015b14"),
    ("8x100", "limped BB-UTG 100bb: UTG call, BB check", "UTG", "6c0fa78d93721e58"),
    ("8x200", "4bp+ UTG-UTG1 200bb: UTG raise, UTG1 raise, UTG raise, UTG1 call", "UTG", "c6dac04f7159a7d8"),
    ("8x200", "4bp+ UTG-UTG1 200bb: UTG raise, UTG1 raise, UTG raise, UTG1 call", "UTG1", "3d0c34094700be3f"),
]  # fmt: skip
# library flop key (preset accuracy) on PROBE_BOARD at 798b5c4
HEAD_KEY = [
    ("6x100", "srp BB-BTN 100bb: BTN raise, BB call", "2e18e549a88799adbdefba27c3bbe672e07dfb489e83ce8393ea81022fdd112b"),
    ("6x100", "3bp SB-BTN 100bb: BTN raise, SB raise, BTN call", "b59e702a702554a8aff3af49587e5eac58853705be0ea50146daf228ee7cc8a2"),
    ("8x100", "4bp+ UTG-LJ 100bb: UTG raise, LJ raise, UTG raise, LJ call", "a7db336f6e0d06191c7d170bbf35ab4e3d736c048003347c225ea575dab12e52"),
    ("6x100", "limped BB-LJ 100bb: LJ call, BB check", "4c8f0a72d5231fe10a76e8f2f550fe650694087ddb9412756fa39dfeb47c9ba5"),
    ("8x200", "4bp+ UTG-UTG1 200bb: UTG raise, UTG1 raise, UTG raise, UTG1 call", "062083b438556b0c542e82fd8db7d9b2c8727438eb0d9753196a2c6acc198f64"),
]  # fmt: skip


@pytest.fixture(scope="module")
def libs() -> dict[str, dict[str, flib.Matchup]]:
    out = {}
    for lib in {x[0] for x in HEAD_TEXT} | {"6x200"}:
        t, d = map(int, lib.split("x"))
        out[lib] = {m.label: m for m in lib_matchups(t, d)}
    return out


def _seats(m: flib.Matchup) -> dict[str, tuple[str, str]]:
    """position -> (range text, origin) on PROBE_BOARD."""
    return {s.seat.position: (s.range_text, s.range_origin) for s in build(m.request(PROBE_BOARD)).seats}


def test_unaffected_seats_keep_their_head_range_text(libs) -> None:
    got = [(lib, label, pos, hashlib.sha256(_seats(libs[lib][label])[pos][0].encode()).hexdigest()[:16])
           for lib, label, pos, _ in HEAD_TEXT]  # fmt: skip
    assert got == HEAD_TEXT


def test_unaffected_matchups_keep_their_head_flop_key(libs) -> None:
    assert [(lib, label, flib.flop_spot(libs[lib][label], PROBE_BOARD)[2])
            for lib, label, _ in HEAD_KEY] == HEAD_KEY  # fmt: skip


def test_policy_seats_and_origins(libs) -> None:
    four = _seats(libs["6x100"]["4bp+ CO-BTN 100bb: CO raise, BTN raise, CO raise, BTN call"])
    assert four["BTN"][1] == "policy:call_4bet BTN vs CO" and four["CO"][1] == "chart:4bet&open"
    limp = _seats(libs["6x100"]["limped SB-BB 100bb: SB call, BB check"])
    assert limp["SB"][1] == "policy:limp SB" and limp["BB"][1] == "policy:check BB vs SB limp"
    assert _seats(libs["6x100"]["limped BB-LJ 100bb: LJ call, BB check"])["BB"][1] == "any_two"
    ep = _seats(libs["8x100"]["4bp+ UTG-LJ 100bb: UTG raise, LJ raise, UTG raise, LJ call"])
    assert ep["LJ"][1] == "policy:call_4bet LJ vs UTG"  # same text as the old chart (HEAD_TEXT)
    # combos before dead cards (the policy's 3-bet x call vs the 4-bet; hero's SB limps)
    from pn.adapter.state import Line
    from pn.solve.sources import seat_range

    def n(pos: str, line: Line, depth: float, limper: str | None = None) -> float:
        rng, _ = seat_range(position=pos, line=line, table="6max", depth_bb=depth, three_bettor=None,
                            limper=limper)  # fmt: skip
        return round(sum(rng.values()), 1)

    assert n("SB", Line("limp"), 99, "SB") == 644.0
    assert n("BB", Line("check"), 99, "SB") == pytest.approx(816.2, abs=0.2)
    assert n("BB", Line("check"), 199, "SB") == pytest.approx(750.4, abs=0.2)
    assert n("BB", Line("check"), 99, "LJ") == 1326.0  # BB vs another limper: any two, as before
    assert n("SB", Line("limp"), 99, None) == 290.0  # no single SB limper: chart:open(limp), as before
