# Computational limits

What the solver actually solves, what is simplified, what it costs, and which spots are out of reach.
For the meaning of "converged" see [exploitability.md](exploitability.md).

Related contracts: [texas_backend.md](../contracts/texas_backend.md),
[tree_config.md](../contracts/tree_config.md), [solve_service.md](../contracts/solve_service.md),
[trainer.md](../contracts/trainer.md).

## Summary

| spot type | solved how | cost |
|---|---|---|
| preflop | never solved; charts only | none |
| heads-up flop | once per canonical flop per matchup, offline, into the flop library | ~3-6 min wall per 100bb SRP board at 1 %; 3-bet pots ~9x faster, 4-bet pots ~25x faster (table below) |
| heads-up turn / river in the trainer | solved live from the flop (or turn) document's leaf, `trainer` preset | ~5 s per 100bb SRP turn (measured below) |
| heads-up flop / turn / river, ad hoc | `pn solve run` or the web Strategy page, any preset | 100bb SRP flop with the library tree: 763 s, 2.9 GB to 0.3 % on an 8 GB M3 |
| 3+ players postflop | refused (`multiway_unsolvable`) | none |

## Tree abstraction

TexasSolver solves a game tree, not real poker. The tree only has the bet sizes you give it.

Default sizes (`DEFAULT_SIZES`, `pn/pn/solve/texas.py:38-40`):

| setting | value |
|---|---|
| flop bets | `auto`: 1 or 2 sizes by board texture from `config/sizing_table.toml` |
| turn bets | 75 % pot |
| river bets | 150 % pot |
| raise | 100 % of the pot after calling |
| max bets per street | 3 (bet + 2 raises) |
| all-in threshold | 0.67 (a bet that commits more than this share of the stack becomes all-in) |
| donk bets | allowed |
| all-in | always an option, every street, both seats (`texas.py:160`) |

Flop sizes by texture (`config/sizing_table.toml`, read by `texas.py:122-127`): 33 % alone on trips,
high paired, A-high rainbow, high dry rainbow and mono low boards; 33 and 100 on most others; 50 and
100 on connected boards; 25 and 50 on most monotone boards; default 33 and 100. The file says it is a
hand-authored table (lines 1-6). Its header says how it is read: `_auto_flop_bets` looks up the
board's texture bucket when `flop_bets = "auto"`, and the `library` preset keeps the first size.

Preset caps (`pn/pn/solve/request.py:45-52`, applied in `apply_preset`, lines 61-77):

| preset | bets per street | flop sizes |
|---|---|---|
| `trainer` | 3 (default) | sizing table (1-2) |
| `review` | 2 | sizing table (1-2) |
| `library` | 2 | the first size of the bucket only |

So every flop in the library offers exactly one bet size (usually 33 %), one turn size, one river size
and one raise size, plus all-in.

Other simplifications:

- **Suit isomorphism** is on (`set_use_isomorphism 1`, `texas.py:190`). TexasSolver turns it off for a
  solve whose ranges are written combo by combo, which is typical for lifted turn and river spots
  (`meta.isomorphism = false`, [texas_backend.md](../contracts/texas_backend.md) lines 72-75). This is
  exact, not an approximation; it only saves work.
- **Range pruning**: ranges are scaled to a maximum weight of 1 and entries at or below 0.005 are
  dropped (`MIN_WEIGHT`, `texas.py:36`, `:93-119`). Rare combos vanish from the solve.
- **Ranges come from preflop charts**, not from a preflop solve (PokerCoaching charts, then GTO Wizard
  data, then hand-written charts; [solve_service.md](../contracts/solve_service.md) line 13). A wrong
  chart gives a well-solved answer to the wrong question.
- **Two players only**. `check_spot` refuses preflop and anything but 2 players
  (`texas.py:76-86`).
- **No EVs**. TexasSolver dumps strategies only; grading uses frequencies
  ([exploitability.md](exploitability.md)).

## Flops: one solve per canonical flop

There are 22,100 raw flops and 1,755 canonical flops once suits are treated as interchangeable
(`pn/pn/solve/flops.py:17-18`). One solved board serves every suit permutation of itself: the trainer
deals it under a random permutation that avoids the live hole cards and reads the document through
the inverse (`pn/pn/train/flop_library.py:336-363`).

- 6-max 100bb: all 1,755 canonical flops per matchup.
- 8-max 100bb, 6-max 200bb, 8-max 200bb: a 184-flop subset per matchup, stratified by texture x high
  card and reweighted so it reproduces the full texture and high-card distributions
  (`subset_flops`, `pn/pn/train/library_fill.py:93-123`; `--subset 184` in `tools/cloud_runbook.md`
  line 10). The trainer only deals flops from this subset at those tables and depths.

Coverage numbers are in [flop-library.md](flop-library.md).

## Turn and river: lifted subgames

The flop solve builds the full tree to the river but the dump keeps only the flop (`set_dump_rounds 1`,
`texas.py:191`). At the end of the flop the document has `leaf` nodes that carry each player's
reach, the pot and the stacks ([strategy_output.md](../contracts/strategy_output.md) line 29).

When a turn card is dealt, `pn.solve.subgame` (`pn/pn/solve/subgame.py:118-164`) builds a new spot:

| held fixed | re-solved |
|---|---|
| both players' flop strategy, hence their ranges at the leaf (reach, scaled to max 1, dead cards removed) | the turn and river, from scratch, as a new heads-up solve |
| pot and stacks at the leaf | the betting tree: the `trainer` preset's (3 bets per street), not the library's 2 |
| who was the last aggressor on the flop | |

