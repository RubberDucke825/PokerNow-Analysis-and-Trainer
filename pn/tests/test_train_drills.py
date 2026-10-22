"""Plan 6 (contracts/trainer.md "Plan 6 additions"): spot keys, solver frequencies from a hand-built
lazy preflop root and from GTO Wizard chains, shrinkage and weights, drill construction with a
seeded session, the deviation path, solver_unavailable, SolveCache priority, and the CLI."""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pn import ranges
from pn.cli.main import app
from pn.ingest import db as ingest_db
from pn.solve import SolveCache
from pn.solve.backend import SolveError
from pn.solve.cache import PriorityLock
from pn.solve.lazydoc import LazyDoc
from pn.train import Config, Trainer, leaks, spots
from pn.train import importance as imp
from pn.train.engine import apply, next_street, start
from pn.train.importance import spot_rows
from pn.train.leaks import Leaks, gtow_walk, library_walk, token_kind

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import fake_preflop
from fake_tree import fake_runner

ORDER = fake_preflop.ORDER


@pytest.fixture(scope="module")
def lib(tmp_path_factory: pytest.TempPathFactory) -> LazyDoc:
    path = fake_preflop.write(tmp_path_factory.mktemp("lib") / "6max_100bb_gto.json")
    return LazyDoc.open(path)


# ------------------------------------------------------------------ spot keys


@pytest.mark.parametrize(("decision", "key", "text"), [
    ({"street": "preflop", "position": "CO", "faced": "rfi", "vs": None}, "pf|CO|rfi|-",
     "CO first in"),
    ({"street": "preflop", "position": "BB", "faced": "vs_open", "vs": "BTN"},
     "pf|BB|vs_open|BTN", "BB vs BTN open"),
    ({"street": "preflop", "position": "LJ", "faced": "vs_4bet+", "vs": "BTN"},
     "pf|LJ|vs_4bet+|BTN", "LJ vs BTN 4-bet+"),
    ({"street": "flop", "pot": "srp", "ip": True, "situation": "facing_bet"},
     "post|srp|ip|flop|facing_bet", "SRP IP flop facing a bet"),
    ({"street": "river", "pot": "3bp", "ip": False, "situation": "first"},
     "post|3bp|oop|river|first", "3BP OOP river first to act"),
])  # fmt: skip
def test_spot_key_table(decision: dict, key: str, text: str) -> None:
    assert spots.spot_key(decision) == key and spots.label(key) == text
    back = spots.parse(key)
    assert spots.spot_key(back) == key
    with pytest.raises(ValueError):
        spots.parse("pf|CO")


def test_preflop_fields_and_from_state() -> None:
    assert spots.preflop_fields("BB", [], 1) == {"faced": "vs_limp", "vs": "-"}
    assert spots.preflop_fields("CO", ["LJ", "HJ"], 0) == {"faced": "cold", "vs": "HJ"}
    assert spots.preflop_fields("LJ", ["LJ", "HJ"], 0) == {"faced": "vs_3bet", "vs": "HJ"}
    assert spots.preflop_fields("HJ", ["LJ", "HJ", "LJ"], 0)["faced"] == "vs_4bet+"
    assert [spots.pot_type(n) for n in range(5)] == ["limped", "srp", "3bp", "4bp+", "4bp+"]
    st = start(ORDER, 10_000, 50, 100)
    assert spots.from_state(st, 0) == "pf|LJ|rfi|-"
    st = apply(st, "raise", 250)
    for _ in range(4):
        st = apply(st, "fold")  # HJ CO BTN SB
    assert spots.from_state(st, 5) == "pf|BB|vs_open|LJ"
    flop = next_street(apply(st, "call"), ("Kd", "7s", "2c"))
    assert spots.from_state(flop, flop.to_act) == "post|srp|oop|flop|first"
    bet = apply(flop, "raise", 300)
    assert spots.from_state(bet, bet.to_act) == "post|srp|ip|flop|facing_bet"


