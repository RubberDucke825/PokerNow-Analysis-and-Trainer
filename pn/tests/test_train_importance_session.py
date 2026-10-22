"""Plan 14 in trainer sessions: importance parameters, texture pins and texture-drawn drill flops,
board / pot / formation stored on decision rows (never for replays)."""

from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

import pytest

from pn.solve import SolveCache
from pn.train import Config, Trainer, TrainError, drills
from pn.train import importance as imp
from pn.train import textures as tx

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import fake_preflop
import flop_seed
from fake_tree import fake_runner


@pytest.fixture(autouse=True)
def _flop_library(flop_template: Path, tmp_path: Path) -> None:
    """Solved boards for every matchup: flop_seed.BOARDS (King, Ace, Queen, Low; no Paired)."""
    flop_seed.copy_into(flop_template, tmp_path / "cache")


def _trainer(tmp_path: Path, db: Path | None = None) -> Trainer:
    from pn.solve.lazydoc import LazyDoc
    from pn.train.leaks import Leaks

    lib = LazyDoc.open(fake_preflop.write(tmp_path / "lib.json"))
    return Trainer(SolveCache(tmp_path / "cache", runner=fake_runner), background=False, db=db,
                   leaks=Leaks(db, library=lambda table, depth: lib))  # fmt: skip


def _token(v: dict, kind: str | None) -> str:
    toks = [o["token"] for o in v["legal_actions"]]
    want = {"raise": ("raise", "allin"), "allin": ("allin",), "call": ("call", "check"),
            "check": ("check", "call"), "fold": ("fold", "check")}.get(kind, ("check", "call"))  # fmt: skip
    return next((t for t in want if t in toks), toks[0])


def _to_flop(t: Trainer, v: dict) -> dict:
    """Hero follows the drill's preflop line (passive without one) until the flop or hand end."""
    while v["result"] is None and v["street"] == "preflop" and v["legal_actions"]:
        h = t.get(v["sid"]).hand
        kind = None
        if h.drill is not None:
            try:
                kind = drills.preflop_step(h.drill, h.state, h.state.to_act)
            except LookupError:
                kind = None
        v = t.act(v["sid"], _token(v, kind))
    return v


def _finish(t: Trainer, v: dict) -> dict:
    for _ in range(40):
        if v["result"] is not None:
            return v
        toks = [o["token"] for o in v["legal_actions"]]
        v = t.act(v["sid"], next(x for x in ("check", "call", "fold") if x in toks))
    raise AssertionError("hand did not finish")


def test_config_params_validation_and_defaults() -> None:
    c = Config()
    assert (c.p_form, c.p_tex, c.pot_mode, c.drill_texture) == (3, 1, "start", None)
    for bad in ({"p_form": 0}, {"p_form": 6}, {"p_tex": 0}, {"p_tex": 6}, {"pot_mode": "mean"},
                {"drill_texture": "Wet"}, {"p_form": 2.5}):  # fmt: skip
        with pytest.raises(TrainError):
            Config(**bad)
    assert Config(p_form=5, p_tex=5, pot_mode="avg", drill_texture="Monotone").p_form == 5


def test_params_change_the_running_session(tmp_path: Path) -> None:
    t = _trainer(tmp_path)
    v = t.new(Config(seed=1, drill_share=0.0))
    assert v["params"] == {"p_form": 3, "p_tex": 1, "pot_mode": "start"}
    p = t.params(v["sid"], p_tex=4)
    assert (
        p["params"] == {"p_form": 3, "p_tex": 4, "pot_mode": "start"}
        and p["hand_no"] == v["hand_no"]
    )
    assert t.params(v["sid"], p_form=1, pot_mode="avg")["params"] == {
        "p_form": 1,
        "p_tex": 4,
        "pot_mode": "avg",
    }
    with pytest.raises(TrainError):
        t.params(v["sid"], p_form=9)
    assert t.get(v["sid"]).cfg.p_form == 1  # a refused change leaves the session as it was


def test_drill_weights_follow_the_session_params(tmp_path: Path, monkeypatch) -> None:
    t = _trainer(tmp_path)
    seen: list[dict] = []
    real = t.leaks.importance

    def spy(*a, **kw):
        seen.append(kw)
        return real(*a, **kw)

    monkeypatch.setattr(t.leaks, "importance", spy)
    t.new(Config(seed=2, drill_share=1.0, p_form=5, p_tex=2, pot_mode="avg"))
    assert seen and all((k["p_form"], k["p_tex"], k["pot"]) == (5, 2, "avg") for k in seen)


