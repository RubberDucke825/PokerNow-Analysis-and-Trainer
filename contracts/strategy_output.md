# Solver Output (JSON) — the document `pn.solve` builds from a TexasSolver dump (`pn/pn/solve/texas_doc.py`)

Stored by `SolveCache` (`data/solves/cache/<key>.json`) and read by the trainer, hand review, live assist and the
strategy viewer. The mapping from TexasSolver's dump is in contracts/texas_backend.md.

```json
{
  "meta": { "backend": "texas", "ev_available": false, "iterations": 300, "max_iterations": 300, "exploitability_pct_pot": 0.48,
            "accuracy_pct_pot": 0.5, "wall_sec": 73.2, "stop": "converged", "threads": 8, "isomorphism": true,
            "node_count": 18234, "emitted_streets": 1, "mode": "full" },   // stop: "converged" | "max_iterations"
  "players": [ {"index":0,"name":"hero","position":"BB","hero":true,"locked":false}, ... ],   // players[0] = OOP, players[1] = IP; locked is always false
  "combos": ["2c2d", "2c2h", ...],                  // 1326 strings in combo_index order
  "root": "n0",
  "nodes": {
    "n0": {
      "type": "action",                            // action | chance | terminal
      "street": "flop",
      "player": 0,                                 // acting player index
      "pot": 1500,
      "stacks": [9800, 14700],
      "history": [],                               // action tokens from root
      "actions": [ {"token":"check","child":"n1"}, {"token":"bet33","amount":495,"child":"n2"} ],   // amount = actor's street commitment after the action
      "strategy": [[f32 x n_actions] x 1326],      // per combo; combos with range weight 0 or blocked by the board -> all zeros
      "reach": [f32 x 1326],                       // acting player's combo weights reaching this node
      "reach_by_player": [[f32 x 1326] x n_players],  // HERO action nodes only (absent at other action nodes; leaf terminals and chance nodes always carry it). Every player's combo weights reaching this node (range weight x own strategy product along history). `reach` == reach_by_player[player].
      "summary": { "check": 0.62, "bet33": 0.38 }  // reach-weighted action frequencies
    },
    "n7": { "type":"terminal", "kind":"fold|showdown|allin_runout|leaf", "pot":..., "payoffs_by_player": null },
    "n8": { "type":"terminal", "kind":"leaf", "pot":..., "street":"flop", "stacks":[...], "reach_by_player":[[f32 x 1326] x n_players], "payoffs_by_player": null },  // end of the emitted streets: carries street, stacks and reach_by_player so pn.solve.subgame can start the next-street solve there
    "n9": { "type":"chance", "street":"turn", "pot":..., "stacks":[...], "reach_by_player":[[f32 x 1326] x n_players], "children": {"2c":"n10", ...} }   // only within the emitted streets
  }
}
```
- Node ids: `n<int>`, assigned in pre-order. Chance children keyed by card string; board cards are skipped.
- Action tokens: `check|call|fold|bet<pct>|raise<pct>|allin`, `<pct>` = the configured size nearest to the realised one.
- Hand-class summary (sets, two pair, flush draw, …) is **not** in the output; consumers compute from combos+board.
- Every emitted f32 array value (`strategy`, `reach`, `reach_by_player`, `summary`) is rounded to 5 significant digits; exact 0 stays 0.
- No EVs: `ev`, `action_ev`, `equity` and `summary.ev_bb|equity|eqr` are absent (`meta.ev_available = false`). Consumers that read
  them (`action_ev_loss`, the EV columns of `decision_ev`) use them only when `meta.ev_available` is true; `action_ev_loss` raises
  `EvUnavailable` otherwise.
- Documents cached before Plan 7 carry another `meta.backend`; heads-up postflop ones are stale and re-solved
  (`pn.solve.cache.is_stale`, contracts/solve_service.md).

## Range equity at a node (Plan 13, `pn/pn/web/equity.py`)
`GET /api/solves/{name}/equity?path=<comma tokens>` (same path semantics as `/ranges`, including `?node=<id>` in place of `path`; 404 for an unknown solve or path,
422 for a node without a flop/turn/river board or a non-heads-up solve):
```
{"board": ["Ks","7d","2c"],            // solve board + the chance cards on the path (a chance node uses the board before its card)
 "street": "flop"|"turn"|"river",
 "method": "exact"|"enumerated",       // river: one showdown; flop/turn: every runout enumerated ("montecarlo" is never used)
 "runouts": 1176,                      // river 1, turn 48, flop C(49,2) = 1176 unordered turn+river pairs
 "players": [{"position": "BB",
              "equity": 0.4612|null,   // joint mean: sum(reach_i * win_i) / sum(reach_i * compat_i) -> the two players sum to 1
              "combos": 182.5,         // sum of reach over combos not blocked by the board
              "per_combo": [f|null x 1326]}]}   // hand-vs-range equity vs the OPPONENT's reach at this node; null when this
                                                // player's reach is 0, the combo is board-blocked or no opponent combo is compatible
```
- Reach is exactly the per-player reach `/ranges` returns for the path (`study.node_ranges`); a player with unknown reach
  (`null`) gets `equity: null`, `combos: 0`, all-null `per_combo`, and so does the opponent's `per_combo`.
- Equity of combo i = sum over runouts r disjoint from i, opponent combos j disjoint from i and r of
  `w_j * (1 | 0.5 | 0)` / the same sum of `w_j`. Values rounded to 4 decimals.
- Implementation: hand values from two lookup tables built once per process with `handeval.evaluate` (49,205 rank multisets
  + 8,192 suited masks, ~0.3 s); card removal by inclusion-exclusion over per-(runout, card) sorted groups, all numpy.
  Caches: per-board runout plan (LRU 2, ~76 MB for a flop) and results keyed by (board, reach digest) (LRU 64).
  Concurrency: plans are built one at a time and each board once (concurrent cold requests wait for that build); at most
  2 computes run at once (4 concurrent cold flop requests peak at ~640 MB RSS instead of ~1.3 GB).
- Timings (8 GB M3, measured 2026-10-15): flop new board 0.45-0.69 s over HTTP including the doc load (0.46 s in-process),
  another node on the same flop 0.22 s, turn 0.017 s, river 0.001 s, repeat request 0.002 s. The very first request after
  the server starts paid up to 2.1 s (tables + imports). Exact enumeration fits the ~2 s budget, so no Monte Carlo.