# ------------------------------------------------------------------ frequencies


def test_library_walk_on_a_lazy_root(lib: LazyDoc) -> None:
    fm = library_walk(lib)
    root = lib["nodes"][lib.root]
    lj_open = root["summary"]["raise250"]
    assert fm.freq["pf|LJ|rfi|-"] == pytest.approx(1 / 6)
    assert fm.freq["pf|HJ|rfi|-"] == pytest.approx((1 - lj_open) / 6, rel=1e-6)
    assert fm.freq["pf|HJ|vs_open|LJ"] == pytest.approx(lj_open / 6, rel=1e-6)
    # the partial node reads agree with a fully loaded document
    full = library_walk(json.loads(lib.path.read_text()))
    assert full.freq == pytest.approx(fm.freq)
    line = fm.lines["pf|HJ|vs_open|LJ"]
    assert line.hero == "HJ" and [(s.position, s.kind) for s in line.steps] == [("LJ", "raise")]
    assert line.hero_src[0] == "lib_reach"
    post = fm.lines["post|srp|ip"]
    assert post.hero_src[0] == "lib_leaf" and fm.freq["post|srp|ip"] > 0
    assert sum(v for k, v in fm.freq.items() if k.startswith("post|")) <= 1.0


def _cells(share_r: float) -> dict:
    return {cls: {"R": 100 * share_r, "F": 100 * (1 - share_r)} for cls in ("AA", "72o")}


def test_gtow_chains() -> None:
    freqs = {f"100|rfi|{p}": {"cells": _cells(0.5)} for p in ("UTG", "HJ", "CO", "BTN", "SB")}
    freqs["100|vs_open|UTG|HJ"] = {"cells": _cells(0.25)}
    fm = gtow_walk(freqs, 6, 100, pokercoaching=False)
    assert fm.source == "gtow"
    assert fm.freq["pf|LJ|rfi|-"] == pytest.approx(1 / 6)
    assert fm.freq["pf|CO|rfi|-"] == pytest.approx(0.25 / 6)
    assert fm.freq["pf|HJ|vs_open|LJ"] == pytest.approx(0.5 / 6)
    # CO vs LJ open: HJ folds to the open (0.75)
    assert fm.freq["pf|CO|vs_open|LJ"] == pytest.approx(0.5 * 0.75 / 6)
    assert fm.lines["pf|CO|vs_open|LJ"].steps[0].kind == "raise"
    eight = gtow_walk(freqs, 8, 100, pokercoaching=False)  # Plan 16: 8 seats, EP keys PokerCoaching only
    assert eight.freq["pf|UTG|rfi|-"] == pytest.approx(1 / 8)  # no EP chart here: UTG / UTG1 fold
    assert eight.freq["pf|LJ|rfi|-"] == pytest.approx(1 / 8)
    assert gtow_walk(freqs, 9, 100).freq == {}
    assert gtow_walk(freqs, 6, 200, pokercoaching=False).freq["pf|LJ|rfi|-"] == pytest.approx(1 / 6)  # nearest depth


def test_postflop_conditionals_from_cached_street_solves(tmp_path: Path) -> None:
    from pn.solve import build_spot
    from pn.solve.request import new_request, parse_board

    cache = SolveCache(tmp_path, runner=fake_runner)
    cache.solve(build_spot(new_request(["BTN", "BB"], 100, board=parse_board("Kd7s2c"),
                                       line="srp")))  # fmt: skip
    model = Leaks(None, library=lambda t, d: None, cache_root=tmp_path, freqs={})
    _, cond = model.solver_map(6, 100)
    side = "ip"
    # both seats of the document count: the OOP player is always first on the flop
    assert cond["post|srp|oop|flop|first"] == pytest.approx(1.0)
    first = cond[f"post|srp|{side}|flop|first"]
    assert 0 < first <= 1 and cond.get(f"post|srp|{side}|flop|facing_bet", 0) >= 0
    assert not any("|turn|" in k for k in cond)  # no turn solve cached yet
    cache.solve(build_spot(new_request(["BTN", "BB"], 100, board=parse_board("Kd7s2c3h"),
                                       line="srp")))  # fmt: skip
    _, cond = model.solver_map(6, 100)  # only the new entry is read
    stats = [d for ds in model._docs.values() if ds for d in ds]
    flop = next(d for d in stats if d.street == "flop" and d.ip)
    turn = next(d for d in stats if d.street == "turn" and d.ip)
    assert cond[f"post|srp|{side}|turn|first"] == pytest.approx(flop.p_next * turn.occ["first"])


