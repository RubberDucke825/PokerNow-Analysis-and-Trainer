"""TexasSolver backend (contracts/texas_backend.md): command file, dump -> document, process
control, and one real solve when console_solver is built."""

from __future__ import annotations

import sys
import threading
import tomllib
from pathlib import Path

import pytest

from pn import ranges
from pn.solve import texas
from pn.solve.backend import (
    MULTIWAY_UNSOLVABLE,
    PREFLOP_UNSOLVABLE,
    Preempted,
    SolveError,
    choose_backend,
    run_backend,
)
from pn.solve.texas_doc import Sizes, to_document
from pn.web.solves import validate

FIXTURES = Path(__file__).parent / "fixtures"
REAL_CMD = texas.texas_cmd()  # before conftest's autouse fixture hides it
EV_KEYS = ("ev", "action_ev", "equity")


def flop_spot() -> dict:
    return tomllib.loads((FIXTURES / "texas_flop.toml").read_text())


def _lines(text: str) -> list[str]:
    return text.strip().split("\n")


# ------------------------------------------------------------------ backend selection


def test_choose_backend_refuses_everything_but_heads_up_postflop() -> None:
    spot = flop_spot()
    assert choose_backend(spot) == "texas"
    with pytest.raises(SolveError, match=PREFLOP_UNSOLVABLE):
        choose_backend({**spot, "game": {**spot["game"], "street": "preflop", "board": []}})
    three = {**spot, "players": [*spot["players"], {**spot["players"][1], "name": "x"}]}
    with pytest.raises(SolveError, match=MULTIWAY_UNSOLVABLE):
        choose_backend(three)


# ------------------------------------------------------------------ command file


def test_command_text_maps_the_spot() -> None:
    text, (oop_w, ip_w), sizes = texas.command_text(flop_spot())
    lines = _lines(text)
    assert lines[:3] == ["set_pot 1500", "set_effective_stack 9800", "set_board Kd,Ah,5h"]
    assert "set_bet_sizes oop,flop,bet,33,100" in lines
    assert "set_bet_sizes ip,flop,raise,100" in lines
    assert "set_bet_sizes oop,turn,donk,75" in lines
    assert "set_bet_sizes ip,river,bet,150" in lines
    assert "set_bet_sizes ip,river,allin" in lines
    assert not any("ip,turn,donk" in x for x in lines)  # only OOP donks
    assert lines.index("set_raise_limit 3") < lines.index("build_tree")
    assert "set_allin_threshold 0.67" in lines
    assert ["set_max_iteration 300", "set_print_interval 10"] == lines[-6:-4]
    assert lines[-3:] == ["start_solve", "set_dump_rounds 1", "dump_result dump.json"]
    assert sizes == Sizes(bets={"flop": (33, 100), "turn": (75,), "river": (150,)}, raises=(100,))
    # board-blocked combos carry no weight; KhQh keeps its own 0.3 as an exact combo
    assert oop_w[ranges.combo_str_index("AsAh")] == 0
    assert oop_w[ranges.combo_str_index("AsAd")] == 1
    assert ip_w[ranges.combo_str_index("KhQh")] == pytest.approx(0.3)
    ip_range = next(x for x in lines if x.startswith("set_range_ip ")).split(" ", 1)[1]
    parts = ip_range.split(",")
    assert "KhQh:0.3" in parts and "KsQs" in parts and "KQs" not in parts
    assert "JTo:0.5" in parts and "AA" in parts and " " not in ip_range


def test_texas_range_scales_drops_and_groups_classes() -> None:
    text, used = texas.texas_range("AA:0.5,KK:0.25,QQ:0.001,AhKh:0.5", ["Ad", "2c", "3c"])
    parts = text.split(",")
    assert "AA" in parts  # 0.5 scaled to 1 over the 3 live AA combos
    assert "KK:0.5" in parts
    assert not any(p.startswith("QQ") for p in parts)  # 0.002 after scaling: dropped
    assert "AhKh" in parts  # exact combo, scaled to 1
    assert used[ranges.combo_str_index("AsAh")] == 0.5  # original weights, not scaled
    assert used[ranges.combo_str_index("AdAc")] == 0  # board
    assert used[ranges.combo_str_index("QsQh")] == 0  # dropped like TexasSolver does


