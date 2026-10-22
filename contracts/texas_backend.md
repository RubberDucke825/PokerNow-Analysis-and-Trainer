# texas backend (ADDED 2026-10-10, Plan 7) — TexasSolver as the only postflop engine

Engine: `third_party/texassolver` (bupticybee/TexasSolver `console` branch, AGPL-3.0, private local use;
see UPSTREAM.md). Build: `tools/build_texassolver.sh` -> `third_party/texassolver/build/console_solver`.
Binary lookup in `pn`: `$PN_TEXAS` (may include args), then `<repo>/third_party/texassolver/build/console_solver`;
resources dir `<repo>/third_party/texassolver/resources` (`-r`). Missing binary -> `SolveError`, never a fallback.

## Scope
Heads-up postflop only: `players` must have exactly 2 entries (`players[0]` = OOP, `players[1]` = IP)
and `game.street` in flop|turn|river with a 3/4/5-card board. Preflop and 3+ players -> `SolveError`.
No locks: `pn.adapter.validate` rejects them (only a preflop fold lock is valid, and preflop is refused); `check_spot`
still refuses a spot with a `lock` (`locks_unsupported`) as a guard. TexasSolver player index: root `player` = 1 is OOP (acts
first), 0 = IP. The adapter must verify this on the fixture and document it.

## Input (command file, one per solve, in a temp dir)
```
set_pot <cents>                 set_effective_stack <cents>        set_board Qs,Jh,2h[,7c[,3d]]
set_range_oop <range>           set_range_ip <range>               # "AA,KK:0.5,KhQh:1.0,..." weights 0..1
set_bet_sizes <oop|ip>,<flop|turn|river>,<bet|raise|donk>,<pct>[,<pct>...]   # pct of pot
set_bet_sizes <oop|ip>,<street>,allin
set_allin_threshold <f>         build_tree
set_thread_num <n>              set_accuracy <pct of pot>          set_max_iteration <n>
set_print_interval <n>          set_use_isomorphism 1              start_solve
set_dump_rounds <n>             dump_result <path>
```
Mapping from `contracts/tree_config.md`: `[sizes]` per street -> bet/raise lines for both seats (donk only
where the TOML allows it); `allin_threshold` -> `set_allin_threshold`; preset -> accuracy / max_iteration /
threads (trainer: accuracy 0.5, 300 it; review: 0.3, 600 it; library: 0.3, 400 it on a 1-flop-size, 2-bet tree (Plan 10); all capped by
`time_limit_sec` through a watchdog kill + the last dump is NOT available, so cap by iterations only).
Amounts in cents (TexasSolver prints `BET 25.000000` = absolute amount, pot 50 -> 50%).

## Output
`strategy_output.md` document with `meta.backend = "texas"`, `meta.ev_available = false`, `meta.iterations`,
`meta.exploitability_pct_pot` (last "Total exploitability" line of the log), `meta.wall_sec`, `meta.stop`
(`converged` | `max_iterations` | `interrupted`). Node ids `n<int>`, root `n0`, tokens
`check|call|fold|bet<pct>|raise<pct>|allin` where `<pct>` is the configured pot percentage; `strategy`
in combo_index order (1326 rows; off-range and board-blocked combos all zero); `reach` and
`reach_by_player` by walking range weight x strategy product; `summary` reach-weighted frequencies.
`ev`, `action_ev`, `equity`, `summary.ev_bb|equity|eqr` are ABSENT. `--streets N` = `set_dump_rounds N`:
deeper chance nodes become `leaf` terminals with `street`, `pot`, `stacks`, `reach_by_player` so
`pn.solve.subgame` lifts them with a new texas solve on the dealt card.

## Consumers (Plan 7)
- Grading without EVs: `grade = best` if the solver plays the chosen action at >= 0.5 x the max action
  frequency for that combo, `mixed` if >= 0.1 x, `blunder` otherwise; `loss_bb = null`. Thresholds live in
  one constants block in `pn/pn/train/`. Frequencies come from the combo row (`strategy[combo]`).
- Leaks weight by blunder frequency instead of EV burn. `bb/100` disappears from the trainer UI.

## As implemented (T1, 2026-10-10): `pn/pn/solve/texas.py` + `texas_doc.py`

Verified against the binary (console branch 6dfb65b + the local `set_raise_limit` patch):
- Player index: TexasSolver `player` 1 = OOP (acts first at the root) = `players[0]`; 0 = IP = `players[1]`
  (checked with OOP range `AA` only: the root strategy rows are the AA combos and root player is 1).
- `set_pot P` puts P/2 into each player's commitment; `set_effective_stack S` plays min(stacks) only. Document
  `stacks` are each player's real stack minus street contributions; all-in detection uses the effective stack.
- Action strings: `CHECK`, `CALL`, `FOLD`, `BET <x>`, `RAISE <x>` with `x` printed `%f` ("BET 25.000000"). `x` is
  the chips the actor ADDS with that action (a raise includes the call), not raise-to. Document `amount` is the
  actor's commitment on the street after the action (raise-to), as in contracts/strategy_output.md.