# ------------------------------------------------------------------ shrinkage and weights


def _row(spot: str, grade: str = "best", options=None, chosen="check") -> dict:
    return {"hand_uid": "h", "spot": spot, "chosen": chosen, "grade": grade,
            "options_json": json.dumps(options or []), "drill_spot": None}  # fmt: skip


def test_blunder_fields_shrinkage_and_priority_order() -> None:
    a, b = "post|srp|ip|flop|first", "pf|CO|rfi|-"
    rows = [_row(a, "blunder")] * 3 + [_row(b, "mixed")] * 2 + [_row(b, "none")]
    table = spot_rows({a: 0.1, b: 0.05, "pf|LJ|rfi|-": 0.0}, rows, {})
    out = {r["spot"]: r for r in table}
    assert set(out) == {a, b}  # never reached by the solver: not a drill candidate
    mean = (3.0 + 5 * leaks.PRIOR_BLUNDER_RATE) / (5 + 5)  # the global rate is shrunk too
    assert out[a]["blunder_rate"] == pytest.approx((3 + 5 * mean) / 8, abs=1e-4)
    assert out[b]["blunder_rate"] == pytest.approx(5 * mean / 7, abs=1e-4)
    assert out[a]["blunders"] == 3 and out[b]["blunders"] == 0
    assert out[a]["expected_blunders_per_100"] == pytest.approx(100 * 0.1 * (3 + 5 * mean) / 8, abs=1e-3)
    assert out[a]["n"] == 3 and out[b]["n"] == 2  # ungraded ("none") rows do not count
    assert [r["spot"] for r in table] == [a, b] and out[a]["priority"] == 1.0  # sorted by priority
    assert sum(r["weight"] for r in table) == pytest.approx(1.0, abs=1e-4)
    # a pre-Plan 7 "freq" row is graded from its stored frequencies: 0.05 vs 0.95 -> blunder
    legacy = spot_rows({b: 0.05}, [_row(b, "freq", [{"token": "fold", "freq": 0.05},
                                                    {"token": "raise", "freq": 0.95}], "fold")], {})  # fmt: skip
    one_mean = (1.0 + 5 * leaks.PRIOR_BLUNDER_RATE) / 6
    assert legacy[0]["blunder_rate"] == pytest.approx((1.0 + 5 * one_mean) / 6, abs=1e-4)
    assert legacy[0]["score"] < imp.PRIOR_SCORE


