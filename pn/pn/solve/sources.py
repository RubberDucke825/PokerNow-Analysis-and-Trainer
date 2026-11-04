"""Seat ranges for solver spots (contracts/solve_service.md, Plan 7: GTO ranges only, no locks).

A seat's range is the trainer's preflop policy's own reach along its heads-up line, others
folding (policy_line_range: pn.train.preflop_line.policy_reach, the product of the seat's node
shares under pn.train.preflop.gto_decision: Upswing first, PokerCoaching, GTO Wizard, the
hand-written charts) for open, call, 3bet, call_3bet, 4bet, call_4bet and every SB limp line (limp / check, limp /
raise / call, the limp-3-bet and limp-4-bet lines: the SB bot limps into a BB hero).
Where the policy has no heads-up replay of the line (a heads-up table, an unknown seat, another
seat's limp, a cold call of a 3-bet, 5-bet lines), the per-line charts answer as before:
PokerCoaching pure charts, then GTO Wizard per-class frequencies, then the hand-written charts
(pn.adapter.spot_ranges.line_range, which also narrows later lines and falls back to defaults).
A preflop seat without a line (the root) is any-two.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from pn import ranges
from pn.adapter import spot_ranges as sr
from pn.adapter.state import Line
from pn.profile import charts, gtowizard

GTOW_SHARE = {"open": ("R", "A"), "3bet": ("R", "A"), "call": ("C",)}

Range = dict[str, float]


class ProfileSource(Protocol):
    def get(self, player_id: str, default: Any = None) -> Mapping[str, Any] | None: ...


def profile_hands(profile: Mapping | None) -> int:
    return int(profile.get("hands", 0)) if profile else 0


def pc_line_range(position: str, line: Line, table: str) -> tuple[Range, str] | None:
    """The PokerCoaching pure chart for `line.action`, or None when PokerCoaching has no chart
    (or an empty one: a seat needs combos) for the spot (pn.profile.charts.pc_range)."""
    action = sr.CHART_ACTION.get(line.action)
    if action is None:
        return None
    rng = charts.pc_range(table, action, position, line.vs)
    if not rng:
        return None
    return rng, f"pc:{charts.pc_key(table, action, position, line.vs)}"


def gtow_line_range(position: str, line: Line, depth_bb: float) -> tuple[Range, str] | None:
    """Per-combo frequency of taking `line.action` in the GTO Wizard solution, or None."""
    shares = GTOW_SHARE.get(line.action)
    if shares is None:
        return None
    situ = ("rfi", position) if line.action == "open" else ("vs_open", line.vs, position)
    key = gtowizard.situation_key(situ) if all(situ[1:]) else None
    if key is None:
        return None
    try:
        freqs = gtowizard.load_freqs()
    except FileNotFoundError:
        return None
    for depth in sorted(charts.GTOW_DEPTHS, key=lambda d: abs(d - depth_bb)):
        spot = freqs.get(f"{depth}|{key}")
        if spot is None:
            continue
        out: Range = {}
        for cls, cell in spot["cells"].items():
            known = {k: float(v) for k, v in cell.items() if k in gtowizard.ACTION_CODES}
            tot = sum(known.values())
            w = round(sum(known.get(a, 0.0) for a in shares) / tot, 4) if tot else 0.0
            if w > 0:
                out.update({c: w for c in ranges.class_combos(cls)})
        if out:
            return out, f"gtow:{depth}|{key}"
    return None


LIMPED_VS_BB = ("call", "3bet", "call_4bet")  # a line vs the BB's raise: the BB raised first, over limps


def lone_limper(lines: Iterable[tuple[str, str | None, str | None]]) -> str | None:
    """The pot's only limper among (position, line action, vs) seats, or None. A seat limped when
    its line is `limp`, or when it calls, 3-bets or calls a 4-bet against the BB: the BB raises
    first only over limps, unless its raise was a cold 4-bet (a limper that folded later is not
    seen)."""
    seats = list(lines)
    bb_opened = not any(p == "BB" and action == "cold_4bet" for p, action, _ in seats)
    found = [p for p, action, vs in seats
             if action == "limp" or (bb_opened and p != "BB" and vs == "BB" and action in LIMPED_VS_BB)]  # fmt: skip
    return found[0] if len(found) == 1 else None


SB_LIMP_PLANS = (
    (("SB", "call"), ("BB", "raise"), ("SB", "call")),
    (("SB", "call"), ("BB", "raise"), ("SB", "raise"), ("BB", "call")),
    (("SB", "call"), ("BB", "raise"), ("SB", "raise"), ("BB", "raise"), ("SB", "call")),
)
# (position, line action) after the SB limps into the BB -> its plan in SB_LIMP_PLANS
SB_LIMP_LINES = {("BB", "open"): 0, ("SB", "call"): 0, ("SB", "3bet"): 1, ("BB", "call_3bet"): 1,
                 ("BB", "4bet"): 2, ("SB", "call_4bet"): 2}  # fmt: skip


def _policy_plan(position: str, line: Line,
                 limper: str | None) -> tuple[tuple[tuple[str, str], ...], str | None, str] | None:  # fmt: skip
    """(plan, hero, origin text) of the heads-up line that ends in `position`'s `line`, or None.
    Who calls an open does not change the opener's reach, so the open plan has the BB call."""
    p, o = position, line.vs
    if limper == "SB" and (p, line.action) in (("SB", "limp"), ("BB", "check")):
        # the SB is a bot limping into a BB hero (pn.train.preflop.sb_bot_first_in), hero checks
        what = "limp SB" if p == "SB" else "check BB vs SB limp"
        return (("SB", "call"), ("BB", "check")), "BB", what
    if limper == "SB" and (k := SB_LIMP_LINES.get((p, line.action))) is not None:
        # the BB hero raises the SB bot's limp (GTO Wizard vs_limp), the SB continues per limp_vs_raise..
        what = f"{line.action} SB limp vs BB" if p == "SB" else f"{line.action} BB vs SB limp"
        return SB_LIMP_PLANS[k], "BB", what
    if line.action == "open":
        if o is not None or p == "BB":  # the BB raises only over a limp
            return None
        return ((p, "raise"), ("BB", "call")), None, f"open {p}"
    plans = {
        "call": ((o, "raise"), (p, "call")),
        "3bet": ((o, "raise"), (p, "raise"), (o, "call")),
        "call_3bet": ((p, "raise"), (o, "raise"), (p, "call")),
        "4bet": ((p, "raise"), (o, "raise"), (p, "raise"), (o, "call")),
        "call_4bet": ((o, "raise"), (p, "raise"), (o, "raise"), (p, "call")),
    }
    plan = plans.get(line.action)
    if plan is None or not o:
        return None
    return plan, None, f"{line.action} {p} vs {o}"


