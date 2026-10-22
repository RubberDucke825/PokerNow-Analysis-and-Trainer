# Preflop study navigator API (Plan 9, 2026-10-10)

`GET /api/study/preflop?table=6|8&depth=100|200&line=<line>` (`pn/pn/web/study_preflop.py`, router mounted in `app.py`).
Amounts in cents (bb = 100), positions per contracts/README.md. Depth defaults to 100; table to 6.

`line`: comma-separated `<POSITION>:<token>` steps in acting order from the first seat to act, e.g.
`LJ:fold,HJ:raise,CO:fold,BTN:call`. Tokens: `fold | check | call | raise | allin` (`call` by a seat facing no raise
is a limp). Sizes are never in the line: each raise is sized like trainer villains (`pn.train.preflop.raise_to`:
GTO Wizard `raise_to_bb` on exact lines, else 2.5bb open +1bb per limper, 3-bet 3x IP / 3.5x OOP +1x per caller,
4-bet 2.2x, then all-in). The line is replayed through `pn.train.engine` (`start(positions, depth*bb, 50, 100)`,
`apply`). A step for a seat that is not to act, an illegal action, or a step after the hand is decided -> 400
`{"detail": "..."}` naming the bad step.

Response:
```
{table: 6, depth: 100, bb: 100, line: "LJ:fold,HJ:raise",      // echo, normalised
 pot: 350, street: "preflop",
 seats: [ {position, stack (behind, cents), committed, status: "to_act"|"waiting"|"folded"|"acted"|"allin",
           chosen: token|null,                                 // this seat's LAST preflop action (null = none yet)
           options: [{token, kind, to (street commitment after it, cents)|null, label: "Raise 2.5"|"Fold"|"Limp"|"Call"|"All-in 100",
                      freq: 0..1|null}] } ...]                 // preflop order; options = legal actions at the seat's
                                                              // most recent turn (or its upcoming turn for to_act /
                                                              // waiting). freq = range-wide share of that action from
                                                              // the actor's distribution; null for seats whose turn
                                                              // has not come (waiting) and for seats that are folded.
 actor: position|null,                                        // seat to act; null when preflop is over
 situation: {kind, key: "rfi|LJ" | null, source: "pokercoaching"|"gtowizard"|"chart", origin: "pc:<chart key>"|"gtow:<key>"|"chart",
             label: "PokerCoaching 100bb · 6max open LJ" } | null,
 cells: {"AKs": {"raise": 1.0}, "72o": {"fold": 1.0}, ...} | null,   // 169 classes -> token -> frequency for the actor,
                                                              // keyed by the actor's option tokens (codes R/A/C/F/X
                                                              // mapped through pn.train.preflop.code_action)
 summary: {"raise": 0.18, "fold": 0.82} | null,               // combo-weighted
 end: null | {kind: "flop"|"decided",
              players: ["HJ", "BTN"], pot, stacks: {pos: cents},
              matchup: {mid, label, pot_type: "srp"|"3bp"|"4bp+"|"limped", positions: [oop, ip]} | null,  // heads-up only
              boards: [{board: "Kc9c8d", solve: "cache-<16 hex of key>", weight}] ,   // solved flops of the matchup, [] if none
              solvable: bool,                                  // heads-up with betting left -> true (POST /api/solves/new street flop)
              note: null | "multiway: not solved (2-player only)" | "hand decided preflop"}
}
```
Source order for `cells` is the trainer's (`pn.train.preflop.gto_decision`): PokerCoaching class charts
(`pc_class_dists`) first, then the GTO Wizard spot at the nearest depth (`gtow_spot` + `cell_dist` per class),
then the formula chart (`chart_dist` per class representative combo). `freq` on options and `summary` come from
the same distribution.
