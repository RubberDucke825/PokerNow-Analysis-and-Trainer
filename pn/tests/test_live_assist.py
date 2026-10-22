"""Live assist (stream F): current-hand detection, hero-to-act, tree walking, recommendation
and the poll loop, with the solver mocked by the golden output (tests/fixtures/
adapter_hu_flop_out.json) and the fetch mocked by in-memory hands; plus real-solver runs that
are skipped when `mwsolver` is not built.

The synthetic hand reproduces the golden spot: HU, BTN opens to 750, BB (hero) calls, flop
Kd Ah 5h, pot 1500, stacks 9800 (BB) / 14700 (BTN).
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from pn.live import assist
from pn.live.assist import LiveError

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "adapter_hu_flop_out.json").read_text())
HERO, VILLAIN = "hB", "vB"


def _hand(flop_actions: list[tuple[str, str, int]] = (), *, complete: bool = False,
          board: list[str] | None = None) -> dict:  # fmt: skip
    """flop_actions: (player_id, kind, to_amount)."""
    acts = [("preflop", VILLAIN, "post_sb", 25, 25), ("preflop", HERO, "post_bb", 50, 50),
            ("preflop", VILLAIN, "raise", 725, 750), ("preflop", HERO, "call", 700, 750)]  # fmt: skip
    committed: dict[str, int] = {}
    for pid, kind, to in flop_actions:
        amount = to - committed.get(pid, 0) if kind in ("bet", "raise", "call") else 0
        committed[pid] = committed.get(pid, 0) + amount
        acts.append(("flop", pid, kind, amount, to))
    return {
        "hand_id": "live1", "hand_no": 7, "flags": [] if complete else ["incomplete"],
        "blinds": {"sb": 25, "bb": 50, "ante": 0, "straddle": 0},
        "players": [
            {"player_id": VILLAIN, "name": "hal", "position": "BTN", "stack_start": 15450, "cards": None},
            {"player_id": HERO, "name": "hero", "position": "BB", "stack_start": 10550, "cards": None},
        ],
        "actions": [{"street": st, "seq": i, "player_id": pid, "kind": k, "amount": a,
                     "to_amount": to, "allin": False} for i, (st, pid, k, a, to) in enumerate(acts)],
        "board": {"flop": board or ["Kd", "Ah", "5h"], "turn": None, "river": None},
    }  # fmt: skip


def _node_after(tokens: list[str]) -> dict:
    nid = GOLDEN["root"]
    for t in tokens:
        nid = next(a["child"] for a in GOLDEN["nodes"][nid]["actions"] if a["token"] == t)
    return GOLDEN["nodes"][nid]


def _combo_in_range(node: dict) -> tuple[str, str]:
    i = next(k for k, r in enumerate(node["reach"]) if r > 0)
    c = GOLDEN["combos"][i]
    return c[:2], c[2:]


class FakeSolver:
    def __init__(self, out: dict = GOLDEN) -> None:
        self.out, self.calls = out, []

    def __call__(self, toml_text: str) -> dict:
        self.calls.append(tomllib.loads(toml_text))
        return self.out


# ------------------------------------------------------------------ state


def test_parse_cards() -> None:
    assert assist.parse_cards("ahKD") == ("Ah", "Kd")
    assert assist.parse_cards("Ah Kd") == ("Ah", "Kd")
    assert assist.parse_cards(None) is None
    with pytest.raises(ValueError):
        assist.parse_cards("AhK")


def test_current_hand_is_the_unfinished_last_hand() -> None:
    done, live = _hand(complete=True), _hand()
    assert assist.current_hand([done, live]) is live
    assert assist.current_hand([live, done]) is None
    assert assist.current_hand([]) is None


def test_hero_to_act() -> None:
    assert assist.hero_to_act(_hand(), HERO) == "flop"  # BB first to act postflop
    assert assist.hero_to_act(_hand(), VILLAIN) is None
    assert assist.hero_to_act(_hand([(HERO, "check", 0)]), HERO) is None
    assert assist.hero_to_act(_hand([(HERO, "check", 0), (VILLAIN, "bet", 495)]), HERO) == "flop"


# ------------------------------------------------------------------ recommend (mocked solver)


def test_recommend_at_root() -> None:
    solver = FakeSolver()
    root = _node_after([])
    cards = _combo_in_range(root)
    rec = assist.recommend(_hand(), HERO, solver, hero_cards=cards)
    assert rec is not None and rec.street == "flop" and rec.history == ()
    tokens = [a["token"] for a in root["actions"]]
    assert list(rec.summary) == tokens  # action frequencies only, no ev/equity metrics
    i = next(k for k, c in enumerate(GOLDEN["combos"]) if {c[:2], c[2:]} == set(cards))
    assert rec.hero_freqs == pytest.approx(dict(zip(tokens, root["strategy"][i], strict=True)))
    cfg = solver.calls[0]
    assert cfg["game"]["pot"] == 1500 and cfg["game"]["board"] == ["Kd", "Ah", "5h"]
    assert [p["stack"] for p in cfg["players"]] == [9800, 14700]
    assert cfg["game"]["time_limit_sec"] == assist.LIVE_GAME["time_limit_sec"]
    assert not any("lock" in p for p in cfg["players"])  # Plan 7: GTO ranges, no node locks


def test_recommend_walks_real_actions_and_snaps_bet_sizes() -> None:
    hand = _hand([(HERO, "check", 0), (VILLAIN, "bet", 520)])  # 520 is closest to bet33 (495)
    rec = assist.recommend(hand, HERO, FakeSolver())
    assert rec.history == ("check", "bet33")
    assert list(rec.summary) == [a["token"] for a in _node_after(["check", "bet33"])["actions"]]
    assert rec.hero_freqs is None  # no hero cards given
    big = assist.recommend(_hand([(HERO, "check", 0), (VILLAIN, "bet", 1400)]), HERO, FakeSolver())
    assert big.history == ("check", "bet100")


def test_recommend_none_when_villain_to_act() -> None:
    solver = FakeSolver()
    assert assist.recommend(_hand([(HERO, "check", 0)]), HERO, solver) is None
    assert solver.calls == []


def test_hero_cards_outside_range() -> None:
    root = _node_after([])
    i = next(k for k, r in enumerate(root["reach"]) if r == 0 and not
             {GOLDEN["combos"][k][:2], GOLDEN["combos"][k][2:]} & {"Kd", "Ah", "5h"})  # fmt: skip
    c = GOLDEN["combos"][i]
    rec = assist.recommend(_hand(), HERO, FakeSolver(), hero_cards=(c[:2], c[2:]))
    assert rec.hero_freqs is None
    assert "not in hero's modeled range" in assist.render(rec)


def test_walk_off_tree_raises() -> None:
    bad = {**GOLDEN, "nodes": {**GOLDEN["nodes"], GOLDEN["root"]: {**_node_after([]), "player": 1}}}
    with pytest.raises(LiveError, match="left the solved tree"):
        assist.recommend(_hand([(HERO, "check", 0), (VILLAIN, "bet", 495)]), HERO, FakeSolver(bad))


def test_render() -> None:
    root = _node_after([])
    rec = assist.recommend(_hand(), HERO, FakeSolver(), hero_cards=_combo_in_range(root))
    text = assist.render(rec)
    assert text.startswith("hand live1 flop after [start]")
    assert "range:" in text and "locks" not in text


def test_cache_solver_failure_raises(tmp_path: Path) -> None:
    from pn.solve import SolveCache, SolveError

    def failing(_spot, _backend):
        raise SolveError("mwsolver exit 1: boom", 1)

    solve = assist.cache_solver(SolveCache(tmp_path, runner=failing))
    with pytest.raises(LiveError, match="exit"):
        solve(assist.build_spot_from_hand(_hand(), "preflop", HERO).toml)


def test_heads_up_postflop_is_cache_only(tmp_path: Path) -> None:
    import sys

    from pn.solve import SolveCache

    sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
    from fake_tree import fake_doc

    calls: list = []
    hand = _hand([(HERO, "check", 0), (VILLAIN, "bet", 495)])
    tomls: list[str] = []

    def capture(toml_text: str) -> dict:
        tomls.append(toml_text)
        raise LiveError("captured")

    with pytest.raises(LiveError):
        assist.recommend(hand, HERO, capture, hero_cards=("As", "Ad"), game=TINY)
    spot = tomllib.loads(tomls[0])
    solve = assist.cache_solver(SolveCache(tmp_path, runner=lambda s, b: calls.append(s)))
    with pytest.raises(LiveError, match="no_solve_available"):
        solve(tomls[0])
    assert calls == []  # never starts a solve for heads-up postflop
    old = SolveCache(tmp_path, runner=lambda s, b: ({**fake_doc(s), "meta": {"backend": "mwsolver"}},
                                                    "mwsolver"))  # fmt: skip
    old.solve(spot, "mwsolver")
    with pytest.raises(LiveError, match="no_solve_available"):
        solve(tomls[0])  # a cached non-texas document is not served
    SolveCache(tmp_path).path(assist.spot_key(spot)).unlink()
    seeded = SolveCache(tmp_path, runner=lambda s, b: ({**fake_doc(s), "meta": {"backend": "texas"}},
                                                       "texas"))  # fmt: skip
    seeded.solve(spot, "texas")
    solve = assist.cache_solver(SolveCache(tmp_path, runner=lambda s, b: calls.append(s)))
    rec = assist.recommend(hand, HERO, solve, hero_cards=("As", "Ad"), game=TINY)  # same key
    assert rec.history == ("check", "bet50") and calls == []  # served from the cache
    assert set(rec.summary) == {"fold", "call"}


def test_cache_solver_refuses_multiway_postflop(tmp_path: Path) -> None:
    import tomllib

    from pn.adapter.validate import to_toml
    from pn.solve import SolveCache

    calls: list = []
    solve = assist.cache_solver(SolveCache(tmp_path, runner=lambda s, b: calls.append(s)))
    spot = tomllib.loads(assist.build_spot_from_hand(_hand(), "flop", HERO).toml)
    spot["players"] = [*spot["players"], {**spot["players"][-1], "name": "third"}]
    with pytest.raises(LiveError, match="multiway_unsolvable"):
        solve(to_toml(spot))
    assert calls == []


# ------------------------------------------------------------------ poll loop


def test_watch_emits_once_per_decision() -> None:
    polls = [
        [_hand([(HERO, "check", 0)])],  # villain to act
        [_hand([(HERO, "check", 0), (VILLAIN, "bet", 495)])],  # hero to act
        [_hand([(HERO, "check", 0), (VILLAIN, "bet", 495)])],  # same decision: no repeat
        [_hand(complete=True)],  # hand over
    ]
    feed = iter(polls)
    out: list[str] = []
    solver = FakeSolver()
    n = assist.watch(lambda: next(feed), HERO, solver, emit=out.append, max_polls=len(polls),
                     sleep=lambda _s: None)  # fmt: skip
    assert n == 1 and len(out) == 1 and len(solver.calls) == 1
    assert "after [check bet33]" in out[0]


def test_watch_reports_errors_once() -> None:
    def broken(_toml: str) -> dict:
        raise LiveError("solver down")

    out: list[str] = []
    hand = _hand()
    n = assist.watch(lambda: [hand], HERO, broken, emit=out.append, max_polls=3,
                     sleep=lambda _s: None)  # fmt: skip
    assert n == 0 and out == ["hand live1: no recommendation (solver down)"]


# ------------------------------------------------------------------ real solver



TINY = {"time_limit_sec": 5, "max_iterations": 30, "threads": 2}


def test_preflop_recommendation_is_refused(tmp_path: Path) -> None:
    """Plan 7: there is no preflop engine; a preflop spot raises LiveError(preflop_unsolvable)."""
    from pn.solve import SolveCache

    solve = assist.cache_solver(SolveCache(tmp_path))
    hand = _hand()
    hand["actions"] = hand["actions"][:3]  # BTN raised to 750; BB (hero) to act preflop
    hand["board"] = {"flop": [], "turn": None, "river": None}
    assert assist.hero_to_act(hand, HERO) == "preflop"
    with pytest.raises(LiveError, match="preflop_unsolvable"):
        assist.recommend(hand, HERO, solve, hero_cards=("As", "Ad"), game=TINY)