def test_unseen_spots_get_the_prior_and_are_drilled() -> None:
    freq = {"pf|BTN|rfi|-": 0.08, "pf|BB|vs_open|BTN": 0.02, "pf|LJ|vs_4bet+|BTN": 0.0001}
    fresh = {r["spot"]: r for r in spot_rows(freq, [], {})}
    assert set(fresh) == set(freq)
    for s, r in fresh.items():
        assert r["blunder_rate"] == leaks.PRIOR_BLUNDER_RATE and r["n"] == 0 and r["score"] == imp.PRIOR_SCORE
        assert r["priority"] == pytest.approx(freq[s] / 0.08, abs=1e-4)  # same pot and score: by freq
        assert r["weight"] > 0
    assert sum(r["weight"] for r in fresh.values()) == pytest.approx(1.0, abs=1e-4)
    assert fresh["pf|BTN|rfi|-"]["weight"] > fresh["pf|LJ|vs_4bet+|BTN"]["weight"]
    # with history, a spot never reached is scored at the global score
    seen = "pf|BTN|rfi|-"
    out = {r["spot"]: r for r in spot_rows(freq, [_row(seen, "blunder")] * 4, {})}
    g = (0.0 + 5 * imp.PRIOR_SCORE) / 9
    assert out["pf|BB|vs_open|BTN"]["score"] == pytest.approx(g, abs=1e-4)
    assert out["pf|BB|vs_open|BTN"]["weight"] > 0


def test_one_clean_decision_does_not_zero_every_spot() -> None:
    freq = {"pf|BTN|rfi|-": 0.08, "pf|BB|vs_open|BTN": 0.02, "post|srp|ip|flop|first": 0.05}
    out = spot_rows(freq, [_row("pf|BTN|rfi|-", "best")], {})  # a single recorded hand, played perfectly
    assert all(r["priority"] > 0 and r["expected_blunders_per_100"] > 0 for r in out)
    unseen = [r["spot"] for r in out if r["n"] == 0]
    assert unseen == ["post|srp|ip|flop|first", "pf|BB|vs_open|BTN"]  # by frequency, not name


# ------------------------------------------------------------------ drills


class OneSpot(Leaks):
    """Always drills `spot` (the leak table is replaced by a single row)."""

    def __init__(self, spot: str, **kw) -> None:
        super().__init__(None, **kw)
        self.spot = spot

    def importance(self, conn, table, depth, window, **kw):
        row = {"spot": self.spot, "label": spots.label(self.spot), "weight": 1.0}
        return {"spots": [row], "formations": [], "textures": [], "params": {}}


def _node(doc, history_kinds: list[str]) -> dict | None:
    """Fixture node reached by the engine's preflop history (matched by action kind); None
    once natural play leaves the fixture's small action set."""
    node = doc["nodes"][doc.root]
    for kind in history_kinds:
        child = next((a["child"] for a in node.get("actions", [])
                      if token_kind(a["token"]) == kind), None)  # fmt: skip
        if child is None:
            return None
        node = doc["nodes"][child]
    return node


def _solver_hero(doc):
    """Hero plays the fixture's (pure) strategy for its combo preflop, passively postflop."""
    idx = {c: k for k, c in enumerate(doc["combos"])}

    def choose(t: Trainer, v: dict) -> str:
        toks = [o["token"] for o in v["legal_actions"]]
        h = t.get(v["sid"]).hand
        if v["street"] == "preflop":
            node = _node(doc, [a.kind for a in h.state.history])
            if node is not None and node.get("type") == "action":
                combo = ranges.canonical_combo("".join(v["hero"]["cards"]))
                row = node["strategy"][idx[combo]]
                kind = token_kind(node["actions"][max(range(len(row)), key=row.__getitem__)]["token"])
                if kind in toks:
                    return kind
        return next(x for x in ("check", "call", "fold") if x in toks)

    return choose


def _play(t: Trainer, v: dict, choose) -> dict:
    for _ in range(60):
        if v["result"] is not None:
            return v
        v = t.act(v["sid"], choose(t, v))
    raise AssertionError("hand did not finish")


