"""Stand-in for TexasSolver's console_solver in pn tests: `fake_texas.py -i <cmdfile> -r <res>`.

Reads the command file, prints TexasSolver-style progress and writes a tiny dump for the spot:
OOP (player 1) checks or bets the first configured size, IP checks/bets or calls/folds, OOP
calls/folds facing a bet; every combo plays 50/50. Streets end in a chance node with no deal
(flop/turn) or a showdown (river). Env: FAKE_LOG (append "texas" per run), FAKE_ARGS_LOG
(argv), FAKE_TEXAS_MODE = fail (print "boom", exit 1) | sleep (never finish).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from pn import ranges

argv = sys.argv[1:]
if os.environ.get("FAKE_LOG"):
    with open(os.environ["FAKE_LOG"], "a") as f:
        f.write("texas\n")
if os.environ.get("FAKE_ARGS_LOG"):
    Path(os.environ["FAKE_ARGS_LOG"]).write_text(" ".join(argv))
mode = os.environ.get("FAKE_TEXAS_MODE")
if mode == "fail":
    print("boom", flush=True)
    sys.exit(1)
if mode == "sleep":
    while True:
        print("Iter: 1", flush=True)
        time.sleep(0.05)

cmds: dict[str, str] = {}
bets: dict[str, list[str]] = {}
for line in Path(argv[argv.index("-i") + 1]).read_text().splitlines():
    name, _, param = line.partition(" ")
    if name == "set_bet_sizes":
        seat, street, kind, *sizes = param.split(",")
        if kind == "bet" and sizes:
            bets[f"{seat},{street}"] = sizes
    else:
        cmds[name] = param

board = cmds["set_board"].split(",")
street = {3: "flop", 4: "turn", 5: "river"}[len(board)]
pot = float(cmds["set_pot"])
dead = {ranges.card_to_int(c) for c in board}


def combos(text: str) -> list[str]:
    return [c for c in ranges.parse(text)
            if ranges.card_to_int(c[:2]) not in dead and ranges.card_to_int(c[2:]) not in dead]


oop, ip = combos(cmds["set_range_oop"]), combos(cmds["set_range_ip"])


def node(player: int, actions: list[str], children: dict) -> dict:
    hands = oop if player == 1 else ip
    strat = {c: [1.0 / len(actions)] * len(actions) for c in hands}
    out = {"actions": actions, "player": player, "node_type": "action_node",
           "strategy": {"actions": actions, "strategy": strat}}
    kids = {k: v for k, v in children.items() if v is not None}
    if kids:
        out["childrens"] = kids
    return out


end = None if street == "river" else {"deal_number": 0, "node_type": "chance_node"}
b_oop = f"BET {round(pot * float(bets.get(f'oop,{street}', ['50'])[0]) / 100):.6f}"
b_ip = f"BET {round(pot * float(bets.get(f'ip,{street}', ['50'])[0]) / 100):.6f}"
dump = node(1, ["CHECK", b_oop], {
    "CHECK": node(0, ["CHECK", b_ip], {"CHECK": end,
                                       b_ip: node(1, ["CALL", "FOLD"], {"CALL": end})}),
    b_oop: node(0, ["CALL", "FOLD"], {"CALL": end}),
})
print("<<<START SOLVING>>>\nIter: 0\nTotal exploitability 9.5 precent", flush=True)
print("-------------------\nIter: 4\nTotal exploitability 0.4 precent\ntime used: 0.01", flush=True)
Path(cmds["dump_result"]).write_text(json.dumps(dump))