def test_texture_pin_deals_postflop_drills_on_flops_of_that_texture(tmp_path: Path) -> None:
    t = _trainer(tmp_path)
    v = t.new(Config(seed=7, drill_share=0.0, drill_texture="Queen"))
    flops = 0
    for _ in range(8):
        h = t.get(v["sid"]).hand
        assert (
            h.drill is not None and h.drill_spot.startswith("post|") and h.drill.texture == "Queen"
        )
        assert v["pinned_drill"] == {
            "spot": None,
            "label": "Queen flops",
            "dealt": True,
            "texture": "Queen",
            "formation": None,
        }
        v = _to_flop(t, v)
        if len(v["board"]) >= 3 and t.get(v["sid"]).hand.drill is not None:
            assert tx.texture(v["board"]) == "Queen"
            flops += 1
        v = t.next(_finish(t, v)["sid"])
    assert flops >= 4  # most drills stay on their line to the flop


def test_texture_pin_with_a_spot_and_the_top_spot_without_one(tmp_path: Path) -> None:
    t = _trainer(tmp_path)
    rows = t.leaks.rows(None, 6, 100, 1000)
    post = [
        r["spot"]
        for r in rows
        if r["spot"].startswith("post|") and t._drill_line(Config(), r["spot"])
    ]
    v = t.new(Config(seed=3, drill_share=0.0))
    t.drill(v["sid"], None, "Low")
    dealt = set()
    for _ in range(12):  # texture alone: postflop spots drawn by weight, not only the top one
        v = t.next(_finish(t, t.state(v["sid"]))["sid"])
        dealt.add(t.get(v["sid"]).hand.drill_spot)
    assert dealt <= set(post) and len(dealt) >= 2
    t.drill(v["sid"], post[-1], "Ace")
    v = t.next(_finish(t, t.state(v["sid"]))["sid"])
    h = t.get(v["sid"]).hand
    assert h.drill_spot == post[-1] and h.drill_texture == "Ace"
    assert v["pinned_drill"]["spot"] == post[-1] and v["pinned_drill"]["texture"] == "Ace"
    pre = next(r["spot"] for r in rows if r["spot"].startswith("pf|"))
    with pytest.raises(TrainError):
        t.drill(v["sid"], pre, "Ace")  # a texture only shapes postflop drills
    with pytest.raises(TrainError):
        t.drill(v["sid"], None, "Wet")
    assert t.state(v["sid"])["pinned_drill"]["texture"] == "Ace"  # refused pins change nothing


def test_texture_without_a_solved_board_falls_back_with_a_note(tmp_path: Path, caplog) -> None:
    t = _trainer(tmp_path)
    with caplog.at_level(logging.WARNING, logger="pn.train.session"):
        v = t.new(Config(seed=4, drill_share=0.0, drill_texture="Paired"))  # no paired board solved
    h = t.get(v["sid"]).hand
    assert h.drill is not None and h.drill_spot.startswith("post|") and h.drill.texture is None
    assert "no solved Paired flop" in caplog.text
    assert v["pinned_drill"]["dealt"] is False and v["pinned_drill"]["texture"] == "Paired"
    v = _to_flop(t, v)
    if len(v["board"]) >= 3:
        assert tx.texture(v["board"]) != "Paired"  # any solved board instead


def test_a_pinned_hand_that_leaves_the_line_says_so(tmp_path: Path) -> None:
    """Hero always passive preflop: hands that leave the drill line play on naturally on any
    flop; the state tells them apart (`drill` null, `pinned_drill.dealt` still true)."""
    t = _trainer(tmp_path)
    v = t.new(Config(seed=9, drill_share=0.0, drill_texture="Queen"))
    left = 0
    for _ in range(10):
        assert v["pinned_drill"]["dealt"] is True and v["drill"] is not None
        while v["result"] is None and v["street"] == "preflop" and v["legal_actions"]:
            v = t.act(v["sid"], _token(v, "call"))
        if len(v["board"]) >= 3 and tx.texture(v["board"]) != "Queen":
            assert v["drill"] is None and v["pinned_drill"]["dealt"] is True
            left += 1
        v = t.next(_finish(t, v)["sid"])
    assert left