def test_drills_land_in_the_target_spot(lib: LazyDoc, tmp_path: Path) -> None:
    db = tmp_path / "pn.sqlite"
    t = Trainer(SolveCache(tmp_path / "cache", runner=fake_runner), background=False, db=db,
                leaks=Leaks(db, library=lambda table, depth: lib))  # fmt: skip
    v = t.new(Config(seed=13, drill_share=1.0))
    hero = _solver_hero(lib)
    attempts = landed = 0
    for _ in range(25):
        h = t.get(v["sid"]).hand
        assert h.drill is not None and v["drill"] == {"spot": h.drill.spot,
                                                      "label": spots.label(h.drill.spot)}
        assert v["hero"]["position"] == h.drill.line.hero
        v = _play(t, v, hero)
        attempts += 1
        landed += t.get(v["sid"]).hand.drill_landed
        v = t.next(v["sid"])
    assert landed / attempts >= 0.8
    with sqlite3.connect(db) as c:
        rows = c.execute("SELECT drill_spot, drill_landed FROM train_hands").fetchall()
        spot_rows = c.execute("SELECT spot FROM train_decisions").fetchall()
    assert len(rows) == attempts and all(r[0] for r in rows)
    assert sum(r[1] for r in rows) == landed and all(r[0] for r in spot_rows)
    assert v["rolling"]["leaks_top"] and len(v["rolling"]["leaks_top"]) <= 3


def test_villains_follow_the_line_with_hands_from_their_strategy(lib: LazyDoc, tmp_path: Path) -> None:
    fm = library_walk(lib)
    spot = next(k for k in fm.lines if k.startswith("pf|") and "|cold|" in k)
    t = Trainer(SolveCache(tmp_path, runner=fake_runner), background=False,
                leaks=OneSpot(spot, library=lambda table, depth: lib))  # fmt: skip
    v = t.new(Config(seed=4, drill_share=1.0))
    h = t.get(v["sid"]).hand
    line = fm.lines[spot]
    assert v["to_act"] == line.hero and h.drill.landed is False
    assert [(x["position"], x["token"] if x["token"] != "allin" else "raise") for x in v["history"]] == [
        (s.position, "check" if s.kind == "check" else s.kind) for s in line.steps]
    assert all(x["origin"] == "drill" for x in v["history"])
    pct = {p.position: fake_preflop.class_percentile(ranges.combo_class(p.combo)) for p in h.players}
    raisers = [s.position for s in line.steps if s.kind == "raise"]
    assert pct[raisers[0]] < fake_preflop.OPEN and pct[raisers[1]] < fake_preflop.THREE_BET
    assert line.hero not in raisers  # cold: hero has not acted, so its whole range reaches


def test_hero_hand_comes_from_its_reach(lib: LazyDoc, tmp_path: Path) -> None:
    spot = next(k for k in library_walk(lib).lines if "|vs_3bet|" in k)
    t = Trainer(SolveCache(tmp_path, runner=fake_runner), background=False,
                leaks=OneSpot(spot, library=lambda table, depth: lib))  # fmt: skip
    for seed in range(5):
        v = t.new(Config(seed=seed, drill_share=1.0))
        cls = ranges.combo_class(ranges.canonical_combo("".join(v["hero"]["cards"])))
        assert fake_preflop.class_percentile(cls) < fake_preflop.OPEN  # hero opened in the line


def test_deviation_continues_naturally(lib: LazyDoc, tmp_path: Path) -> None:
    fm = library_walk(lib)
    spot = next(k for k in fm.lines if "|vs_3bet|" in k)  # hero must open first
    db = tmp_path / "pn.sqlite"
    t = Trainer(SolveCache(tmp_path / "cache", runner=fake_runner), background=False, db=db,
                leaks=OneSpot(spot, library=lambda table, depth: lib))  # fmt: skip
    v = t.new(Config(seed=9, drill_share=1.0))
    assert v["drill"]["spot"] == spot and v["to_act"] == fm.lines[spot].hero
    v = t.act(v["sid"], "fold")  # hero leaves the line
    assert v["drill"] is None
    h = t.get(v["sid"]).hand
    assert h.drill_spot == spot and not h.drill_landed
    v = _play(t, v, lambda t, v: next(x for x in ("check", "call", "fold")
                                        if x in [o["token"] for o in v["legal_actions"]]))  # fmt: skip
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT drill_spot, drill_landed FROM train_hands").fetchall() == [(spot, 0)]