def test_donk_false_blocks_the_oop_flop_lead_after_an_ip_raise() -> None:
    spot = flop_spot()
    spot["sizes"]["donk"] = False
    lines = _lines(texas.command_text(spot)[0])
    assert not any(x.startswith("set_bet_sizes oop,flop,bet") for x in lines)
    assert "set_bet_sizes ip,flop,bet,33,100" in lines
    assert not any(",donk," in x for x in lines)


def test_turn_spot_emits_turn_and_river_sizes_only() -> None:
    spot = flop_spot()
    spot["game"] = {**spot["game"], "street": "turn", "board": ["Kd", "Ah", "5h", "7c"]}
    lines = _lines(texas.command_text(spot, streets=2)[0])
    assert not any(",flop," in x for x in lines)
    assert "set_dump_rounds 2" in lines


# ------------------------------------------------------------------ dump -> document


def tiny_spot() -> dict:
    return {
        "game": {"street": "flop", "board": ["Kd", "Ah", "5h"], "pot": 1500},
        "players": [{"name": "hero", "position": "BB", "stack": 9800, "hero": True, "range": "AA"},
                    {"name": "v", "position": "BTN", "stack": 9800, "range": "KQs:0.5"}],
        "sizes": {"flop_bets": [33], "turn_bets": [75], "raise": [100]},
    }


def tiny_dump() -> dict:
    """Root OOP (TexasSolver player 1): check / bet 33% / all-in. Check -> chance node past the
    dump depth; bet -> IP call / raise 100% / fold; call -> chance node dealing Qs (and the
    board's Kd, which must be ignored); all-in -> IP call (all-in runout) / fold."""
    turn = {"actions": ["CHECK"], "player": 1, "node_type": "action_node",
            "strategy": {"actions": ["CHECK"], "strategy": {"AsAd": [1.0], "AdAc": [1.0]}}}
    return {
        "actions": ["CHECK", "BET 495.000000", "BET 9800.000000"], "player": 1,
        "node_type": "action_node",
        "strategy": {"actions": ["CHECK", "BET 495.000000", "BET 9800.000000"],
                     "strategy": {"AsAd": [0.0, 1.0, 0.0], "AsAc": [1.0, 0.0, 0.0],
                                  "AdAc": [0.5, 0.25, 0.25]}},
        "childrens": {
            "CHECK": {"deal_number": 0, "node_type": "chance_node"},
            "BET 495.000000": {
                "actions": ["CALL", "RAISE 2985.000000", "FOLD"], "player": 0,
                "node_type": "action_node",
                "strategy": {"actions": ["CALL", "RAISE 2985.000000", "FOLD"],
                             "strategy": {"KsQs": [1.0, 0.0, 0.0], "KhQh": [0.0, 1.0, 0.0],
                                          "KcQc": [0.5, 0.0, 0.5]}},
                "childrens": {
                    "CALL": {"deal_number": 2, "node_type": "chance_node",
                             "dealcards": {"Qs": turn, "Kd": turn}},
                    "RAISE 2985.000000": {
                        "actions": ["CALL", "FOLD"], "player": 1, "node_type": "action_node",
                        "strategy": {"actions": ["CALL", "FOLD"],
                                     "strategy": {"AsAd": [1.0, 0.0], "AdAc": [0.0, 1.0]}},
                        "childrens": {"CALL": {"deal_number": 0, "node_type": "chance_node"}}},
                }},
            "BET 9800.000000": {
                "actions": ["CALL", "FOLD"], "player": 0, "node_type": "action_node",
                "strategy": {"actions": ["CALL", "FOLD"],
                             "strategy": {"KsQs": [1.0, 0.0], "KhQh": [0.0, 1.0],
                                          "KcQc": [0.0, 1.0]}},
                "childrens": {"CALL": {"deal_number": 0, "node_type": "chance_node"}}},
        },
    }  # fmt: skip


def _doc(streets: int) -> dict:
    spot = tiny_spot()
    _, reach, sizes = texas.command_text(spot)
    return to_document(tiny_dump(), spot, sizes, reach, streets, {"iterations": 7})


def _tokens(node: dict) -> list[str]:
    return [a["token"] for a in node["actions"]]


def _at(doc: dict, *path: str) -> dict:
    node = doc["nodes"][doc["root"]]
    for step in path:
        child = next((a["child"] for a in node.get("actions", []) if a["token"] == step), None)
        node = doc["nodes"][child if child else node["children"][step]]
    return node


