"""A small hand-built 6-max preflop root in the strategy-document format (contracts/
strategy_output.md), generated with pn.train.engine so its action order matches the trainer.

Actions: unopened -> fold / raise; facing one raise -> fold / raise (3-bet), plus call for BTN
and BB; facing two -> fold / call. Pure strategies by preflop strength percentile (pn.train.preflop.class_percentile):
open < OPEN, 3-bet < THREE_BET, call an open < CALL, call a 3-bet < CALL_3BET. The street ends
in `leaf` terminals carrying reach_by_player. `write(path)` saves it for LazyDoc.open.
"""

from __future__ import annotations

import json
from pathlib import Path

from pn import ranges
from pn.train.engine import apply, hand_decided, round_closed, start
from pn.train.preflop import class_percentile

ORDER = ("LJ", "HJ", "CO", "BTN", "SB", "BB")
OPEN, THREE_BET, CALL, CALL_3BET = 0.25, 0.05, 0.30, 0.12
FLATTERS = ("BTN", "BB")  # the only seats that may flat an open (keeps the tree small)
COMBOS = list(ranges.all_combos())
PCT = [class_percentile(ranges.combo_class(c)) for c in COMBOS]


def _row(raises: int, kinds: list[str], pct: float) -> list[float]:
    if raises == 0:
        want = "raise" if pct < OPEN else "fold"
    elif raises == 1:
        want = "raise" if pct < THREE_BET else "call" if pct < CALL else "fold"
    else:
        want = "call" if pct < CALL_3BET else "fold"
    if want not in kinds:
        want = "call" if "call" in kinds else "fold"
    return [1.0 if k == want else 0.0 for k in kinds]


def build() -> dict:
    nodes: dict[str, dict] = {}
    counter = [0]

    def nid() -> str:
        counter[0] += 1
        return f"n{counter[0] - 1}"

    def walk(st, reach: list[list[float]], history: list[str]) -> str:
        me = nid()
        if hand_decided(st) or round_closed(st):
            live = [i for i in range(6) if not st.folded[i]]
            kind = "leaf" if len(live) > 1 else "fold"
            nodes[me] = {"type": "terminal", "kind": kind, "pot": st.pot, "street": "preflop",
                         "stacks": list(st.stacks),
                         "reach_by_player": [reach[i] if not st.folded[i] else None
                                             for i in range(6)],
                         "payoffs_by_player": None}  # fmt: skip
            return me
        i = st.to_act
        raises = st.bets - 1
        kinds = ["fold", "call", "raise"] if raises else ["fold", "raise"]
        if raises == 1 and ORDER[i] not in FLATTERS:
            kinds = ["fold", "raise"]
        if raises >= 2 or not st.can_raise(i):
            kinds = ["fold", "call"]
        if st.to_call(i) == 0:
            kinds = ["check" if k == "call" else k for k in kinds if k != "fold"]
        strat = [_row(raises, kinds, p) for p in PCT]
        mine = reach[i]
        actions = []
        node = {"type": "action", "street": "preflop", "player": i, "pot": st.pot,
                "history": list(history), "actions": actions, "strategy": strat,
                "reach": mine}  # fmt: skip
        nodes[me] = node
        tot = sum(mine) or 1.0
        summary = {}
        for k, kind in enumerate(kinds):
            if kind == "raise":
                to = 250 if raises == 0 else st.highest * 3
                nxt, token = apply(st, "raise", to), f"raise{to}"
            else:
                nxt, token = apply(st, kind), kind
            share = [m * row[k] for m, row in zip(mine, strat, strict=True)]
            summary[token] = sum(share) / tot
            new_reach = [share if j == i else r for j, r in enumerate(reach)]
            entry = {"token": token, "child": None}
            if kind == "raise":
                entry["amount"] = to
            actions.append(entry)
            entry["child"] = walk(nxt, new_reach, [*history, token])
        node["summary"] = summary  # last key: LazyDoc partial reads find it in the tail
        return me

    st = start(ORDER, 10_000, 50, 100)
    root = walk(st, [[1.0] * len(COMBOS) for _ in range(6)], [])
    return {"meta": {"backend": "fake", "mode": "depth_limited"},
            "players": [{"index": k, "name": p.lower(), "position": p, "hero": p == "BTN",
                         "locked": False} for k, p in enumerate(ORDER)],
            "combos": COMBOS, "root": root, "nodes": nodes}  # fmt: skip


def write(path: Path) -> Path:
    path.write_text(json.dumps(build()))
    return path