- Sizes: bet = pct x pot; raise = pct x (pot after calling) + to-call (our `raise` semantics). Amounts are rounded to
  whole cents. `<pct>` in tokens is the configured size nearest to the realised one; an amount >= stack behind
  (minus 0.5 cent) is `allin`. TexasSolver turns a bet into all-in when `commit + bet > threshold x
  (effective stack + pot/2)` (commitments include the pot halves): close to, not identical with, tree_config's
  `allin_threshold` rule.
- `max_bets_per_street` -> `set_raise_limit N` (LOCAL PATCH in `src/tools/CommandLineTool.cpp`, before
  `build_tree`; TexasSolver's raise_limit counts the bet, so N = bets per street). An unpatched binary prints
  "command not recognized" and keeps its default 4.
- `donk`: OOP turn/river leads after calling IP's bet use `donk` sizes (= the street's bet sizes when
  `donk = true`, none when false). `donk = false` with IP as `last_aggressor` also drops OOP's flop bet sizes. The
  `allin` flag is per street and seat, so an all-in lead stays possible in both cases.
- Combo strings: higher card first with suit order c<d<h<s (pairs "AdAc", suited "AcKc"); canonicalized with
  `pn.ranges.canonical_combo`. Board-colliding combos are absent from dump strategies (rows stay 0).
- Ranges: weights scaled to max 1 per player (results are invariant); TexasSolver drops entries <= 0.005, so do
  we (reach 0). A hand class whose live combos share one weight is written as the class (`AA`, `AKs`, `AKo`);
  otherwise exact combos, which makes TexasSolver turn off suit isomorphism for that solve
  (`meta.isomorphism = false`; typical for lifted turn/river subgames).
- Dump: showdown and fold children are absent from `childrens`; a missing child is `fold` after FOLD, else
  `showdown`. A chance node reached with a player all-in becomes an `allin_runout` terminal. Past
  `set_dump_rounds` chance nodes have `deal_number 0` and no `dealcards` -> `leaf` terminal (`street` = the street
  that ended, `pot`, `stacks`, `reach_by_player`). Within the dump depth `dealcards` lists all 52 cards including
  the board; board cards are skipped.
- Log (stdout): `Iter: N` then `Total exploitability X precent` (X = % of the starting pot, averaged over both
  players) every `set_print_interval` iterations (we use min(10, max_iterations/4)), then `time used: s`.
  The solve stops early when X <= `set_accuracy`. `meta`: `iterations` (last `Iter` when converged, else
  `max_iterations`), `exploitability_pct_pot` (last X), `wall_sec`, `stop` (`converged` | `max_iterations`),
  `max_iterations`, `accuracy_pct_pot`, `threads`, `isomorphism`, `node_count`, `emitted_streets`, `mode="full"`.
  A killed run (watchdog: `time_limit_sec x 1.5 + 30` s, or `should_stop`) raises; no document is written, so
  `interrupted` never appears.
- Startup costs ~2.6 s per run (it loads the 52 MB `card5_dic_sorted.txt`).
- The binary writes `tmp_log.txt` into its cwd; runs happen in a temp dir. Binary lookup: `$PN_TEXAS`, then
  `<repo>/third_party/texassolver/build/console_solver`; resources: `$PN_TEXAS_RESOURCES`, then
  `<binary dir>/../resources`, then the vendored resources.

### Measured (T1, 2026-10-10; M3 8 GB, 8 threads, other agents running)
100bb SRP BB vs BTN flop Qs7h2c, pot 550, stacks 9700, turn 75 / river 150, raise 100, all-in, threshold
0.67, isomorphism on, dump_rounds 1. "s/it" = iterations 11-21 (incl. one exploitability pass).

| flop sizes | bets/street | peak footprint | max RSS | s/it | expl. after 21 it |
|---|---|---|---|---|---|
| 33,100 (trainer/library preset) | 3 | 8.18 GB | 1.8 GB (swapping) | 56 | 37.5% pot (killed at 21 it, 997 s) |
| 33,100 | 2 | 7.42 GB | 3.1 GB (swapping) | 45 | 31.5% |
| 33 | 3 | 5.84 GB | 3.8 GB (swapping) | 31 | 33.5% |
| 33 | 2 | 5.06 GB | 3.0 GB | 4.0 | 32.4% |

Trainer preset (300 it) on the preset tree: about 3-5 h. Library (1000 it): about 12-16 h. Both are swap-bound, and the
8.2 GB footprint is above the 6 GB gate. The library preset has the same tree, so its memory is the same (not run
to completion). Only the 1-size, 2-bet tree runs in RAM: about 20 min for 300 it.

### Measured (Plan 10, 2026-10-10; same machine, web app + agents running, swap already 4.4 GB in use)
100bb SRP BB vs BTN flop Kc9c8d (the library request), accuracy 0.3, 8 threads. The library preset is now this
tree: `hu_flop_sizes = 1` (the bucket's first sizing-table size), `hu_max_bets = 2`, 400 it cap, accuracy 0.3.

| flop sizes | bets/street | peak RSS | wall | stop |
|---|---|---|---|---|
| 33 (library preset) | 2 | 2.9 GB | 763 s | converged 0.295% pot at it 221 |

