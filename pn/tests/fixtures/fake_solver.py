"""Stand-in for the mwsolver / hu / dcfr-pn binaries in pn.solve tests.

Usage: python fake_solver.py <name> (solve <cfg> --out <file> | tree-size <cfg>)
Env: FAKE_<NAME>_MODE = ok | locks | fail; FAKE_EST_BYTES = tree-size estimate;
FAKE_LOG = file that gets the backend name per solve; FAKE_ARGS_LOG = name + solve args.
FAKE_EV=1: emit a one-node tree (first actor: check / bet33, action_ev [10, 25] for every combo,
reach_by_player 1.0) instead of the empty document. As `dcfr`, bet33 leads to an action node
"n2" for player 1 (fold / call) whose own `reach` is 0 for even combo indices while action_ev
stays [-5, 40] for every combo (counterfactual off-path EVs).
FAKE_DOC=<path>: emit that JSON document (meta.stop "converged"). FAKE_SLEEP=<sec>: sleep before
writing; with [game] checkpoint_sec in the config, a copy with meta.stop "checkpoint" is
written first (a killed run leaves the checkpoint behind).
FAKE_BIG_NODES=<n>: a preflop-like chain of n action nodes (fold / call / raise300), each with
full per-combo strategy, ev, reach, equity and action_ev at 5 significant digits (size tests).
FAKE_TREE=1: emit fake_tree.fake_doc(config): one street of check/bet50, fold/call, ending in
leaf terminals (trainer and Strategy-tab tests).
"""

import json
import os
import sys

name, sub = sys.argv[1], sys.argv[2]
if sub == "tree-size":
    est = int(os.environ.get("FAKE_EST_BYTES", "1000"))
    print(json.dumps({"node_count": 7, "est_bytes": est, "fits": est <= 5_000_000_000}))
    sys.exit(0)
mode = os.environ.get(f"FAKE_{name.upper()}_MODE", "ok")
if mode == "locks":
    print(json.dumps({"error": "locks_unsupported: hu cannot node-lock", "code": 2}))
    sys.exit(2)
if mode == "fail":
    print(json.dumps({"error": "boom", "code": 1}))
    sys.exit(1)
if os.environ.get("FAKE_ARGS_LOG"):
    with open(os.environ["FAKE_ARGS_LOG"], "a") as f:
        f.write(" ".join([name, *sys.argv[3:]]) + "\n")
if os.environ.get("FAKE_LOG"):
    with open(os.environ["FAKE_LOG"], "a") as f:
        f.write(f"{name}\n")
out = sys.argv[sys.argv.index("--out") + 1]
meta = {"iterations": 1}
if name in ("hu", "dcfr"):
    meta["backend"] = name  # mwsolver fake leaves it out: SolveCache fills it in
doc = {"meta": meta, "players": [], "combos": [], "root": "n0", "nodes": {}}
if os.environ.get("FAKE_EV") == "1":
    import tomllib

    from pn import ranges

    with open(sys.argv[3]) as cfg:
        n_players = len(tomllib.loads(cfg.read())["players"])
    combos = list(ranges.all_combos())
    doc["combos"] = combos
    doc["nodes"] = {
        "n0": {"type": "action", "player": 0, "street": "flop", "history": [],
               "actions": [{"token": "check", "child": "n1"},
                           {"token": "bet33", "amount": 100, "child": "n2"}],
               "action_ev": [[10.0, 25.0] for _ in combos],
               "reach_by_player": [[1.0] * len(combos) for _ in range(n_players)]},
        "n1": {"type": "terminal", "kind": "leaf", "pot": 0},
        "n2": {"type": "terminal", "kind": "leaf", "pot": 0},
    }
    if name == "dcfr":
        doc["nodes"]["n2"] = {
            "type": "action", "player": 1, "street": "flop", "history": ["bet33"],
            "actions": [{"token": "fold", "child": "n3"}, {"token": "call", "child": "n4"}],
            "action_ev": [[-5.0, 40.0] for _ in combos],
            "reach": [0.0 if k % 2 == 0 else 1.0 for k in range(len(combos))]}  # fmt: skip
        doc["nodes"]["n3"] = {"type": "terminal", "kind": "fold", "pot": 0}
        doc["nodes"]["n4"] = {"type": "terminal", "kind": "leaf", "pot": 0}
if os.environ.get("FAKE_TREE") == "1":
    import tomllib

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fake_tree import fake_doc

    with open(sys.argv[3], "rb") as cfg:
        doc = {**fake_doc(tomllib.load(cfg)), "meta": meta}
if os.environ.get("FAKE_DOC"):
    with open(os.environ["FAKE_DOC"]) as f:
        doc = {**json.load(f), "meta": {**meta, "stop": "converged", "exploitability_pct_pot": 0.4}}
if os.environ.get("FAKE_BIG_NODES"):
    import random

    from pn import ranges

    rnd = random.Random(7)
    n_nodes, combos = int(os.environ["FAKE_BIG_NODES"]), list(ranges.all_combos())

    def num() -> float:
        return float(f"{rnd.random():.5g}")

    nodes = {}
    for i in range(n_nodes):
        last = i == n_nodes - 1
        nodes[f"n{i}"] = {
            "type": "action", "street": "preflop", "player": i % 6, "pot": 150,
            "actions": [{"token": "fold", "child": f"t{i}" if last else f"n{i + 1}"},
                        {"token": "call", "child": f"t{i}"},
                        {"token": "raise300", "amount": 300, "child": f"t{i}"}],
            "strategy": [[num(), num(), num()] for _ in combos],
            "action_ev": [[num(), num(), num()] for _ in combos],
            "ev": [num() for _ in combos], "reach": [num() for _ in combos],
            "equity": [num() for _ in combos], "summary": {"fold": 0.8, "call": 0.0, "raise300": 0.2},
        }  # fmt: skip
        nodes[f"t{i}"] = {"type": "terminal", "kind": "leaf", "pot": 150}
    doc = {"meta": meta, "players": [{"index": k, "name": f"p{k}", "position": p}
                                     for k, p in enumerate(("LJ", "HJ", "CO", "BTN", "SB", "BB"))],
           "combos": combos, "root": "n0", "nodes": nodes}  # fmt: skip
if os.environ.get("FAKE_SLEEP"):
    import time
    import tomllib

    with open(sys.argv[3], "rb") as cfg:
        if tomllib.load(cfg)["game"].get("checkpoint_sec"):
            with open(out, "w") as f:
                json.dump({**doc, "meta": {**doc["meta"], "stop": "checkpoint"}}, f)
    time.sleep(float(os.environ["FAKE_SLEEP"]))
with open(out, "w") as f:
    json.dump(doc, f)
