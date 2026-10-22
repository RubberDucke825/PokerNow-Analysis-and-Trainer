"""Tiny contract-shaped strategy document (contracts/strategy_output.md) for a spot config,
written independently of pn.train.engine so trainer tests cross-check the tree walker.

One street: nobody has bet -> [check, bet50]; facing the bet -> [fold, call]; the street ends
in a `leaf` (flop/turn) or `showdown` (river) terminal, a lone survivor in a `fold` terminal.
Every in-range combo plays action j with weight 3**j (two actions: 0.25 / 0.75), so the last
action is "best" and the first "mixed" (pn.train.grading). TexasSolver-shaped: no EVs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pn import ranges

COMBOS = ranges.all_combos()


def fake_doc(spot: Mapping[str, Any]) -> dict:
    g, players = spot["game"], spot["players"]
    n = len(players)
    dense = [ranges.to_dense(ranges.parse(p["range"])) for p in players]
    nodes: dict[str, dict] = {}

    def add(node: dict) -> str:
        nid = f"n{len(nodes)}"
        nodes[nid] = node
        return nid

    def end(pot: int, stacks: list[int], alive: list[int]) -> dict:
        if len(alive) == 1:
            return {"type": "terminal", "kind": "fold", "pot": pot, "payoffs_by_player": None}
        kind = "showdown" if g["street"] == "river" else "leaf"
        return {"type": "terminal", "kind": kind, "pot": pot, "street": g["street"],
                "stacks": list(stacks), "reach_by_player": dense, "payoffs_by_player": None}

    def action(p: int, pot: int, stacks: list[int], history: list[str],
               acts: list[tuple[str, int | None, str]]) -> dict:  # fmt: skip
        k = len(acts)
        in_range = [w > 0 for w in dense[p]]
        row = [3.0**j / sum(3.0**i for i in range(k)) for j in range(k)]
        return {
            "type": "action", "street": g["street"], "player": p, "pot": pot,
            "stacks": list(stacks), "history": history,
            "actions": [{"token": t, **({"amount": a} if a is not None else {}), "child": c}
                        for t, a, c in acts],
            "strategy": [list(row) if r else [0.0] * k for r in in_range],
            "reach": dense[p], "reach_by_player": dense,
            "summary": {t: f for (t, _, _), f in zip(acts, row, strict=True)},
        }  # fmt: skip

    def facing(order: list[int], pot: int, stacks: list[int], alive: list[int],
               bet: int, history: list[str]) -> str:  # fmt: skip
        """Players in `order` respond to a bet of `bet` (street commitment)."""
        if not order:
            return add(end(pot, stacks, alive))
        p, rest = order[0], order[1:]
        put = min(bet, stacks[p])
        fold = facing(rest, pot, stacks, [q for q in alive if q != p], bet, [*history, "fold"])
        st2 = list(stacks)
        st2[p] -= put
        call = facing(rest, pot + put, st2, alive, bet, [*history, "call"])
        return add(action(p, pot, stacks, history, [("fold", None, fold), ("call", None, call)]))

    def first(i: int, pot: int, stacks: list[int], history: list[str]) -> str:
        if i == n:
            return add(end(pot, stacks, list(range(n))))
        check = first(i + 1, pot, stacks, [*history, "check"])
        bet = min(pot // 2, stacks[i])
        st2 = list(stacks)
        st2[i] -= bet
        others = [q for q in [*range(i + 1, n), *range(i)]]
        child = facing(others, pot + bet, st2, list(range(n)), bet, [*history, "bet50"])
        return add(action(i, pot, stacks, history, [("check", None, check), ("bet50", bet, child)]))

    root = first(0, int(g["pot"]), [int(p["stack"]) for p in players], [])
    return {"meta": {"iterations": 1, "mode": g.get("mode", "depth_limited"), "board": g["board"],
                     "ev_available": False},
            "players": [{"index": k, "name": p["name"], "position": p["position"],
                         "hero": bool(p.get("hero")), "locked": False} for k, p in enumerate(players)],
            "combos": list(COMBOS), "root": root, "nodes": nodes}  # fmt: skip


def fake_runner(spot: Mapping[str, Any], backend: str | None = None) -> tuple[dict, str]:
    """SolveCache runner stand-in."""
    return fake_doc(spot), backend or "texas"