def test_postflop_drill_steers_villains(lib: LazyDoc, tmp_path: Path, flop_template: Path) -> None:
    import flop_seed

    flop_seed.copy_into(flop_template, tmp_path)
    spot = "post|srp|ip|flop|facing_bet"
    t = Trainer(SolveCache(tmp_path, runner=fake_runner), background=False,
                leaks=OneSpot(spot, library=lambda table, depth: lib))  # fmt: skip
    hero = _solver_hero(lib)
    landed = 0
    v = t.new(Config(seed=21, drill_share=1.0))
    for _ in range(8):
        v = _play(t, v, hero)
        h = t.get(v["sid"]).hand
        landed += h.drill_landed
        if h.drill_landed:
            flop = [x for x in v["history"] if x["street"] == "flop"]
            assert flop[0]["token"].startswith("bet") and flop[0]["origin"].startswith("drill:")
        v = t.next(v["sid"])
    assert landed >= 6


def test_drill_share_zero_and_validation(lib: LazyDoc, tmp_path: Path) -> None:
    t = Trainer(SolveCache(tmp_path, runner=fake_runner), background=False,
                leaks=Leaks(None, library=lambda table, depth: lib))  # fmt: skip
    v = t.new(Config(seed=2, drill_share=0.0, hero_seat="CO"))
    assert v["drill"] is None and v["hero"]["position"] == "CO" and v["drill_share"] == 0.0
    assert t.new(Config(seed=2, drill_share=0.5))["drill_share"] == 0.5
    plain = Trainer(SolveCache(tmp_path, runner=fake_runner), background=False)
    assert plain.new(Config(seed=2))["drill_share"] == 0.0  # no leak model: never drills
    with pytest.raises(ValueError):
        Config(drill_share=1.5)


def test_solver_unavailable_is_recorded(tmp_path: Path, flop_template: Path) -> None:
    import flop_seed

    def boom(spot, backend=None):
        raise SolveError("solver exploded")

    flop_seed.copy_into(flop_template, tmp_path / "cache")  # flops from the library; turn fails
    db = tmp_path / "pn.sqlite"
    t = Trainer(SolveCache(tmp_path / "cache", runner=boom), background=False, db=db)
    v = t.new(Config(seed=5, hero_seat="BB"))
    seen = error = None
    for _ in range(40):
        while v["result"] is None:
            toks = [o["token"] for o in v["legal_actions"]]
            failed = v["solve"]["status"] == "failed" and v["street"] == "turn"
            if failed and seen is None:
                error = v["solve_error"]  # the street hero is about to act on
            v = t.act(v["sid"], next(x for x in ("check", "call", "fold") if x in toks))
            if failed and seen is None:
                seen = v
        if seen:
            break
        v = t.next(v["sid"])
    fb = seen["feedback"]
    assert fb["street"] == "turn"
    assert fb["note"] == "solver_unavailable" and fb["grade"] == "none" and fb["loss_bb"] is None
    assert "solver exploded" in error
    v = _play(t, v, lambda t, v: next(x for x in ("check", "call", "fold")
                                          if x in [o["token"] for o in v["legal_actions"]]))  # fmt: skip
    with sqlite3.connect(db) as c:
        notes = [r[0] for r in c.execute(
            "SELECT note FROM train_decisions WHERE street = 'turn' AND grade = 'none' "
            "AND note != 'multiway_unsolvable'")]  # fmt: skip
    assert notes and set(notes) == {"solver_unavailable"}


# ------------------------------------------------------------------ priority and migration