The trainer adds each live player's actual combo to its range at a small weight
([trainer.md](../contracts/trainer.md) line 151). The river is lifted from the turn the same way.

This is plain re-solving with fixed ranges. Nothing ties the turn solve back to the flop solve, so
the flop and turn strategies together are not one equilibrium. How far the turn re-solve drifts from
the flop tree's own turn strategy was never measured; this is a known, unquantified limitation.

## What is exact

Nothing beyond the abstraction. CFR converges toward an equilibrium of the abstracted game (these
ranges, these sizes, these caps) and stops at the stated exploitability. Card removal, showdown values
and suit isomorphism are exact. Everything else is a choice listed above.

## Runtime and memory

The reference machine is an 8 GB Apple M3. Measurements from
[texas_backend.md](../contracts/texas_backend.md) lines 93-114 (100bb SRP BB vs BTN, pot 550, 8
threads):

| flop sizes | bets per street | peak | speed / result |
|---|---|---|---|
| 33, 100 | 3 | 8.18 GB footprint (swapping) | 56 s per iteration |
| 33, 100 | 2 | 7.42 GB (swapping) | 45 s per iteration |
| 33 | 3 | 5.84 GB (swapping) | 31 s per iteration |
| 33 | 2 | 5.06 GB footprint, 3.0 GB RSS | 4.0 s per iteration |
| 33 (library preset) | 2 | 2.9 GB RSS | 0.295 % at iteration 221, 763 s |

Only the one-size, two-bet tree fits in RAM on 8 GB. That is why the library preset is that tree.

Wall clock recorded in library documents (`meta.wall_sec`, 8 threads, median and 90th percentile of
150 sampled documents per group). These ran on mixed hardware (the M3, 4-vCPU GitHub runners, Google
Cloud VMs), so treat them as rough:

| group | target | median s | 90th pct s |
|---|---|---|---|
| 6-max 100bb srp | 1 % | 196 | 340 |
| 6-max 100bb 3bp | 1 % | 22 | 36 |
| 6-max 100bb 4bp+ | 1 % | 7 | 9 |
| 6-max 200bb srp | 2 % | 287 | 568 |
| 6-max 200bb 3bp | 2 % | 38 | 66 |
| 8-max 100bb srp | 1 % | 162 | 259 |

Live turn solve with the `trainer` preset, measured 2026-10-22 on an 8-core M3 with 8 GB: a 100bb
BTN vs BB single-raised turn on 6c4c2d plus Kh, Js or 8h, built by `pn solve new --line srp` (chart
ranges, not a lifted leaf) and solved with `pn solve run --preset trainer` into a scratch cache. All
three stopped at 131 iterations, 0.44-0.50 % pot.

| measure | min s | median s | max s |
|---|---|---|---|
| solver (`meta.wall_sec`) | 4.7 | 5.3 | 5.9 |
| whole `pn solve run` command | 5.5 | 6.3 | 6.8 |

Turns lifted from a flop leaf, as the trainer plays them, and rivers were not timed separately.

Other memory caps:

- 4 solve documents kept in memory by `SolveCache` (`MEMORY_DOCS`, `pn/pn/solve/cache.py:30`).
- The web app keeps 4 loaded documents and 16 generated solves (`CACHE_SIZE`, `MAX_GENERATED`,
  `pn/pn/web/solves.py:39-40`).
- One solver process at a time per cache root, across processes (`cache.py:1-8`, `pn/pn/solve/lock.py`).
- `ROOT_KNOBS` (`pn/pn/solve/request.py:133-138`) limit preflop trees to under 5 GB. They only matter
  for building preflop spots; preflop is never solved.

Library totals (`tasks/todo.md` lines 53-54, confirmed by counting the files): 189,284 documents, 5.5 GB
gzipped, a 26.9 MB index.

## Out of reach

| spot | why | what happens instead |
|---|---|---|
| multiway postflop (3+ players) | TexasSolver is heads-up only (`texas.py:81-82`) | not solved, not graded (`multiway_unsolvable`) |
| preflop | never solved (`texas.py:79-80`) | chart frequencies |
| 300bb and deeper | the trainer deals 100 and 200bb only (`pn/pn/train/model.py:23`) | none |
| limped pots outside 6-max 100bb | skipped to save compute (`SKIPPED_POTS`, `pn/pn/train/library_alias.py:26`) | the trainer runs the board out with no betting |
| full-width flop trees (2-3 sizes, 3 bets) on 8 GB | measured above: swap-bound, hours per board | the library uses one size, two bets |
| heads-up table | trainer tables are 6 and 8 only (`TABLE_SEATS`, `request.py:124-127`) | parked |
| EV per action | TexasSolver dumps strategies only | frequency grading |

Heads-up (a 2-player table) is parked. The code blocks it in these places:

- The engine posts blinds by position name (`pn/pn/train/engine.py:93`), so a 2-seat BTN posts no
  small blind.
- `Config.table_kind` returns `"2max"` (`pn/pn/train/model.py:88-89`), while the chart loader knows
  `"hu"` (`pn/pn/profile/charts.py:11-14,44-47`); `f"{table}max"` is hard-coded in 11 places in
  `pn/pn`.
- 6-max GTO Wizard keys would leak into heads-up play: `situation()` (`pn/pn/train/preflop.py:44`)
  and `gtow_line_range` (`pn/pn/solve/sources.py:47`) are not gated by table.
- `new_request` adds the SB as dead money when no seat is the SB (`pn/pn/solve/request.py:215`), and
  `ROOT_KNOBS` has no entry for 2 players (`request.py:135-138`).
- There is no heads-up flop library, and the trainer never solves a flop live.

The only heads-up preflop source is `config/preflop_charts/hu.txt`.
