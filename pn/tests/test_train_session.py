"""pn.train sessions with a fake solved tree (tests/fixtures/fake_tree.py): deterministic deals,
chip conservation, postflop EV grading, fallback on solve failure, background solving, and the
hard-timeout runner."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

from pn.solve import SolveCache
from pn.solve.backend import SolveError
from pn.train import Config, SessionNotFound, Trainer, TrainError
from pn.train import postflop as post

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from fake_tree import fake_runner

FAKE = Path(__file__).parent / "fixtures" / "fake_solver.py"


@pytest.fixture(autouse=True)
def _flop_library(flop_template: Path, tmp_path: Path) -> None:
    """Heads-up flops come from the solved set: seed tmp_path/cache (the _trainer root)."""
    import flop_seed

    flop_seed.copy_into(flop_template, tmp_path / "cache")


def _trainer(tmp_path: Path, runner=fake_runner, background: bool = False) -> Trainer:
    return Trainer(SolveCache(tmp_path / "cache", runner=runner), background=background)


def _to_turn(t: Trainer, v: dict, hands: int = 40) -> dict:
    """Play passively (new hands as needed) until hero's hand reaches a heads-up turn."""
    for _ in range(hands):
        while v["result"] is None and v["street"] in ("preflop", "flop") and v["legal_actions"]:
            v = t.act(v["sid"], _passive(v))
        if v["result"] is None and v["street"] == "turn":
            return v
        if v["result"] is None:
            v = _play_hand(t, v)
        v = t.next(v["sid"])
    raise AssertionError("no heads-up turn reached")


def _passive(view: dict) -> str:
    toks = [o["token"] for o in view["legal_actions"]]
    return next(t for t in ("check", "call", "fold") if t in toks)


def _play_hand(t: Trainer, view: dict, choose=_passive, limit: int = 40) -> dict:
    for _ in range(limit):
        if view["result"] is not None:
            return view
        assert view["legal_actions"], f"hero cannot act: {view['solve']}"
        view = t.act(view["sid"], choose(view))
    raise AssertionError("hand did not finish")


def test_same_seed_same_session(tmp_path: Path) -> None:
    runs = []
    for k in range(2):
        t = _trainer(tmp_path / str(k))
        v = _play_hand(t, t.new(Config(seed=7, hero_seat="BTN")))
        runs.append((v["hero"]["cards"], v["history"], v["result"]))
    assert runs[0] == runs[1]


def test_hands_conserve_chips_and_rotate_the_button(tmp_path: Path) -> None:
    t = _trainer(tmp_path)
    v = t.new(Config(seed=3, hero_seat="BB"))
    assert v["hero"]["position"] == "BB" and len(v["seats"]) == 6 and len(v["hero"]["cards"]) == 2
    assert v["seats"][0]["is_hero"] and "cards" not in v["seats"][1]  # villains hidden
    positions = []
    for _ in range(6):
        v = _play_hand(t, v)
        assert sum(v["result"]["net"].values()) == pytest.approx(0.0)
        positions.append(v["hero"]["position"])
        v = t.next(v["sid"])
    assert positions == ["BB", "SB", "BTN", "CO", "HJ", "LJ"]
    assert v["totals"]["hands"] == 6


def test_postflop_decisions_are_graded_by_frequency(tmp_path: Path) -> None:
    t = _trainer(tmp_path)
    v = t.new(Config(seed=11, hero_seat="BB"))
    graded = []
    for _ in range(60):  # hands until hero checks on a solved flop
        while v["result"] is None:
            toks = [o["token"] for o in v["legal_actions"]]
            v = t.act(v["sid"], _passive(v))
            fb = v["feedback"]
            if fb["street"] != "preflop" and fb["origin"] == "solve" and fb["grade"] != "none":
                graded.append(fb)  # combos outside the library range are not graded
                assert fb["grid_ref"] is None  # no registry attached in these tests
            assert toks
        if any(f["chosen"] == "check" for f in graded):
            break
        v = t.next(v["sid"])
    check = next(f for f in graded if f["chosen"] == "check")
    # fake tree: [check, bet50] at 0.25 / 0.75 -> check is 1/3 of the max frequency: mixed
    assert check["best"] == "bet50" and check["grade"] == "mixed" and check["loss_bb"] is None
    assert check["freq"] == 0.25 and check["best_freq"] == 0.75 and check["freq_error"] == 0.75
    assert [a["token"] for a in check["options"]] == ["check", "bet50"]
    assert [a["freq"] for a in check["options"]] == [0.25, 0.75]
    assert [a["ev_bb"] for a in check["options"]] == [None, None] and check["node_id"]
    tot = v["totals"]
    assert tot["mixed"] >= 1 and tot["decisions"] == tot["best"] + tot["mixed"] + tot["blunders"]
    assert "loss_bb" not in tot and "ev_decisions" not in tot