def test_dump_to_document_ids_tokens_and_amounts() -> None:
    doc = _doc(streets=1)
    validate(doc)
    assert doc["root"] == "n0" and all(k.startswith("n") for k in doc["nodes"])
    assert doc["meta"]["backend"] == "texas" and doc["meta"]["ev_available"] is False
    assert doc["meta"]["iterations"] == 7 and doc["meta"]["emitted_streets"] == 1
    root = doc["nodes"]["n0"]
    assert root["player"] == 0 and root["street"] == "flop"  # TexasSolver player 1 = OOP
    assert _tokens(root) == ["check", "bet33", "allin"]
    assert [a.get("amount") for a in root["actions"]] == [None, 495, 9800]
    assert root["actions"][0]["child"] == "n1"  # pre-order ids
    facing = _at(doc, "bet33")
    assert facing["player"] == 1 and _tokens(facing) == ["call", "raise100", "fold"]
    assert facing["actions"][1]["amount"] == 2985  # raise-to: 2490 (100% after call) + 495
    assert facing["pot"] == 1995 and facing["stacks"] == [9305, 9800]
    assert facing["history"] == ["bet33"]
    assert _at(doc, "check")["kind"] == "leaf" and _at(doc, "check")["street"] == "flop"
    assert _at(doc, "bet33", "fold")["kind"] == "fold"
    assert _at(doc, "allin", "call")["kind"] == "allin_runout"
    assert _at(doc, "allin", "call")["pot"] == 1500 + 2 * 9800
    leaf = _at(doc, "bet33", "call")
    assert leaf["kind"] == "leaf" and leaf["pot"] == 2490 and leaf["stacks"] == [9305, 9305]
    for node in doc["nodes"].values():
        assert not any(k in node for k in EV_KEYS)
        assert not any(k in node.get("summary", {}) for k in ("ev_bb", "equity", "eqr"))


def test_dump_to_document_strategy_rows_follow_combo_index() -> None:
    doc = _doc(streets=1)
    root = doc["nodes"]["n0"]
    assert doc["combos"] == list(ranges.all_combos())
    assert root["strategy"][ranges.combo_str_index("AsAd")] == [0.0, 1.0, 0.0]
    assert root["strategy"][ranges.combo_str_index("AdAc")] == [0.5, 0.25, 0.25]
    assert root["strategy"][ranges.combo_str_index("KsQs")] == [0.0, 0.0, 0.0]  # not OOP's
    assert sum(1 for row in root["strategy"] if any(row)) == 3  # AA minus the board's Ah


def test_dump_to_document_reach_and_summary() -> None:
    doc = _doc(streets=1)
    root = doc["nodes"]["n0"]
    i = ranges.combo_str_index
    assert root["reach"][i("AsAd")] == 1.0 and root["reach_by_player"][1][i("KsQs")] == 0.5
    assert root["summary"] == pytest.approx({"check": 0.5, "bet33": 1.25 / 3, "allin": 0.25 / 3},
                                            rel=1e-4)  # fmt: skip
    facing = _at(doc, "bet33")
    assert "reach_by_player" not in facing  # villain node
    assert facing["reach"][i("KsQs")] == 0.5
    assert facing["summary"] == pytest.approx({"call": 0.75 / 1.5, "raise100": 0.5 / 1.5,
                                               "fold": 0.25 / 1.5}, rel=1e-4)  # fmt: skip
    leaf = _at(doc, "bet33", "call")
    oop, ip = leaf["reach_by_player"]
    assert (oop[i("AsAd")], oop[i("AdAc")], oop[i("AsAc")]) == (1.0, 0.25, 0.0)
    assert (ip[i("KsQs")], ip[i("KhQh")], ip[i("KcQc")]) == (0.5, 0.0, 0.25)


def test_dump_to_document_chance_node_with_two_streets() -> None:
    doc = _doc(streets=2)
    chance = _at(doc, "bet33", "call")
    assert chance["type"] == "chance" and chance["street"] == "turn"
    assert list(chance["children"]) == ["Qs"]  # the board's Kd is not a runout
    turn = doc["nodes"][chance["children"]["Qs"]]
    assert turn["street"] == "turn" and turn["pot"] == 2490 and turn["stacks"] == [9305, 9305]
    i = ranges.combo_str_index
    assert turn["reach_by_player"][1][i("KsQs")] == 0  # blocked by the turn card
    assert turn["reach_by_player"][1][i("KcQc")] == 0.25
    assert _at(doc, "check")["kind"] == "leaf"  # no dealcards in the dump: still a leaf


