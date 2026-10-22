"""Source of the hand-written profile fixtures (tests/fixtures/hands_*.json).

Each hand is written as a compact action list; the builder fills amount/to_amount, uncalled
returns, pots and nets per contracts/hand_history.md. `python tests/test_profile_fixture_build.py`
rewrites the JSON; the test below fails if the committed JSON drifts from this source.
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
SB, BB = 25, 50
STACK = 5000
SIX = [("pA", "LJ"), ("pB", "HJ"), ("pC", "CO"), ("pD", "BTN"), ("pE", "SB"), ("pF", "BB")]
NAMES = {"pA": "alice", "pB": "bob", "pC": "carol", "pD": "dave", "pE": "erin", "pF": "frank",
         "pG": "gina", "pH": "hank"}  # fmt: skip
STREET_OF = {"F": "flop", "T": "turn", "R": "river"}


def _board(cards: str) -> dict:
    cs = cards.split()
    return {"flop": cs[:3], "turn": cs[3] if len(cs) > 3 else None,
            "river": cs[4] if len(cs) > 4 else None}  # fmt: skip


def build(
    hid: str,
    seats: list[tuple[str, str]],
    script: str,
    board: str = "",
    shows: dict[str, str] | None = None,
    winners: list[str] | None = None,
    flags: tuple[str, ...] = (),
    runs: list[str] | None = None,
    straddle: str | None = None,
    stacks: dict[str, int] | None = None,
) -> dict:
    """script: `;`-separated tokens `<pid> <kind> [to]`, street markers `F`, `T`, `R`.

    Blinds are posted automatically by the SB/BB (HU: BTN posts SB); `straddle` = pid posting 2bb.
    kinds: f(old) x(check) c(all) b(et) r(aise); `to` = total committed this street.
    """
    stacks = {pid: (stacks or {}).get(pid, STACK) for pid, _ in seats}
    pos = dict(seats)
    actions: list[dict] = []
    contrib = {pid: 0 for pid in stacks}
    committed: dict[str, int] = {}
    street = "preflop"
    level = 0

    def add(pid: str, kind: str, amount: int, to: int) -> None:
        contrib[pid] += amount
        actions.append({
            "street": street, "seq": len(actions), "player_id": pid, "kind": kind,
            "amount": amount, "to_amount": to, "allin": contrib[pid] == stacks[pid] and amount > 0,
            "at": "2026-10-01T00:00:00.000Z",
        })  # fmt: skip

    sb = next(p for p, q in seats if q == "SB") if "SB" in pos.values() else None
    if sb is None:  # heads-up: BTN posts the small blind
        sb = next(p for p, q in seats if q == "BTN")
    bbp = next(p for p, q in seats if q == "BB")
    for pid, kind, amt in ((sb, "post_sb", SB), (bbp, "post_bb", BB)):
        committed[pid] = amt
        add(pid, kind, amt, amt)
    level = BB
    if straddle:
        committed[straddle] = 2 * BB
        add(straddle, "post_straddle", 2 * BB, 2 * BB)
        level = 2 * BB
    for tok in [t.strip() for t in script.split(";") if t.strip()]:
        parts = tok.split()
        if parts[0] in STREET_OF:
            street, committed, level = STREET_OF[parts[0]], {}, 0
            continue
        pid, kind = parts[0], parts[1]
        mine = committed.get(pid, 0)
        left = stacks[pid] - contrib[pid]
        if kind == "f":
            add(pid, "fold", 0, mine)
        elif kind == "x":
            add(pid, "check", 0, mine)
        elif kind == "c":
            amt = min(level - mine, left)
            committed[pid] = mine + amt
            add(pid, "call", amt, mine + amt)
        else:
            to = int(parts[2])
            committed[pid] = to
            level = to
            add(pid, "bet" if kind == "b" else "raise", to - mine, to)
    ranked = sorted(contrib.values(), reverse=True)
    top = max(contrib, key=lambda p: contrib[p])
    returned = ranked[0] - ranked[1]
    uncalled = [{"player_id": top, "amount": returned}] if returned > 0 else []
    pot = sum(contrib.values()) - returned
    win = winners or [top]
    share, rem = divmod(pot, len(win))
    got = {p: share + (rem if i == 0 else 0) for i, p in enumerate(win)}
    net = {p: got.get(p, 0) - contrib[p] + (returned if p == top else 0) for p in contrib}
    shows = shows or {}
    hand = {
        "hand_id": hid, "hand_no": 0,
        "source": {"kind": "pokernow_log", "game_id": "fixture", "fetched_at": "2026-10-01T00:00:00Z"},
        "started_at": "2026-10-01T00:00:00.000Z", "variant": "NLHE",
        "blinds": {"sb": SB, "bb": BB, "ante": 0, "straddle": 2 * BB if straddle else 0},
        "button_seat": next(i + 1 for i, (_, q) in enumerate(seats) if q == "BTN"),
        "players": [
            {"seat": i + 1, "player_id": p, "name": NAMES.get(p, p), "stack_start": stacks[p],
             "position": q, "cards": shows[p].split() if p in shows else None,
             "cards_source": "showdown" if p in shows else None}
            for i, (p, q) in enumerate(seats)
        ],
        "actions": actions,
        "board": _board(board),
        "runs": [{"board": _board(b)} for b in runs] if runs else [{"board": _board(board)}],
        "showdowns": [{"player_id": p, "cards": c.split(), "shown": True} for p, c in shows.items()],
        "pots": [{"amount": pot, "winners": [{"player_id": p, "amount": a, "hand_desc": None}
                                             for p, a in got.items()]}],
        "uncalled_returned": uncalled,
        "net": net,
        "flags": sorted(flags),
    }  # fmt: skip
    assert sum(net.values()) == 0, hid
    return hand


def core_hands() -> list[dict]:
    """24 six-max hands with the fixed seating SIX (pA=LJ ... pF=BB)."""
    h = [
        build("c01_open_fold", SIX, "pA r 125; pB f; pC f; pD f; pE f; pF f"),
        build("c02_cbet33_fold", SIX, "pA f; pB f; pC r 125; pD f; pE f; pF c;"
              "F; pF x; pC b 85; pF f", "Kd 7s 2c"),
        build("c03_3bet_fold", SIX, "pA r 125; pB f; pC r 375; pD f; pE f; pF f; pA f"),
        build("c04_4bet_5bet", SIX, "pA r 125; pB r 375; pC f; pD f; pE f; pF f; pA r 825;"
              "pB r 5000; pA f"),
        build("c05_limp_iso_barrel", SIX, "pA c; pB f; pC r 200; pD f; pE f; pF f; pA c;"
              "F; pA x; pC b 225; pA c; T; pA x; pC b 500; pA f", "9h 8h 2s 3d"),
        build("c06_limped_checkdown", SIX, "pA c; pB c; pC f; pD f; pE c; pF x;"
              "F; pE x; pF x; pA x; pB x; T; pE x; pF x; pA x; pB x;"
              "R; pE x; pF x; pA x; pB x", "Jc Tc 4d 5h 2d",
              shows={"pE": "Ah Jh", "pF": "Tc 9c", "pA": "Qc Ks"}, winners=["pE"]),
        build("c07_steal_donk_raised", SIX, "pA f; pB f; pC f; pD r 125; pE f; pF c;"
              "F; pF b 125; pD r 450; pF f", "6s 5s 4d"),
        build("c08_sb_limp_bet", SIX, "pA f; pB f; pC f; pD f; pE c; pF x; F; pE b 50; pF f",
              "Ah 9d 3c"),
        build("c09_squeeze", SIX, "pA r 125; pB c; pC r 550; pD f; pE f; pF f; pA f; pB f"),
        build("c10_multiway_cbet_barrel", SIX, "pA r 125; pB f; pC c; pD c; pE f; pF f;"
              "F; pA b 150; pC f; pD c; T; pA b 560; pD f", "Qh Jd 3s 8c"),
        build("c11_check_raise", SIX, "pA f; pB f; pC r 125; pD f; pE f; pF c;"
              "F; pF x; pC b 90; pF r 360; pC f", "8d 7d 2h"),
        build("c12_triple_barrel_sd", SIX, "pA f; pB r 125; pC f; pD f; pE f; pF c;"
              "F; pF x; pB b 180; pF c; T; pF x; pB b 475; pF c; R; pF x; pB b 1200; pF c",
              "Ks 8h 4c 2d 9s", shows={"pB": "Ah Kd", "pF": "Qs Qd"}, winners=["pB"]),
        build("c13_probe_turn", SIX, "pA f; pB f; pC r 125; pD f; pE f; pF c;"
              "F; pF x; pC x; T; pF b 150; pC c; R; pF b 400; pC f", "Td 6c 3h Js 2c"),
        build("c14_cbet100_fold", SIX, "pA f; pB f; pC f; pD r 125; pE f; pF c;"
              "F; pF x; pD b 275; pF f", "9c 5d 2s"),
        build("c15_cbet25_call", SIX, "pA f; pB f; pC r 125; pD f; pE f; pF c;"
              "F; pF x; pC b 70; pF c; T; pF x; pC x; R; pF b 300; pC f", "As Ks 2h 7c 7d"),
        build("c16_overbet_turn_fold", SIX, "pA f; pB f; pC f; pD r 125; pE f; pF c;"
              "F; pF x; pD b 180; pF c; T; pF x; pD b 950; pF f", "8s 6s 2d Kc"),
        build("c17_overbet_turn_call_sd", SIX, "pA f; pB f; pC f; pD r 125; pE f; pF c;"
              "F; pF x; pD b 90; pF c; T; pF x; pD b 680; pF c; R; pF x; pD x",
              "9h 5c 2c Jd 3s", shows={"pF": "Jc Tc", "pD": "7h 6h"}, winners=["pF"]),
        build("c18_river_raise_sd", SIX, "pA f; pB f; pC r 125; pD f; pE f; pF c;"
              "F; pF x; pC x; T; pF x; pC x; R; pF b 200; pC r 800; pF c", "Qc 8c 5h 2s Ac",
              shows={"pC": "Kc Jc", "pF": "Qh Qd"}, winners=["pC"]),
        build("c19_walk", SIX, "pA f; pB f; pC f; pD f; pE f"),
        build("c20_allin_pre_sd", SIX, "pA r 125; pB f; pC f; pD f; pE f; pF r 5000; pA c",
              "7c 7h 2d Kc 4s", shows={"pA": "As Ad", "pF": "Kh Qh"}, winners=["pA"]),
        build("c21_donk_multiway", SIX, "pA r 125; pB f; pC c; pD f; pE f; pF c;"
              "F; pF b 300; pA f; pC c; T; pF x; pC x; R; pF x; pC b 500; pF f",
              "7s 6d 5c 2h Ts"),
        build("c22_3bet_call_cbet", SIX, "pA r 125; pB f; pC f; pD r 400; pE f; pF f; pA c;"
              "F; pA x; pD b 300; pA f", "Jh 9s 4d"),
        build("c23_4bet_call_cbet", SIX, "pA f; pB r 125; pC r 375; pD f; pE f; pF f;"
              "pB r 900; pC c; F; pC x; pB b 600; pC f", "8c 4h 3d"),
        build("c24_limp_reraise", SIX, "pA c; pB f; pC r 200; pD f; pE f; pF f; pA r 650; pC f"),
    ]  # fmt: skip
    return h


def special_hands() -> list[dict]:
    """Run-it-twice, straddle, heads-up, 8-max multiway, two skipped hands, squeeze/depth."""
    straddle_seats = [("pA", "STR"), ("pB", "HJ"), ("pC", "CO"), ("pD", "BTN"), ("pE", "SB"),
                      ("pF", "BB")]  # fmt: skip
    eight = [("pA", "UTG"), ("pB", "UTG1"), ("pC", "LJ"), ("pD", "HJ"), ("pE", "CO"),
             ("pG", "BTN"), ("pH", "SB"), ("pF", "BB")]  # fmt: skip
    return [
        build("s01_run_it_twice", SIX, "pA r 125; pB f; pC f; pD f; pE f; pF r 5000; pA c",
              "9c 5d 2h 3s 8c", shows={"pA": "Js Jd", "pF": "Ah Kh"}, winners=["pA", "pF"],
              flags=("run_it_twice",),
              runs=["9c 5d 2h 3s 8c", "Ac 7d 4s 6c Td"]),
        build("s02_straddle", straddle_seats, "pB c; pC r 300; pD f; pE f; pF f; pA f; pB c;"
              "F; pB x; pC b 400; pB f", "Ad Td 6s", straddle="pA", flags=("straddle",)),
        build("s03_heads_up", [("pA", "BTN"), ("pB", "BB")], "pA r 125; pB c;"
              "F; pB x; pA b 80; pB c; T; pB x; pA x; R; pB b 150; pA f", "Kh 7d 3c 9s 2h"),
        build("s04_eight_max_multiway", eight, "pA r 125; pB c; pC f; pD f; pE f; pG f; pH f;"
              "pF c; F; pF x; pA b 200; pB c; pF f; T; pA x; pB b 300; pA f", "Th 9h 2c 4s"),
        build("s05_bomb_pot_skipped", SIX, "F; pE x; pF x; pA x; pB x; pC x; pD x", "2c 3c 4c",
              flags=("bomb_pot",)),
        build("s06_incomplete_skipped", SIX, "pA r 125; pB f", flags=("incomplete",)),
        build("s07_squeeze_called", SIX, "pA r 125; pB c; pC f; pD r 600; pE f; pF f; pA f;"
              "pB c; F; pB x; pD b 500; pB f", "Kc 8d 3h"),
        build("s08_deep_hu_3bet", [("pA", "BTN"), ("pB", "BB")], "pA r 125; pB r 400; pA f",
              stacks={"pA": 20000, "pB": 15000}),
        build("s09_short_sb_open", SIX, "pA f; pB f; pC f; pD f; pE r 150; pF f",
              stacks={"pE": 2500}),
    ]  # fmt: skip


def _write() -> None:
    FIXTURES.mkdir(exist_ok=True)
    (FIXTURES / "hands_core.json").write_text(json.dumps(core_hands(), indent=1) + "\n")
    (FIXTURES / "hands_special.json").write_text(json.dumps(special_hands(), indent=1) + "\n")


def test_fixture_json_matches_source() -> None:
    assert json.loads((FIXTURES / "hands_core.json").read_text()) == core_hands()
    assert json.loads((FIXTURES / "hands_special.json").read_text()) == special_hands()


if __name__ == "__main__":
    _write()