def test_multiway_streets_are_not_solved_or_graded(tmp_path: Path) -> None:
    sizes: list[int] = []

    def counting(spot, backend=None):
        sizes.append(len(spot["players"]))
        return fake_runner(spot, backend)

    t = _trainer(tmp_path, runner=counting)
    v = t.new(Config(seed=1, hero_seat="BB"))
    seen = None
    for _ in range(60):  # hands until hero acts on a flop that 3+ players saw
        while v["result"] is None:
            multi = v["street"] != "preflop" and v["solve"]["note"] == "multiway"
            v = t.act(v["sid"], _passive(v))
            if multi and seen is None:
                seen = v["feedback"]
        if seen is not None:
            break
        v = t.next(v["sid"])
    assert seen is not None, "no multiway flop in 60 hands"
    assert seen["grade"] == "none" and seen["note"] == "multiway_unsolvable"
    assert seen["loss_bb"] is None and seen["origin"] == "none"
    assert set(sizes) <= {2}  # only heads-up (turn / river) streets reached the solver
    h, solved = t.get(v["sid"]).hand, len(sizes)
    while v["result"] is None:  # later streets stay unsolved even when heads-up again
        assert h.multiway and v["solve"]["note"] == "multiway" and v["solve"]["status"] != "ready"
        v = t.act(v["sid"], _passive(v))
        if v["feedback"]["street"] != "preflop":
            assert v["feedback"]["note"] == "multiway_unsolvable"
        h = t.get(v["sid"]).hand
    assert h.multiway and all(d["note"] == "multiway_unsolvable"
                              for d in h.decisions if d["street"] != "preflop")  # fmt: skip
    assert len(sizes) == solved  # no solve started for the rest of this hand


def test_failed_solve_falls_back(tmp_path: Path) -> None:
    def boom(spot, backend=None):
        raise SolveError("solver exploded")

    t = _trainer(tmp_path, runner=boom)
    v = _to_turn(t, t.new(Config(seed=5, hero_seat="BB")))  # the flop came from the library
    assert v["solve"]["status"] == "failed" and "solver exploded" in v["solve"]["note"]
    assert v["legal_actions"] and v["feedback"]  # fallback options, hand plays on
    v = _play_hand(t, v)
    assert v["result"] is not None


def test_background_solve_blocks_until_done(tmp_path: Path) -> None:
    gate = threading.Event()

    def slow(spot, backend=None):
        gate.wait(10)
        return fake_runner(spot, backend)

    t = _trainer(tmp_path, runner=slow, background=True)
    v = t.new(Config(seed=5, hero_seat="BB"))
    for _ in range(40):  # the flop is the library document; the turn is solved live
        while v["result"] is None and not v["solving"] and v["legal_actions"]:
            v = t.act(v["sid"], _passive(v))
        if v["solving"]:
            break
        if v["result"] is None:
            v = _play_hand(t, v)
        v = t.next(v["sid"])
    assert v["street"] == "turn" and v["solving"] and v["legal_actions"] == [] and v["solve"]["status"] == "solving"
    with pytest.raises(TrainError):
        t.act(v["sid"], "check")
    gate.set()
    for _ in range(100):
        v = t.state(v["sid"])
        if not v["solving"]:
            break
        time.sleep(0.05)
    assert not v["solving"] and (v["legal_actions"] or v["result"])


def test_session_errors(tmp_path: Path) -> None:
    t = _trainer(tmp_path)
    v = t.new(Config(seed=1))
    with pytest.raises(TrainError):
        t.next(v["sid"])  # hand not over
    with pytest.raises(TrainError):
        t.act(v["sid"], "raise999")
    with pytest.raises(SessionNotFound):
        t.state("nope")
    with pytest.raises(TrainError):
        Config(table=7)
    with pytest.raises(TrainError):
        Config(hero_seat="UTG")  # not a 6-max seat


def test_real_players_are_named_and_play_gto(tmp_path: Path) -> None:
    t = Trainer(SolveCache(tmp_path, runner=fake_runner), background=False,
                names=lambda: {"p1": "Alice"})  # fmt: skip
    v = t.new(Config(seed=2, opponents=("p1",)))
    assert any(s["name"] == "Alice" and s["player_id"] == "p1" for s in v["seats"])
    v = _play_hand(t, v)
    assert not any(str(h["origin"]).startswith("profile(") for h in v["history"])
    for source in ("tendencies", "custom", "population"):  # Plan 7: no locked solves
        with pytest.raises(TrainError, match="source"):
            Config(source=source)