def test_priority_waiters_go_first() -> None:
    lock = PriorityLock()
    order: list[str] = []
    release = threading.Event()

    def holder() -> None:
        with lock.hold():
            release.wait(5)

    def waiter(name: str, priority: bool) -> None:
        with lock.hold(priority):
            order.append(name)

    threads = [threading.Thread(target=holder)]
    threads[0].start()
    time.sleep(0.05)
    for name, prio in (("library", False), ("trainer", True)):
        th = threading.Thread(target=waiter, args=(name, prio))
        th.start()
        threads.append(th)
        time.sleep(0.05)
    release.set()
    for th in threads:
        th.join(5)
    assert order == ["trainer", "library"]


def test_old_train_tables_gain_plan6_columns(tmp_path: Path) -> None:
    db = tmp_path / "old.sqlite"
    with sqlite3.connect(db) as c:  # Plan 4 shape
        c.execute("CREATE TABLE train_hands (hand_uid TEXT PRIMARY KEY, session_id TEXT NOT NULL,"
                  " played_at TEXT NOT NULL, table_size INTEGER NOT NULL, depth INTEGER NOT NULL,"
                  " source TEXT NOT NULL, hero_pos TEXT NOT NULL, net_bb REAL NOT NULL,"
                  " opponents_json TEXT NOT NULL)")  # fmt: skip
        c.execute("CREATE TABLE train_decisions (hand_uid TEXT NOT NULL, seq INTEGER NOT NULL,"
                  " street TEXT NOT NULL, position TEXT NOT NULL, situation TEXT NOT NULL,"
                  " hand_class TEXT NOT NULL, rng INTEGER, options_json TEXT NOT NULL,"
                  " chosen TEXT NOT NULL, rng_action TEXT, best TEXT, loss_bb REAL,"
                  " grade TEXT NOT NULL, matched_rng INTEGER, pure INTEGER NOT NULL DEFAULT 0,"
                  " PRIMARY KEY (hand_uid, seq))")  # fmt: skip
    with ingest_db.opened(db) as conn:
        assert Leaks(None, library=lambda t, d: None, freqs={}).rows(conn, 6, 100, 10) == []
        cols = {r[1] for r in conn.execute("PRAGMA table_info(train_decisions)")}
    assert {"spot", "note"} <= cols


def test_cli_train_leaks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "pn.sqlite"
    ingest_db.connect(db).close()
    monkeypatch.setattr(leaks, "open_library", lambda table, depth: None)
    monkeypatch.setenv("PN_SOLVE_CACHE", str(tmp_path / "cache"))
    r = CliRunner().invoke(app, ["train", "leaks", "--db", str(db), "--top", "200"])
    assert r.exit_code == 0, r.output
    assert r.output.index("LJ first in") < r.output.index("HJ first in")  # same pot and score: by freq
    bad = CliRunner().invoke(app, ["train", "leaks", "--db", str(db), "--window", "0"])
    assert bad.exit_code == 1


# ------------------------------------------------------------------ 2026-10-07: 2-player only


def test_postflop_lines_and_freqs_are_heads_up_only(lib: LazyDoc) -> None:
    full = json.loads(lib.path.read_text())
    multi = [n for n in full["nodes"].values() if n.get("kind") == "leaf"
             and sum(bool(row) and any(w > 0 for w in row) for row in n["reach_by_player"]) > 2]  # fmt: skip
    assert multi, "fixture should contain multiway preflop leaves"
    fm = library_walk(lib)
    post = {k: line for k, line in fm.lines.items() if k.startswith("post|")}
    assert post
    for line in post.values():  # every step is on the path: the non-folders see the flop
        folded = {s.position for s in line.steps if s.kind == "fold"}
        assert len(set(ORDER) - folded) == 2


def test_multiway_street_solves_do_not_feed_conditionals(tmp_path: Path) -> None:
    from pn.solve import build_spot
    from pn.solve.request import new_request, parse_board

    cache = SolveCache(tmp_path, runner=fake_runner)
    cache.solve(build_spot(new_request(["CO", "BTN", "BB"], 100, board=parse_board("Kd7s2c"),
                                       line="srp")), "mwsolver")  # fmt: skip
    model = Leaks(None, library=lambda t, d: None, cache_root=tmp_path, freqs={})
    _, cond = model.solver_map(6, 100)
    assert cond == {} and all(d is None for d in model._docs.values())