def test_unpinned_postflop_drills_draw_textures_with_solved_boards(tmp_path: Path) -> None:
    t = _trainer(tmp_path)
    v = t.new(Config(seed=11, drill_share=1.0))
    got = set()
    for _ in range(12):
        h = t.get(v["sid"]).hand
        if h.drill is not None and h.drill_spot.startswith("post|"):
            assert h.drill.texture in {tx.texture(b) for b in flop_seed.BOARDS}
            got.add(h.drill.texture)
        v = t.next(_finish(t, v)["sid"])
    assert got


def test_decisions_store_board_pot_and_formation_and_replays_none(tmp_path: Path) -> None:
    db = tmp_path / "pn.sqlite"
    t = _trainer(tmp_path, db)
    v = t.new(Config(seed=7, drill_share=0.0, drill_texture="King"))
    for _ in range(10):
        v = _to_flop(t, v)
        if v["result"] is None and len(v["board"]) >= 3 and v["legal_actions"]:
            break
        v = t.next(_finish(t, v)["sid"])
    else:
        raise AssertionError("no postflop decision")
    h = t.get(v["sid"]).hand
    want_form = imp.state_formation(h.state)
    v = t.act(v["sid"], _token(v, "check"))
    r = t.retry(v["sid"])  # a replay: the redo is never recorded
    r = _finish(t, t.act(r["sid"], _token(r, "check")))
    with sqlite3.connect(db) as c:
        rows = c.execute("SELECT street, board, pot_bb, formation FROM train_decisions WHERE hand_uid = ? "
                         "ORDER BY seq", (f"{v['sid']}-{v['hand_no']}",)).fetchall()  # fmt: skip
    pre = [x for x in rows if x[0] == "preflop"]
    post = [x for x in rows if x[0] != "preflop"]
    assert pre and all(b is None and f is None and p >= 1.5 for _, b, p, f in pre)
    assert len(post) == 1  # the original flop decision only
    street, board, pot_bb, form = post[0]
    assert street == "flop" and tx.texture(board) == "King" and len(board) == 6
    assert pot_bb == h.state.pot / 100 and form is not None and form == want_form


# ------------------------------------------------------------------ formation pins


def _pinnable_formations(t: Trainer) -> list[str]:
    forms = t.leaks.importance(None, 6, 100, 1000)["formations"]
    labels = [f["formation"] for f in forms]
    return [x for x in labels if t._formation_drill(Config(drill_formation=x))]


def test_config_formation_validation() -> None:
    assert Config(drill_formation="CO vs BB - SRP", drill_texture="Ace").drill_formation
    for bad in ({"drill_formation": "CO BB SRP"}, {"drill_formation": "CO vs BB - 5BP"},
                {"drill_formation": "CO vs BB - SRP", "drill_spot": "pf|BB|vs_open|CO"}):  # fmt: skip
        with pytest.raises(TrainError):
            Config(**bad)


def test_formation_pin_deals_postflop_drills_in_that_formation(tmp_path: Path) -> None:
    t = _trainer(tmp_path)
    labels = _pinnable_formations(t)
    assert len(labels) >= 3  # more than the top line of each pot type
    v = t.new(Config(seed=5, drill_share=0.0))
    for label in labels:
        t.drill(v["sid"], None, formation=label)
        v = t.next(_finish(t, t.state(v["sid"]))["sid"])
        h = t.get(v["sid"]).hand
        assert h.drill is not None and h.drill_formation == label
        assert h.drill_spot.startswith("post|")
        assert v["pinned_drill"] == {"spot": None, "label": label, "dealt": True, "texture": None,
                                     "formation": label}  # fmt: skip
        v = _to_flop(t, v)
        h = t.get(v["sid"]).hand
        if len(v["board"]) >= 3 and h.drill is not None:
            assert imp.state_formation(h.state) == label


def test_formation_pin_with_a_texture_and_refusals(tmp_path: Path) -> None:
    t = _trainer(tmp_path)
    label = _pinnable_formations(t)[-1]
    v = t.new(Config(seed=6, drill_share=0.0, drill_formation=label, drill_texture="Queen"))
    h = t.get(v["sid"]).hand
    assert h.drill_formation == label and h.drill.texture == "Queen"
    assert v["pinned_drill"]["formation"] == label and v["pinned_drill"]["texture"] == "Queen"
    with pytest.raises(TrainError):
        t.drill(v["sid"], None, formation="UTG vs HJ - Limped")  # not reached by the preflop map
    with pytest.raises(TrainError):
        t.drill(v["sid"], "pf|BB|vs_open|CO", formation=label)
    assert t.state(v["sid"])["pinned_drill"]["formation"] == label  # refused pins change nothing