def test_runner_kills_slow_texas_without_fallback(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    from pn.solve import texas

    sleeper = tmp_path / "sleep.py"
    sleeper.write_text("import time; time.sleep(30)\n")
    monkeypatch.setattr(texas, "texas_cmd", lambda: [sys.executable, str(sleeper)])
    monkeypatch.setattr(post, "HARD_GRACE_SEC", 0)
    from pn.solve import build_spot
    from pn.solve.request import apply_preset, new_request, parse_board

    spot = build_spot(new_request(["BTN", "BB"], 100, board=parse_board("Kd7s2c"), line="srp"))
    spot, backend = apply_preset(spot, "trainer")
    assert backend == "texas"
    spot["game"]["time_limit_sec"] = 1
    t0 = time.monotonic()
    with pytest.raises(SolveError, match="texas timed out"):
        post.runner(spot, backend)
    assert time.monotonic() - t0 < 10
    monkeypatch.setattr(texas, "texas_cmd", lambda: None)
    with pytest.raises(SolveError, match="console_solver not found"):
        post.runner(spot, backend)


def test_contract_fields_and_grid_refs(tmp_path: Path) -> None:
    names: list[str] = []

    def register(doc: dict, req, parent) -> str:
        names.append(f"train-{len(names)}")
        return names[-1]

    t = Trainer(SolveCache(tmp_path / "cache", runner=fake_runner), background=False,
                register=register)  # fmt: skip
    v = t.new(Config(seed=11, hero_seat="BB"))
    assert v["bb"] == 100 and v["pot"] >= 150 and v["hero"]["stack"] == 9_900  # cents, BB posted
    first = v["legal_actions"]
    assert {o["token"] for o in first} >= {"fold", "call"}
    assert next(o for o in first if o["token"] == "fold")["amount"] is None
    assert all(o["label"] for o in first)
    assert {o["kind"] for o in first} <= {"fold", "check", "call", "raise", "allin"}
    v = t.act(v["sid"], _passive(v))
    fb = v["feedback"]
    assert fb["grade"] in ("best", "mixed", "blunder")
    assert set(fb) >= {"freq", "best", "best_freq", "freq_error", "loss_bb", "preflop_source"}
    assert "gto_best" not in fb
    assert fb["loss_bb"] is None and 0.0 <= fb["freq"] <= fb["best_freq"] <= 1.0
    if fb["preflop_source"] == "pokercoaching":  # pure chart: the PokerCoaching grid
        assert fb["origin"].startswith("pc:") and fb["grid_ref"]["kind"] == "pc"
        assert fb["grid_ref"]["key"] == fb["origin"][len("pc:") :]
        assert fb["freq"] in (0.0, 1.0) and len(fb["buckets"]) <= 1
    else:
        assert fb["preflop_source"] == "gtowizard" and fb["grid_ref"]["kind"] == "gtow"
        assert fb["grid_ref"]["key"].startswith("100|") and fb["grid_ref"]["key"].endswith("|BB")
    for _ in range(30):
        while v["result"] is None:
            v = t.act(v["sid"], _passive(v))
            fb = v["feedback"]
            if fb["origin"] == "solve":
                assert fb["preflop_source"] is None
                ref = fb["grid_ref"]
                assert ref == {"kind": "solve", "name": ref["name"], "node_id": fb["node_id"]}
                assert ref["name"] in names
                return
        v = t.next(v["sid"])
    raise AssertionError("never reached a solved street")


def test_preflop_feedback_names_the_pokercoaching_chart(tmp_path: Path) -> None:
    t = _trainer(tmp_path)
    v = t.new(Config(seed=3, hero_seat="LJ"))  # first to act: LJ raise first in
    v = t.act(v["sid"], _passive(v))
    fb = v["feedback"]
    assert fb["preflop_source"] == "pokercoaching" and fb["origin"] == "pc:open LJ"
    assert fb["grid_ref"] == {"kind": "pc", "key": "open LJ", "table": "6max", "situation": "rfi",
                              "position": "LJ", "other": None}  # fmt: skip


# ------------------------------------------------------------------ Plan 4: RNG and history


def _rolls(t: Trainer, cfg: Config, hands: int = 3) -> list[tuple]:
    v = t.new(cfg)
    out = []
    for _ in range(hands):
        while v["result"] is None:
            roll = v["rng"]
            v = t.act(v["sid"], _passive(v))
            fb = v["feedback"]
            out.append((roll, fb["rng"], fb["rng_action"], fb["matched_rng"]))
        v = t.next(v["sid"])
    return out


def test_rng_is_seeded_and_judged(tmp_path: Path) -> None:
    a = _rolls(_trainer(tmp_path / "a"), Config(seed=21, hero_seat="BB"))
    b = _rolls(_trainer(tmp_path / "b"), Config(seed=21, hero_seat="BB"))
    assert a == b and a
    assert all(1 <= roll <= 100 and roll == fb_roll for roll, fb_roll, _, _ in a)
    t = _trainer(tmp_path / "c")
    v = t.act((v0 := t.new(Config(seed=21, hero_seat="BB")))["sid"], _passive(v0))
    fb = v["feedback"]
    assert v["rng_enabled"] is True and fb["buckets"] and fb["buckets"][-1]["to"] == 100
    assert fb["rng_action"] in {b["token"] for b in fb["buckets"]}
    assert isinstance(fb["pure"], bool) and fb["matched_rng"] == (fb["chosen"] == fb["rng_action"])


def test_rng_disabled_keeps_the_deal(tmp_path: Path) -> None:
    on = _trainer(tmp_path / "on").new(Config(seed=8, hero_seat="CO"))
    t = _trainer(tmp_path / "off")
    off = t.new(Config(seed=8, hero_seat="CO", rng_enabled=False))
    assert off["hero"]["cards"] == on["hero"]["cards"] and off["history"] == on["history"]
    assert off["rng"] is None and off["rng_enabled"] is False and on["rng"] is not None
    fb = t.act(off["sid"], _passive(off))["feedback"]
    assert fb["rng"] is None and fb["matched_rng"] is None and fb["rng_action"] is None
    assert fb["buckets"]  # still shown
    with pytest.raises(TrainError):
        Config(window=0)


def _rows(db: Path) -> tuple[list, list]:
    import sqlite3

    with sqlite3.connect(db) as c:
        return (c.execute("SELECT * FROM train_hands").fetchall(),
                c.execute("SELECT * FROM train_decisions ORDER BY hand_uid, seq").fetchall())  # fmt: skip


def test_hands_are_written_at_hand_end_only(tmp_path: Path) -> None:
    db = tmp_path / "pn.sqlite"
    t = Trainer(SolveCache(tmp_path / "cache", runner=fake_runner), background=False, db=db)
    v = t.new(Config(seed=11, hero_seat="BB"))
    assert v["rolling"] == {"window": 1000, "hands": 0, "rng_match_rate": None,
                            "blunder_rate": None, "mixed_rate": None, "best_rate": None,
                            "leaks_top": []}  # fmt: skip
    v = t.act(v["sid"], _passive(v))
    assert v["result"] is None and v["feedback"] and _rows(db) == ([], [])  # mid-hand: nothing
    v = _play_hand(t, v)
    hands = _rows(db)[0]
    assert len(hands) == 1 and v["rolling"]["hands"] == 1
    import sqlite3

    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        h = dict(c.execute("SELECT * FROM train_hands").fetchone())
        d = [dict(r) for r in c.execute("SELECT * FROM train_decisions ORDER BY seq")]
    hero_pos = v["hero"]["position"]
    assert h["hand_uid"] == f"{v['sid']}-1" and h["hero_pos"] == hero_pos == "BB"
    assert h["net_bb"] == pytest.approx(v["result"]["net"][hero_pos] / 100)
    assert [r["seq"] for r in d] == list(range(len(d))) and d[0]["street"] == "preflop"
    assert d[0]["situation"] == "vs_open" and d[0]["position"] == "BB" and d[0]["rng"] is not None
    assert all(r["situation"] in {"rfi", "vs_open", "vs_3bet", "vs_4bet+", "cold", "first",
                                  "facing_bet", "facing_raise"} for r in d)  # fmt: skip
    # an abandoned hand (a new session started mid-hand) writes nothing
    v2 = t.next(v["sid"])
    assert v2["result"] is None
    t.new(Config(seed=12))
    assert len(_rows(db)[0]) == 1


def test_postflop_drill_never_lands_in_a_multiway_flop(tmp_path: Path) -> None:
    from dataclasses import replace

    from pn.train.drills import Drill
    from pn.train.leaks import Line

    t = _trainer(tmp_path)
    v = t.new(Config(seed=1, hero_seat="BB", drill_share=0.0))
    for _ in range(60):
        s = t.get(v["sid"])
        line = Line(s.hand.players[next(i for i, p in enumerate(s.hand.players) if p.hero)].position,
                    (), ("gtow", ()))  # no forced steps: villains play naturally
        s.hand = replace(s.hand, drill=Drill("post|srp|oop|flop|first", line),
                         drill_spot="post|srp|oop|flop|first")  # fmt: skip
        v = t.state(v["sid"])
        while v["result"] is None and v["street"] == "preflop":
            v = t.act(v["sid"], _passive(v))
        if v["result"] is None and v["solve"]["note"] == "multiway":
            h = t.get(v["sid"]).hand
            assert h.drill is None and not h.drill_landed
            return
        v = _play_hand(t, v)
        v = t.next(v["sid"])
    raise AssertionError("no multiway flop in 60 hands")


# ------------------------------------------------------------------ library-first flops


def _play_to_flop(t: Trainer, v: dict, hands: int = 60) -> dict:
    """Play passively until a heads-up flop is dealt to hero (or the hand ends at it)."""
    for _ in range(hands):
        while v["result"] is None and v["street"] == "preflop":
            v = t.act(v["sid"], _passive(v))
        h = t.get(v["sid"]).hand
        if len(h.state.board) >= 3 and len(h.state.in_hand()) == 2:
            return v
        if v["result"] is None:
            v = _play_hand(t, v)
        v = t.next(v["sid"])
    raise AssertionError("no heads-up flop")


def test_heads_up_flops_come_from_the_solved_set(tmp_path: Path) -> None:
    from pn.train import flop_library as flib
    from pn.train import suitmap as sm

    calls: list[dict] = []

    def counting(spot, backend=None):
        calls.append(spot)
        return fake_runner(spot, backend)

    t = _trainer(tmp_path, runner=counting)
    v = _play_to_flop(t, t.new(Config(seed=3, hero_seat="BB")))
    h = t.get(v["sid"]).hand
    if v["result"] is not None:  # a line outside the library: the hand ended at the flop
        assert v["solve"]["note"] == "no_library_solve"
        return
    assert v["street"] == "flop" and v["solve"]["status"] == "ready" and v["solve"]["backend"] == "texas"
    board = "".join(sm.permute_cards(v["board"], sm.inverse(h.perm)))  # the document's board
    m = flib.matchup_of(h.state, "6max", 100)
    assert board in t.flops.boards(m.mid)  # a solved board of this matchup, dealt suit-permuted
    assert not any(c["game"]["street"] == "flop" for c in calls)  # never solved live
    assert not set(v["board"]) & {c for p in h.players for c in p.cards}  # dealable


def test_turn_is_lifted_live_from_the_library_leaf_with_the_real_hero(tmp_path: Path) -> None:
    calls: list[dict] = []

    def counting(spot, backend=None):
        calls.append({"spot": spot, "backend": backend})
        return fake_runner(spot, backend)

    t = _trainer(tmp_path, runner=counting)
    v = _to_turn(t, t.new(Config(seed=5, hero_seat="BB")))
    turn = [c for c in calls if c["spot"]["game"]["street"] == "turn"]
    assert turn and turn[-1]["backend"] == "texas"
    spot = turn[-1]["spot"]
    assert spot["game"]["time_limit_sec"] == 10 and len(spot["game"]["board"]) == 4  # trainer preset
    hero = next(p for p in spot["players"] if p.get("hero"))
    assert hero["position"] == v["hero"]["position"]  # the session's hero, not the library's


def test_no_solved_board_ends_the_hand_at_the_flop(tmp_path: Path) -> None:
    calls: list[dict] = []
    t = Trainer(SolveCache(tmp_path / "empty", runner=lambda s, b=None: calls.append(s)),
                background=False)  # fmt: skip
    v = t.new(Config(seed=3, hero_seat="BB"))
    for _ in range(60):
        while v["result"] is None:
            v = t.act(v["sid"], _passive(v))
        h = t.get(v["sid"]).hand
        if len(h.state.in_hand()) == 2 and len(h.state.board) == 3 and h.solve.note == "no_library_solve":
            break
        v = t.next(v["sid"])
    else:
        raise AssertionError("no heads-up flop reached")
    assert v["hand_over"] and v["solve"]["status"] == "none" and v["solve"]["note"] == "no_library_solve"
    assert len(v["result"]["board"]) == 5  # run out, no postflop betting
    assert all(d["street"] == "preflop" for d in h.decisions)  # nothing graded postflop
    assert calls == []


def test_hero_outside_the_library_range_is_not_graded() -> None:
    from pn.solve.request import SpotRequest

    doc = {"combos": ["AsAd", "KsKd"], "players": [{"position": "BB"}, {"position": "BTN"}],
           "root": "n0", "nodes": {"n0": {
               "type": "action", "player": 0, "street": "flop",
               "actions": [{"token": "check", "child": "n1"}, {"token": "bet50", "child": "n2"}],
               "strategy": [[0.25, 0.75], [0.0, 0.0]],
               "reach_by_player": [[1.0, 0.0], [1.0, 1.0]], "summary": {"check": 0.25, "bet50": 0.75}}}}  # fmt: skip
    tree = post.Tree(doc, "n0", SpotRequest(seats=(), street="flop", board=(), pot=0, sb=50, bb=100))
    inside = post.hero_grade(tree, "AsAd", "check", 100)
    assert inside["grade"] == "mixed" and inside["loss_bb"] is None and inside["note"] is None
    outside = post.hero_grade(tree, "KsKd", "check", 100)
    assert outside["grade"] == "none" and outside["freq"] is None and outside["freq_error"] is None
    assert outside["note"] == "combo_outside_range" and outside["best"] == "bet50"  # range summary


def test_ev_documents_still_fill_loss_bb() -> None:
    """A document with meta.ev_available (not TexasSolver) still reports loss_bb; the grade
    stays frequency-based."""
    from pn.solve.request import SpotRequest

    doc = {"meta": {"ev_available": True}, "combos": ["AsAd"], "players": [{"position": "BB"}],
           "root": "n0", "nodes": {"n0": {
               "type": "action", "player": 0, "street": "flop",
               "actions": [{"token": "check", "child": "n1"}, {"token": "bet50", "child": "n2"}],
               "strategy": [[0.25, 0.75]], "action_ev": [[10.0, 30.0]],
               "reach_by_player": [[1.0]], "summary": {"check": 0.25, "bet50": 0.75}}}}  # fmt: skip
    tree = post.Tree(doc, "n0", SpotRequest(seats=(), street="flop", board=(), pot=0, sb=50, bb=100))
    g = post.hero_grade(tree, "AsAd", "check", 100)
    assert g["loss_bb"] == pytest.approx(0.2) and g["grade"] == "mixed"
    assert [a["ev_bb"] for a in g["actions"]] == [0.1, 0.3]


def test_street_without_a_leaf_is_off_tree(tmp_path: Path) -> None:
    def boom(spot, backend=None):
        raise SolveError("solver exploded")

    t = _trainer(tmp_path, runner=boom)
    v = _to_turn(t, t.new(Config(seed=5, hero_seat="BB")))
    assert v["solve"]["status"] == "failed"  # the turn solve failed: no turn tree
    while v["result"] is None and v["street"] == "turn":
        v = t.act(v["sid"], _passive(v))
    if v["result"] is None:  # the river has no leaf to lift from: not re-solved
        assert v["solve"] == {"status": "none", "backend": None, "note": "off_tree", "name": None,
                              "detail": None}
        v = t.act(v["sid"], _passive(v))
        assert v["feedback"]["street"] == "river" and v["feedback"]["note"] == "off_tree"
        assert v["feedback"]["grade"] == "none"


# ------------------------------------------------------------------ Plan 13: hand feedback, retry, drill pins


def _mid_hand(t: Trainer, v: dict, hands: int = 60) -> tuple[dict, dict]:
    """(view before, view after) a hero decision that leaves the hand running with hero due."""
    for _ in range(hands):
        while v["result"] is None and v["legal_actions"]:
            before = v
            v = t.act(v["sid"], _passive(v))
            if v["result"] is None and v["legal_actions"]:
                return before, v
        v = t.next(v["sid"])
    raise AssertionError("no mid-hand decision")


def _count(db: Path) -> tuple[int, int]:
    hands, decisions = _rows(db)
    return len(hands), len(decisions)


def test_hand_feedback_lists_this_hands_decisions_and_resets_on_next(tmp_path: Path) -> None:
    t = _trainer(tmp_path)
    v = t.new(Config(seed=11, hero_seat="BB"))
    assert v["hand_feedback"] == [] and v["replay"] is False and v["pinned_drill"] is None
    n = 0
    while v["result"] is None:
        v = t.act(v["sid"], _passive(v))
        n += 1
        assert len(v["hand_feedback"]) == n and v["hand_feedback"][-1] == v["feedback"]
        assert all(f["retry"] is False for f in v["hand_feedback"])
    v = t.next(v["sid"])
    assert v["hand_feedback"] == [] and v["feedback"] is None and v["replay"] is False


def test_retry_while_the_hand_runs(tmp_path: Path) -> None:
    db = tmp_path / "pn.sqlite"
    t = Trainer(SolveCache(tmp_path / "cache", runner=fake_runner), background=False, db=db)
    before, after = _mid_hand(t, t.new(Config(seed=11, hero_seat="BB")))
    sid = after["sid"]
    r = t.retry(sid)
    assert r["replay"] is True and r["result"] is None
    assert r["history"] == before["history"] and r["board"] == before["board"]
    assert r["street"] == before["street"] and r["to_act"] == before["to_act"]
    assert [a["token"] for a in r["legal_actions"]] == [a["token"] for a in before["legal_actions"]]
    assert r["totals"]["decisions"] == after["totals"]["decisions"]  # the original decision stays counted
    assert r["totals"]["hands"] == 1  # the original attempt is closed (recorded) at the rewind
    assert r["hand_feedback"] == after["hand_feedback"] and r["feedback"] == after["feedback"]
    v = t.act(sid, _passive(r))
    assert v["feedback"]["retry"] is True and v["hand_feedback"][-1]["retry"] is True
    assert len(v["hand_feedback"]) == len(after["hand_feedback"]) + 1
    assert v["totals"]["decisions"] == after["totals"]["decisions"]  # the replay is not counted
    counted = len(after["hand_feedback"])
    v = _play_hand(t, v)
    assert v["replay"] is True and v["totals"]["decisions"] == after["totals"]["decisions"]
    assert v["totals"]["hands"] == 1  # the hand is recorded once
    assert _count(db) == (1, counted)  # only the original decisions reach train_decisions
    assert t.next(sid)["replay"] is False


def test_retry_while_the_hand_runs_keeps_the_original_net(tmp_path: Path) -> None:
    """The original attempt is closed at the rewind as abandoned (net = minus hero's chips in);
    whatever the replay then does (here: fold, when legal) never changes hero_net or net_bb."""
    db = tmp_path / "pn.sqlite"
    t = Trainer(SolveCache(tmp_path / "cache", runner=fake_runner), background=False, db=db)
    _, after = _mid_hand(t, t.new(Config(seed=11, hero_seat="BB")))
    sid = after["sid"]
    net = after["hero"]["stack"] - after["depth"] * after["bb"]  # minus hero's contribution
    assert net < 0
    hands = after["totals"]["hands"]
    r = t.retry(sid)
    assert r["totals"]["hands"] == hands + 1
    assert r["totals"]["hero_net"] == after["totals"]["hero_net"] + net
    uid = f"{sid}-{after['hand_no']}"
    toks = [a["token"] for a in r["legal_actions"]]
    v = t.act(sid, "fold" if "fold" in toks else _passive(r))
    v = _play_hand(t, v)
    assert v["totals"] == r["totals"]  # the replay's finish adds nothing
    import sqlite3

    with sqlite3.connect(db) as c:
        assert c.execute("SELECT net_bb FROM train_hands WHERE hand_uid = ?", (uid,)).fetchone() == (
            net / after["bb"],)
        assert c.execute("SELECT COUNT(*) FROM train_hands").fetchone() == (hands + 1,)


def test_retry_after_the_hand_ended_does_not_record_it_twice(tmp_path: Path) -> None:
    db = tmp_path / "pn.sqlite"
    t = Trainer(SolveCache(tmp_path / "cache", runner=fake_runner), background=False, db=db)
    v = _play_hand(t, t.new(Config(seed=11, hero_seat="BB")))
    rows, done = _count(db), v
    assert rows[0] == 1 and v["totals"]["hands"] == 1
    r = t.retry(v["sid"])
    assert r["result"] is None and not r["hand_over"] and r["replay"] is True and r["legal_actions"]
    assert len(r["history"]) < len(done["history"])
    v = _play_hand(t, r)
    assert all(f["retry"] for f in v["hand_feedback"][len(done["hand_feedback"]) :])
    assert _count(db) == rows
    assert v["totals"] == done["totals"]  # no extra hand, hero_net or decision
    r2 = t.retry(v["sid"])  # retry during a replay rewinds to before the latest decision
    assert r2["replay"] is True and r2["result"] is None
    assert t.next(_play_hand(t, r2)["sid"])["hand_no"] == 2 and _count(db) == rows


def test_retry_errors(tmp_path: Path) -> None:
    t = _trainer(tmp_path)
    v = t.new(Config(seed=11, hero_seat="BB"))
    with pytest.raises(TrainError):
        t.retry(v["sid"])  # no hero decision this hand
    v = t.next(_play_hand(t, v)["sid"])
    with pytest.raises(TrainError):
        t.retry(v["sid"])  # next clears the snapshots
    with pytest.raises(SessionNotFound):
        t.retry("nope")


def test_retry_refused_while_solving_and_drops_the_abandoned_future(tmp_path: Path) -> None:
    gate = threading.Event()

    def slow(spot, backend=None):
        gate.wait(10)
        return fake_runner(spot, backend)

    t = _trainer(tmp_path, runner=slow, background=True)
    v = t.new(Config(seed=5, hero_seat="BB"))
    for _ in range(40):
        while v["result"] is None and not v["solving"] and v["legal_actions"]:
            before = v
            v = t.act(v["sid"], _passive(v))
        if v["solving"]:
            break
        if v["result"] is None:
            v = _play_hand(t, v)
        v = t.next(v["sid"])
    assert v["solving"] and v["street"] == "turn"
    with pytest.raises(TrainError):
        t.retry(v["sid"])
    gate.set()
    s = t.get(v["sid"])
    for _ in range(100):
        if s.future is not None and s.future.done():
            break
        time.sleep(0.05)
    r = t.retry(v["sid"])
    assert s.future is None and s.pending_req is None
    assert not r["solving"] and r["street"] == before["street"] == "flop"
    assert r["history"] == before["history"] and r["legal_actions"]


def _drill_trainer(cache: Path, lib=None) -> Trainer:
    import fake_preflop

    from pn.solve.lazydoc import LazyDoc
    from pn.train.leaks import Leaks

    lib = lib or LazyDoc.open(fake_preflop.write(cache.parent / "lib.json"))
    return Trainer(SolveCache(cache, runner=fake_runner), background=False,
                   leaks=Leaks(None, library=lambda table, depth: lib))  # fmt: skip


def _pf_spot(t: Trainer, kind: str = "|vs_3bet|") -> str:
    from pn.train.leaks import library_walk

    return next(k for k in library_walk(t.leaks.library(6, 100)).lines if kind in k)


def test_pinned_drill_deals_every_next_hand_until_unpinned(tmp_path: Path) -> None:
    from pn.train import spots

    t = _drill_trainer(tmp_path / "cache")
    v = t.new(Config(seed=3, drill_share=0.0))
    spot = _pf_spot(t)
    assert v["drill"] is None and v["pinned_drill"] is None
    p = t.drill(v["sid"], spot)
    assert p["pinned_drill"] == {"spot": spot, "label": spots.label(spot), "dealt": False, "texture": None,
                                 "formation": None}  # fmt: skip
    assert p["drill"] is None and p["hand_no"] == v["hand_no"]  # the current hand is untouched
    for _ in range(3):
        v = t.next(_play_hand(t, t.state(v["sid"]))["sid"])
        assert v["drill"] == {"spot": spot, "label": spots.label(spot)}
        assert v["pinned_drill"]["spot"] == spot and v["pinned_drill"]["dealt"] is True
    assert t.drill(v["sid"], None)["pinned_drill"] is None
    v = t.next(_play_hand(t, t.state(v["sid"]))["sid"])
    assert v["drill"] is None and v["pinned_drill"] is None  # drill_share 0 again


def test_drill_spot_in_the_config_and_undealable_pins(tmp_path: Path) -> None:
    t = _drill_trainer(tmp_path / "cache")
    spot = _pf_spot(t, "|vs_open|")
    v = t.new(Config(seed=4, drill_share=0.0, drill_spot=spot))
    assert v["drill"]["spot"] == spot and v["pinned_drill"]["spot"] == spot
    with pytest.raises(TrainError):
        Config(drill_spot="not a spot")
    with pytest.raises(TrainError):
        t.drill(v["sid"], "pf|CO|vs_4bet+|UTG")  # well-formed, no line in the library
    bare = _drill_trainer(tmp_path / "empty")  # no solved flop: postflop spots are undealable
    with pytest.raises(TrainError):
        bare.new(Config(seed=4, drill_spot="post|srp|oop|flop|first"))
    plain = _trainer(tmp_path)  # no leak model
    with pytest.raises(TrainError):
        plain.drill(plain.new(Config(seed=4))["sid"], spot)
    assert t.state(v["sid"])["pinned_drill"]["spot"] == spot  # a refused pin changes nothing


def test_pinned_drill_that_fails_at_deal_time_says_so(tmp_path: Path, monkeypatch) -> None:
    from pn.train import drills

    t = _drill_trainer(tmp_path / "cache")
    spot = _pf_spot(t, "|vs_open|")
    v = t.new(Config(seed=4, drill_share=0.0, drill_spot=spot))
    assert v["pinned_drill"]["dealt"] is True

    def broken(*a, **k):
        raise ValueError("no combos")

    monkeypatch.setattr(drills, "sample_hands", broken)
    v = t.next(_play_hand(t, v)["sid"])
    assert v["drill"] is None  # a natural hand ...
    assert v["pinned_drill"]["spot"] == spot and v["pinned_drill"]["dealt"] is False  # ... and the state says so