def test_observed_postflop_skips_multiway_hands() -> None:
    def r(hand: str, spot: str, note: str | None = None) -> dict:
        return {"hand_uid": hand, "spot": spot, "drill_spot": None, "note": note}

    rows = [r("hu", "pf|BB|vs_open|BTN"), r("hu", "post|srp|oop|flop|first"),
            r("mw", "pf|BB|vs_open|CO"),
            r("mw", "post|srp|oop|flop|first", "multiway_unsolvable"),
            r("mw", "post|srp|oop|turn|first")]  # fmt: skip
    pre, cond = leaks.observed_postflop(rows)
    assert pre == {"post|srp|oop": pytest.approx(0.5)}  # 2 hands, 1 heads-up flop
    assert cond == {"post|srp|oop|flop|first": pytest.approx(1.0)}


def test_library_walk_heads_up_lines_are_library_matchups(lib: LazyDoc) -> None:
    from pn.train import flop_library as flib

    fm = library_walk(lib)
    lines = {m.line for m in flib.matchups(ORDER, 100, "6max", sb=50, bb=100)}
    total = sum(fm.matchups.values())
    covered = sum(f for k, f in fm.matchups.items() if k in lines)  # same keys as the fill uses
    assert 0 < total <= 1.0 and covered / total > 0.8  # squeezes etc. are not library matchups
    assert max(fm.matchups, key=fm.matchups.__getitem__) in lines


def test_postflop_drills_need_a_solved_flop(lib: LazyDoc, tmp_path: Path, flop_template: Path) -> None:
    import flop_seed

    spot = "post|srp|ip|flop|facing_bet"
    bare = Trainer(SolveCache(tmp_path / "bare", runner=fake_runner), background=False,
                   leaks=OneSpot(spot, library=lambda table, depth: lib))  # fmt: skip
    v = bare.new(Config(seed=21, drill_share=1.0))
    assert v["drill"] is None and bare.get(v["sid"]).hand.drill_spot is None  # nothing solved
    flop_seed.copy_into(flop_template, tmp_path / "seeded")
    seeded = Trainer(SolveCache(tmp_path / "seeded", runner=fake_runner), background=False,
                     leaks=OneSpot(spot, library=lambda table, depth: lib))  # fmt: skip
    v = seeded.new(Config(seed=21, drill_share=1.0))
    assert v["drill"] == {"spot": spot, "label": spots.label(spot)}
    pre = OneSpot("pf|BTN|rfi|-", library=lambda table, depth: lib)
    t = Trainer(SolveCache(tmp_path / "bare", runner=fake_runner), background=False, leaks=pre)
    assert t.new(Config(seed=21, drill_share=1.0))["drill"]["spot"] == "pf|BTN|rfi|-"  # preflop: any


def test_heads_up_line_rejects_limps_cold_calls_and_squeezes() -> None:
    from pn.train.leaks import Line, Step
    from pn.train.planner import heads_up_line

    def line(*steps):
        return Line("BB", tuple(Step(p, k) for p, k in steps), ())

    assert heads_up_line(line(("LJ", "raise"), ("HJ", "fold"), ("BB", "call")))
    assert heads_up_line(line(("CO", "raise"), ("BTN", "raise"), ("CO", "raise"), ("BTN", "call")))
    assert not heads_up_line(line(("SB", "call"), ("BB", "check")))  # SB limp
    assert not heads_up_line(line(("LJ", "raise"), ("BTN", "call"), ("BB", "call")))  # cold call into 3
    assert not heads_up_line(line(("LJ", "raise"), ("BTN", "call"), ("SB", "raise")))  # squeeze