def policy_line_range(position: str, line: Line, table: str, depth_bb: float,
                      limper: str | None) -> tuple[Range, str] | None:  # fmt: skip
    """The trainer's preflop policy (pn.train.preflop.gto_decision) along the seat's heads-up
    line, others folding (pn.train.preflop_line.policy_reach at the library depth that leaves
    `depth_bb`): the product of the seat's own node shares, e.g. P(open) x P(call vs the 3-bet).
    The SB limp lines (`limper` == "SB", lone_limper) replay a BB hero: the SB bot limps at the
    PokerCoaching share, the BB checks or raises per its graded distribution, the SB continues per
    the limp-line nodes. None when the policy
    does not replay the line to a heads-up flop (seat_range falls back to the charts)."""
    from pn.train.preflop_line import policy_depth, policy_reach

    found = _policy_plan(position, line, limper)
    if found is None:
        return None
    plan, hero, what = found
    depth = policy_depth(table, plan, depth_bb)
    rng = policy_reach(table, depth, plan, position, hero) if depth is not None else None
    return (dict(rng), f"policy:{what}") if rng else None


def seat_range(*, position: str, line: Line | None, table: str, depth_bb: float,
               three_bettor: str | None, limper: str | None = None) -> tuple[Range, str]:  # fmt: skip
    """(range, origin) for one seat before dead-card removal and explicit ranges. `limper`: the
    pot's only limper (policy_line_range)."""
    if line is not None and (policy := policy_line_range(position, line, table, depth_bb, limper)):
        return policy
    if line is None or line.action == "check":
        return sr.line_range(line, position, table, depth_bb, opener_of_3bet=three_bettor)
    if sr.NARROW_FROM.get(line.action) is None:  # narrowed lines go through line_range
        pc = pc_line_range(position, line, table)
        if pc is not None:
            return pc
        gtow = gtow_line_range(position, line, depth_bb)
        if gtow is not None:
            return gtow
    return sr.line_range(line, position, table, depth_bb, opener_of_3bet=three_bettor)