# ------------------------------------------------------------------ process control


def test_log_state_parses_progress_and_survives_a_bad_callback() -> None:
    seen: list[dict] = []

    def cb(d: dict) -> None:
        seen.append(d)
        raise RuntimeError("ui went away")

    st = texas.LogState(cb, 0.0)
    for line in ["EXEC FROM FILE", "Iter: 0", "player 0 exploitability -1.5",
                 "Total exploitability 50.877197 precent", "-------------------", "Iter: 11",
                 "Total exploitability 2.5 precent", "time used: 0.75",
                 "Warning: ... disabling suit isomorphism for this solve."]:  # fmt: skip
        st.feed(line + "\n")
    assert [(d["iterations"], d["exploitability_pct_pot"]) for d in seen] == [(0, 50.877197),
                                                                               (11, 2.5)]
    assert st.iterations == 11 and st.exploitability == 2.5 and st.solver_sec == 0.75
    assert st.isomorphism is False


SLEEPER = "import time\nwhile True:\n    print('Iter: 1', flush=True)\n    time.sleep(0.05)\n"


def test_run_texas_is_preempted_by_should_stop(tmp_path: Path) -> None:
    script = tmp_path / "fake_solver.py"
    script.write_text(SLEEPER)
    flag = threading.Event()
    threading.Timer(0.3, flag.set).start()
    with pytest.raises(Preempted):
        texas.run_texas(flop_spot(), cmd=[sys.executable, str(script)], should_stop=flag.is_set)


def test_run_texas_watchdog_and_missing_dump(tmp_path: Path) -> None:
    script = tmp_path / "fake_solver.py"
    script.write_text(SLEEPER)
    with pytest.raises(SolveError, match="timed out"):
        texas.run_texas(flop_spot(), cmd=[sys.executable, str(script)], timeout=0.5)
    quitter = tmp_path / "quit.py"
    quitter.write_text("print('boom')\n")
    with pytest.raises(SolveError, match="texas exit 0: boom"):
        texas.run_texas(flop_spot(), cmd=[sys.executable, str(quitter)])


def test_missing_binary_is_a_solve_error() -> None:
    with pytest.raises(SolveError, match="console_solver not found"):
        texas.run_texas(flop_spot())


# ------------------------------------------------------------------ real binary


@pytest.mark.skipif(REAL_CMD is None, reason="console_solver not built")
def test_real_river_solve_validates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(texas, "texas_cmd", lambda: REAL_CMD)
    spot = flop_spot()
    spot["game"] = {**spot["game"], "street": "river", "board": ["Kd", "Ah", "5h", "7c", "2s"],
                    "max_iterations": 20, "threads": 2}  # fmt: skip
    progress: list[dict] = []
    doc, ran = run_backend(spot, progress=progress.append)
    assert ran == "texas"
    validate(doc)
    meta = doc["meta"]
    assert meta["backend"] == "texas" and meta["ev_available"] is False
    assert meta["exploitability_pct_pot"] is not None and meta["iterations"] <= 20
    assert progress and progress[-1]["exploitability_pct_pot"] == meta["exploitability_pct_pot"]
    nodes = doc["nodes"]
    root = nodes[doc["root"]]
    assert root["player"] == 0 and _tokens(root)[0] == "check"
    assert set(_tokens(root)) <= {"check", "bet150", "allin"}
    for node in nodes.values():
        assert not any(k in node for k in EV_KEYS)
        for a in node.get("actions", []):
            assert a["child"] in nodes
        if node["type"] != "action":
            continue
        assert len(node["strategy"]) == ranges.N_COMBOS
        for w, row in zip(node["reach"], node["strategy"], strict=True):
            if w > 0:
                assert sum(row) == pytest.approx(1.0, abs=1e-3)
    # hero (OOP) range weights at the root, board removed
    i = ranges.combo_str_index
    assert root["reach"][i("AsAc")] == 1.0 and root["reach"][i("AhAs")] == 0
