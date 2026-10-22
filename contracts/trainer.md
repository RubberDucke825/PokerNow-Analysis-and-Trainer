# Trainer and root-solve web API (ADDED 2026-10-02; streams T backend, U trainer UI, V strategy UI)

All routes JSON, snake_case, amounts in cents, positions per contracts/README.md. Backend owner (T) keeps this
file current with the exact field lists; UI owners (U, V) build against it and read `pn/pn/web/train_routes.py`
and `pn/pn/web/solves.py` for details. Python side: `pn/pn/train/` (engine, preflop policy, postflop walker,
sessions, showdown).

## Trainer (`pn/pn/web/train_routes.py`)

Current shape (2026-10-22). The dated sections below record how each part was added.

- `GET  /api/train/players` -> `[{player_id, name, hands, archetype}]` for the opponent picker (`[]` without a DB).
- `POST /api/train/new` body `{table: 6|8, depth: 100|200, hero_seat: position|null (null = random), opponents: [player_id|null ...] (<= table-1; missing = GTO bot), source: "gto" (anything else is a 400), seed?: int, rng_enabled: bool = true, window: int = 1000, drill_share: float = 0.8, drill_spot|drill_texture|drill_formation: str|null, p_form: int = 3, p_tex: int = 1, pot_mode: "start"|"avg"}` -> state (`NewSession`, `train_routes.py:65-80`). 400 on a bad config.
- `GET  /api/train/{sid}` -> state (also advances villains once a pending solve finished; poll while `solving`). 404 unknown sid.
- `POST /api/train/{sid}/act` body `{token}` (one of `legal_actions[].token`) -> state. 400 when not hero's turn, while solving, or for an illegal token.
- `POST /api/train/{sid}/next` -> state of the next hand; the button moves one seat. 400 while the hand runs. Stacks reset to `depth` every hand.
- `POST /api/train/{sid}/drill` body `{spot?, texture?, formation?}`, `POST /api/train/{sid}/params` body `{p_form?, p_tex?, pot_mode?}`, `POST /api/train/{sid}/retry`: Plan 13 / 14 below.
- `GET /api/train/{sid}/preflop_line`, `/api/train/stats`, `/leaks`, `/importance`, `/performance`, `/performance/recent`, `/dealable`: later sections.

State (hero view, `Trainer._view`, `pn/pn/train/session.py:577-622`):
```
{sid, hand_no, table, depth, bb (=100), source ("gto"), opponents: [player_id|null] (villain seats 1..),
 board: [cards], pot, street, button: "BTN",
 hero: {position, cards: [2], stack},
 seats: [{position, name, player_id|null, stack, in_hand, is_hero, committed (this street), last_action: token|null, allin}]
        -- physical order: hero first, then clockwise; villain hole cards only via result.showdown,
 history: [{street, position, token, amount (street commitment after a raise/call, else null), added, allin, origin}]
        -- origin (villains): "pc:<chart key>" | "gtow:<depth>|<key>" | "chart" | "solve:<token>" | "fallback"; null for hero,
 to_act: position|null,
 legal_actions: [{token, kind: fold|check|call|raise|allin, amount|null (street commitment after the action; null for fold/check), label}]
        -- empty unless hero is due and no solve is running. Preflop tokens: fold|check|call|raise|allin;
           postflop: the solved node's tokens (check|call|fold|bet<pct>|raise<pct>|allin), or the fallback set
           (bet33, bet75 / raise100, allin) when the street has no usable solve,
 solving: bool,
 solve: {status: none|solving|ready|failed|fallback, backend|null, note|null, name|null (Registry solve name), detail|null},
 solve_error: note|null (set when solve.status = failed),
 feedback: null | {street, chosen, origin: "pc:<key>"|"gtow:<key>"|"chart"|"solve"|"none", preflop_source|null,
           grade: "best"|"mixed"|"blunder"|"none", freq|null, best|null, best_freq|null, freq_error|null,
           loss_bb (null for texas documents), note|null, situation?, class? (preflop),
           options: [{token, amount|null, freq|null, ev_bb|null}], node_id|null, hand, position, board, pot,
           rng|null, buckets, rng_action|null, matched_rng|null, pure,
           grid_ref: {kind: "gtow", key} | {kind: "solve", name, node_id} | {kind: "pc", key, table, situation, position, other} | null, retry},
 hand_feedback: [feedback ...] (this hand), replay: bool,
 totals: {decisions, best, mixed, blunders, blunder_rate|null, hands, preflop_decisions, avg_freq_error|null, hero_net (cents)},
 rolling: null | {window, hands, rng_match_rate|null, blunder_rate|null, mixed_rate|null, best_rate|null, leaks_top},
 rng|null, rng_enabled, drill|null, drill_share, pinned_drill, params: {p_form, p_tex, pot_mode},
 hand_over: bool,
 result: null | {winner: position|null (largest gross win), amount (total pot), board (run out), hero_folded,
                 showdown: [{position, cards}], won: {position: cents}, net: {position: cents}}}
```

Grading (`pn/pn/train/grading.py`): frequencies only, no EVs. `grade = "best"` when the chosen action's frequency is at least
0.5 x the most frequent action's, `"mixed"` at least 0.1 x, else `"blunder"`; `"none"` when there are no frequencies (`note`:
`solver_unavailable`, `multiway_unsolvable`, `off_tree`, `combo_outside_range`). Preflop: the frequencies of hero's hand class
from PokerCoaching, then GTO Wizard, then the pure chart (origin `pc:` / `gtow:` / `chart`). Postflop: hero's exact combo row at
the solved node (origin `solve`). `totals.decisions` counts graded decisions only. Hero folding ends the hand. Showdown uses
pn.profile.handeval.evaluate with side pots.

Villain policy: preflop PokerCoaching, then GTO Wizard frequencies, then the pure chart, for every villain, named or not
(source order in "Plan 7 additions" below). Sizes: GTO Wizard `raise_to_bb` on exact lines, else 2.5bb open (+1bb per
limper), 3-bet 3x IP / 3.5x OOP (+1x per caller), 4-bet 2.2x, then all-in (`pn/pn/train/preflop.py:23-26`). Postflop:
flops come from the solved library (2026-10-07 below); turn and river are solved when dealt with the `trainer` preset,
lifting the previous street's leaf via pn.solve.subgame. Every live player's actual combo is kept in its range at weight
>= 0.02. A failed solve switches the street to the fallback policy (`solve.status = failed`, not graded).

## Root solves and subgames (`pn/pn/web/solves.py`; routes in `train_routes.py`)
- `POST /api/solves/new` (202) body `{table, depth, source, street: "preflop"|"flop", board?: [3 cards] or "Kd7s2c", line?: "srp"|"3bp", seats: [{position, player_id?, range?}], hero?: position, preset?: "trainer"|"review"|"library" (default review)}` -> `{job_id}`. Preflop: the whole table (seat entries add players / ranges; hero defaults to BTN), tree-size checked first. Postflop: `seats` are the players in the pot. 400 on an unknown preset.
- `GET  /api/solves/jobs/{job_id}` -> `{job_id, status: "queued"|"running"|"done"|"error", name?, error?, estimate_bytes?, limit_bytes?, elapsed_sec}`; `error = "too_large"` carries the estimate and limit. 404 unknown job.
- `POST /api/solves/{name}/subgame` (202) body `{node_id, cards: [1|3 cards] or "As", preset?}` -> `{job_id}`; works on `leaf` terminals and chance nodes. On completion the result is registered as `<name>__<node_id>__<cards joined>` (an existing one is reused). 404 unknown solve, 422 bad cards; a node that does not end a street ends the job with `status: error`.
- `GET /api/solves/{name}` additionally returns `parent: null | {name, node, cards, path: [tokens from the parent root]}` and `elapsed_sec|null`; generated solves carry `meta.board`. The newest 16 generated solves (UI and trainer) are kept in memory.
- Existing `GET /api/solves`, `/api/solves/{name}`, `/nodes/{id}`, `/walk` unchanged.
- Plan 7 (2026-10-10): these routes moved to `pn/pn/web/solve_routes.py`; body extensions, job progress / cancel, library, delete and ranges are in contracts/solve_service.md "Plan 7".

## Plan 4 additions (2026-10-02): RNG for mixed spots, rolling performance

- State gains `rng: int` (1-100, drawn per hero decision from a per-session seeded generator; `rng_enabled: bool` from the session options) and `rolling` (totals from `/api/train/stats` with the session's window, refreshed at hand end).
- `feedback` gains `rng`, `buckets: [{token, from, to}]` (1-based inclusive ranges over 1-100 built from the strategy for hero's hand, actions ordered by aggression: fold < check < call < bet/raise sizes ascending < allin; a bucket is omitted when its frequency rounds to 0), `rng_action` (the token whose bucket holds `rng`), `matched_rng: bool`, `pure: bool` (one action >= 99%; the RNG is shown but any action in that bucket matches). Preflop buckets come from GTO Wizard class frequencies; postflop from the solved combo row.
- Persistence (`pn/pn/ingest/schema.sql`, written at hand end only): `train_hands(hand_uid PK, session_id, played_at, table_size, depth, source, hero_pos, net_bb, opponents_json)` and `train_decisions(hand_uid, seq, street, position, situation, hand_class, rng, options_json, chosen, rng_action, best, loss_bb, grade, matched_rng, PRIMARY KEY(hand_uid, seq))`. `situation` ∈ rfi | vs_open | vs_3bet | vs_4bet+ | cold | first | facing_bet | facing_raise.
- `GET /api/train/stats?window=1000&source=` -> `{window, hands, decisions, loss_bb, bb_per_100, blunders, blunder_rate, rng_decisions, rng_match_rate, by_street: {street: {decisions, loss_bb, bb_per_100, match_rate}}, by_position: {...}, by_situation: {...}, slices: [{from_hand, to_hand, hands, bb_per_100, match_rate}] (100-hand slices, oldest first)}`. Window counts hands, newest first. CLI: `pn train stats [--window N] [--source S]`.
- `POST /api/train/new` accepts `rng_enabled: bool` (default true) and `window: int` (default 1000).

Implemented (W, 2026-10-02); exact fields and the decisions taken where the text above was open:
- State: `rng: int|null` (the roll for hero's pending decision; null when the RNG is off, hero is not due, or the hand is over), `rng_enabled: bool`, `rolling: null | {window, hands, bb_per_100|null, rng_match_rate|null, blunder_rate|null}` (null when the server has no DB path; computed over all sources, refreshed at session start and hand end).
- Rolls come from a generator seeded with `"rng:<seed>"` (os.urandom when no seed), separate from the deal generator, so `rng_enabled` never changes the cards.
- `feedback` adds `rng: int|null`, `buckets: [{token, from, to}]` (`[]` when the options have no frequencies, e.g. fallback streets), `rng_action: token|null`, `matched_rng: bool|null`, `pure: bool`. Widths use largest-remainder rounding of the normalised option frequencies (ties to the less aggressive action). In a pure spot `rng_action` is the dominant action whatever the roll, and `matched_rng = chosen == rng_action`. `rng_action` / `matched_rng` are null when the RNG is off or there are no buckets. Preflop buckets use the same frequencies as `options` (GTO Wizard class frequencies, else the chart weights).
- `train_decisions` has one extra column `pure INTEGER NOT NULL DEFAULT 0`, so the match rate can exclude pure spots. Every hero decision is stored, `seq` 0-based. `hand_uid = "<sid>-<hand_no>"`, `played_at` ISO UTC with microseconds, `opponents_json` = `state.opponents`.
- `situation`: preflop by raises faced (0 rfi, limps included; 1 vs_open; 2 vs_3bet if hero raised earlier else cold; 3+ vs_4bet+); postflop by bets this street (0 first, 1 facing_bet, 2+ facing_raise).
- `/api/train/stats` also returns `source` (null = all). `bb_per_100 = 100 * loss_bb / hands` (EV lost, positive = worse; preflop decisions carry no loss). `blunder_rate = blunders / decisions with a loss_bb`. `rng_decisions` and `rng_match_rate` count decisions with `matched_rng` not null and not pure. Each `by_street` / `by_position` / `by_situation` entry is `{hands, decisions, loss_bb, bb_per_100, match_rate}` where `hands` = window hands with a decision in that group (the bb/100 denominator). `by_street` keys come in street order, the others sorted. Slices: `from_hand` / `to_hand` are 1-based indexes within the window, oldest first. 400 when `window < 1`; an empty DB gives zeros and nulls.
- `POST /api/train/new` returns 400 when `window < 1`.

## Plan 6 additions (2026-10-02): leak-weighted drills

- Spot key (`pn.train.spots.spot_key(decision) -> str`): preflop `pf|<pos>|<faced>|<vs>` with faced ∈ rfi | vs_limp | vs_open | vs_3bet | vs_4bet+ | cold and vs = the aggressor's position or "-"; postflop `post|<pot>|<ip|oop>|<street>|<first|facing_bet|facing_raise>` with pot ∈ srp | 3bp | 4bp+ | limped. Stored on train_decisions as `spot` (new column) and used by decision_ev consumers via the same function.
- `GET /api/train/leaks?window=1000` -> `[{spot, label, freq, n, loss_per_occ_bb, expected_burn_per_100, weight}]` sorted by expected burn. freq = solver reach probability of hero landing in the spot (preflop from the library root for the session's table/depth, GTO Wizard frequency chains as fallback; postflop = preflop reach of pot type and position × conditional frequency from solved street nodes, trainer observations as fallback). loss_per_occ_bb shrunk toward the global mean with prior weight 5. weight = normalized (expected_burn + exploration bonus c·sqrt(ln N / (n+1))).
- `POST /api/train/new` accepts `drill_share` (0..1, default 0.8). State gains `drill: {spot, label} | null` (null for natural hands; the UI does not display it by default) and `rolling.leaks_top: [label x 3]`.
- Drill construction: hero seat chosen to match the spot; hero's hand sampled from the solver's range for the line into the spot (so hero usually gets there, every decision still hero's own); villains ahead of the spot take the required action with a hand sampled from the part of their strategy that takes it; if hero deviates before the spot the hand continues as natural (`drill` set to null, `train_hands.drill_spot` records the attempt). Postflop drills force the preflop line then draw villains' postflop actions the same way.
- Robustness: trainer solves have priority over library jobs in SolveCache (a `priority` flag; one solve at a time, trainer first); a failed solve sets `state.solve_error` and the decision is recorded ungraded with note "solver_unavailable". No silent fallback play.

Implemented (W, 2026-10-02); exact fields and the decisions taken where the text above was open:
- `pn.train.spots.spot_key(d)` takes a mapping: preflop `{street: "preflop", position, faced, vs}`, postflop `{street, pot, ip: bool, situation}`. Helpers: `preflop_fields(position, raisers, limpers)`, `postflop_fields(position, preflop_raises, live_positions, street_bets)`, `from_state(engine_state, seat)`, `parse(key)`, `label(key)` (e.g. "BB vs BTN open", "CO first in", "BB vs SB 3-bet cold", "SRP IP flop facing a bet"). Positions are plain table positions (no _HU suffix). `pn.ev.decisions` now fills `decision_ev.spot` with these keys for rows graded from now on; older rows keep their previous values.
- Columns: `train_decisions.spot TEXT`, `train_decisions.note TEXT`, `train_hands.drill_spot TEXT` (NULL = natural hand), `train_hands.drill_landed INTEGER` (1/0 for drills, NULL for natural hands). Older databases get them by ALTER (`pn.train.stats.ensure`, run before every read or write). Hands saved before Plan 6 have `spot` NULL and are ignored by the leak table.
- `GET /api/train/leaks?window=1000&table=6&depth=100` -> rows `{spot, label, freq, n, loss_per_occ_bb, expected_burn_per_100, weight}`. The window counts the newest hands at that table and depth. 400 for window < 1 or a bad table/depth.
  - `freq` is expected occurrences per hand. Every spot with freq > 0 is listed and can be drilled. No field is ever null, and `weight` sums to 1.
  - `n` counts graded decisions in the spot. Preflop decisions have no EV, so their loss is `freq_error x 1 bb` until preflop EV grading exists. Ungraded decisions (`solver_unavailable`) are not counted.
  - `loss_per_occ_bb = (sum + PRIOR_N x global_mean) / (n + PRIOR_N)`, with PRIOR_N = 5. global_mean = (sum of all losses + PRIOR_N x PRIOR_LOSS_BB) / (N + PRIOR_N) with PRIOR_LOSS_BB = 0.5 (2026-10-07: shrunk itself, so a single zero-loss decision no longer prices every spot at 0; ties sort by frequency, then spot). Both are constants in `pn/pn/train/leaks.py`. A spot never reached is therefore priced at the global mean, so `expected_burn = 100 x freq x prior`, and its weight is driven by its frequency plus the bonus. Common unseen spots are drilled soon and rare ones eventually. `weight = (expected_burn + 0.5 x sqrt(ln max(N, 2) / (n + 1))) / sum`, with N = graded decisions over the listed spots.
  - Sorted by expected burn, then weight, then spot.
- Frequencies, exactly as built:
  - Preflop comes from the library root's `summary` frequencies (path probability, nodes below 1e-6 pruned, hero seat uniform). Without a root it comes from GTO Wizard chains, 6-max only, at the nearest depth.
  - P(hero sees the flop in pot/side) comes from the root's `leaf` terminals.
  - Conditionals come from SolveCache entries. A flop document's pot type is read from the pot per player in big blinds (<= 1.5 limped, <= 4.5 srp, <= 13 3bp, else 4bp+). Flop documents are grouped by pot and side, and turn/river documents by side only. Turn and river reach chain through each street's probability of continuing with hero and an opponent in.
  - Fallback for both parts: counts from natural trainer hands.
  - The preflop map is cached per (table, depth). Cache entries are read once each, and new solves are picked up incrementally. The web server warms the cache scan in a background thread at startup, because the first scan of a full cache takes minutes (about 16 min on the 142k-entry cache). Until it finishes, background (web) callers use the fallbacks: the importance tables and the weighted drill picks run on the preflop map, observed trainer conditionals and the flop-first fallback, so weighted drills are dealt from the first hand.
- Drills: `POST /api/train/new` `drill_share` (0..1, default 0.8; 400 outside). Drills need the trainer's leak model (always present in the web app).
  - Each hand draws a drill with probability `drill_share`, picks a spot by `weight`, and seats hero at the line's position.
  - The line is the maximum-reach path to the spot's node, or for postflop spots to the maximum-reach flop leaf for that pot and side.
  - Hands: hero's comes from its reach at the target node (or leaf). Each line villain's comes from the reach x strategy share of its last line action in the library, or from the GTO Wizard class shares (product over its steps). Clashing cards are excluded.
  - Villain actions on the line have origin `"drill"` and use the trainer's normal raise sizes.
  - Postflop villains are steered (origin `"drill:<token>"`): passive before the target street. On it: check for `first`; bet (the most frequent bet in the solve) then call for `facing_bet`; raise hero's bet for `facing_raise`.
  - `state.drill = {spot, label}` while the drill is live, and it stays set after hero lands. It becomes null when hero leaves the line preflop (fold vs passive vs aggressive compared), the hand leaves the line, or the hand passes the target street.
- `state.drill_share`: the session's setting (0.0 when the trainer has no leak model). `GET /api/train/stats` adds `drill_hands`, `natural_hands` and `drills_landed` over the window.
- `state.solve_error` is set while the street whose solve failed is being played. The next street's solve or the next hand clears it.
- `state.rolling.leaks_top`: labels of the first 3 leak rows for the session's table/depth/window (`[]` without a leak model).
- Robustness:
  - `SolveCache.solve(spot, backend, *, priority=False)`. Trainer street solves pass `priority=True`. The process-wide `PriorityLock` serves waiting priority solves first, never interrupts a running one, and still allows one solve at a time. Library builds launched as separate processes are not covered.
  - `state.solve_error: str | null` is the failed solve's message.
  - Hero decisions on such a street get `feedback.grade = "none"`, `feedback.note = "solver_unavailable"` (null otherwise), no loss, and are stored with `note`.
  - Villains on that street still act by the fallback policy, so the hand can finish. It is no longer silent: the state carries `solve_error`, and nothing on that street is graded.

## 2026-10-07: 2-player only

- Once a postflop street starts with 3+ players in the hand, that street and every later street of the hand are not solved, even when the hand is heads-up again: `state.solve = {status: "none", backend: null, note: "multiway", name: null}`, villains act by the fallback policy (origin `"fallback"`) so the hand finishes, and `state.solve_error` stays null.
- Hero decisions on those streets get `feedback.grade = "none"`, `feedback.note = "multiway_unsolvable"`, no loss, and are stored in `train_decisions` with that `note`; they are excluded from leak loss averages.
- A missing or failing solver binary is not replaced by another solver: the street is recorded as `solver_unavailable` (the existing failed-solve path).
- Leaks: postflop spot frequencies count only pots that reach the flop heads-up. Library preflop leaves with 3+ players live, 3+ player street solves in the SolveCache, and trainer hands with a `multiway_unsolvable` decision contribute nothing (`train_decisions.note` is read for this).
- Drills: postflop drill lines come only from heads-up library leaves, so a drill that stays on its line reaches a heads-up flop. If a flop is multiway anyway while a drill has not landed, the drill is left (`state.drill = null`) and the hand records `drill_landed = 0`.

## 2026-10-07: web additions (stream U, `pn/pn/web/solves.py`, `pn/pn/web/train_routes.py`)

- `GET /api/solves/{name}` adds `spot: null | {table, depth, source, street, board: [cards], line, hero, preset, seats: [{position, player_id, range}]}`. It is the `POST /api/solves/new` body that produced the solve. It is null for subgames, solver files, uploads and trainer street solves. It is persisted with the name, so it survives eviction and restarts. (The "vs <player>" view that re-posted it with a villain `player_id` is gone since Plan 7.)
- Trainer state `feedback.gto_best` was added here for exploitative street solves; Plan 7 removed it (see below).
- Registry: a generated 2-player postflop document from an older backend (the SolveCache stale rule, `pn.solve.cache.is_stale`) is swapped for its SolveCache entry under the same name once that entry has been re-solved with texas. The entry file is re-read only when its mtime changes. Library roots, files, uploads and trainer street solves (registered without a key) are not refreshed.

## 2026-10-07: library-first heads-up flops ("boards come from the solved set")

A flop solve takes minutes to hours, so the trainer never solves a flop live (`pn/pn/train/flop_library.py`).
- Matchup: a heads-up pot at the start of the flop, identified by the trainer's own preflop engine and sizes (`pn.train.preflop` raise_to / code_action). `flop_library.matchups()` enumerates, per table and depth, every line where the other seats fold: a limp checked by the BB, open + call, 3-bet + call, 4-bet + call, limp + isolation raise + call, and a 3-bet cold-called after the opener folds (85 for 6-max). Label: `<pot> <OOP>-<IP> <depth>bb: <line>`.
- Library key: `spot_key` of the canonical flop request (`library_request`: GTO ranges from the preflop line, anonymous seats, no dead hero cards, hero flag on the preflop aggressor, else the IP seat) with the `library` preset. The document must have `meta.backend = "texas"`.
- Index: `<SolveCache root>/hu_flop_library.json`, `{matchups: {<id>: {label, pot, positions, depth, boards: {<flop>: {key, weight}}}}}`, written by `pn library fill`, re-read by the trainer when it changes.
- Dealing: when preflop ends heads-up, the flop is drawn from the matchup's solved boards whose cards are still in the deck (weighted by canonical flop weight) and the cached document is the flop tree. One solved board serves its whole suit-isomorphism class: it is dealt under a suit permutation chosen uniformly among its distinct images that avoid every live hole card (a board is skipped only when none does), the hand reads the document through the inverse permutation, and turn and river are lifted back into the dealt cards (`pn/pn/train/suitmap.py`). The study views keep showing the document's board. The flop tree is ready at once (`state.solve = {status: "ready", backend: "texas"}`). The source setting does not change library flops: they are GTO.
- No solved board for the hand's matchup (a line outside the enumeration, or not filled yet): the hand ends at the flop. The board runs out with no postflop betting, `state.solve = {status: "none", note: "no_library_solve"}`, nothing postflop is graded, and an unlanded drill is left.
- Turn and river: lifted from the flop (or turn) tree's leaf with `pn.solve.subgame` and solved live with the `trainer` preset (10 s), with the session's hero flagged and hero's cards dead for the villains; each live player's actual combo is added to its range (MIN_WEIGHT). (The 2026-10-07 measurement of about 1 s per turn subgame was made with the previous engine; TexasSolver adds about 2.6 s of startup per run.) A street with no leaf to lift from (the tree and the hand diverged) is not solved: `state.solve.note = "off_tree"`, fallback villains, hero ungraded with note `off_tree`.
- Hero's combo outside the library range (natural hands are not widened to the actual cards on library flops): `feedback.grade = "none"`, `note = "combo_outside_range"`, never graded against the range summary.
- Drills: a postflop drill spot is picked only when its line's heads-up flop has at least one solved board; preflop drill spots are unaffected.
- `pn library fill [--flops 50] [--configs 6] [--table 6] [--depth 100] [--preset library] [--backend texas] [--db PATH] [--dry-run]` (`pn/pn/train/library_fill.py`). Order: matchups by expected burn = 100 x P(the line per hand) x mean `loss_per_occ_bb` of the pot type's postflop leak rows (0.5 bb without any). P(line) comes from the library root's heads-up leaves (`FreqMap.matchups`, keyed by `pn.train.spots.line_key`), else from pot-type priors (srp 1, 3bp 0.3, limped 0.2, 4bp+ 0.05; x2 with the BB, x1.5 with the BTN or SB; x0.2 for limp-isolation and cold-called 3-bets). The top `--configs` matchups (0 = all) are interleaved greedily by burn / (1 + boards planned). Flops: `--flops` canonical flops stratified by texture bucket (at least one per bucket when n allows, the rest by raw weight, round-robin so prefixes stay stratified). Resumable: cached boards are recorded and skipped. A pid file allows one fill per cache root. Plan 10: `--accuracy x [--accuracy y ...]` runs the whole order once per pass, coarse to fine; each pass solves to x % of pot under its own cache key and records `accuracy` in the index entry; a board already indexed at a finer or equal accuracy is reported `finer` and kept. The library preset is one flop size (the bucket's first sizing-table size), two bets per street, 400 iterations, 0.3 % of pot (contracts/texas_backend.md measured table). Run it in the background (`nohup pn library fill > fill.log 2>&1 &`). `--shard i/n` (2026-10-11) keeps every n-th item of the same order from i, so n fills on separate cache roots (`PN_SOLVE_CACHE`, where the lock and pid file live) split the work; `tools/cloud_fill.sh push|start|status|stop|pull` runs them on a Linux box and copies the documents back (content-addressed: the next local fill records them as `cached`). The thread count is part of the cache key, so shards keep the default. `tools/gha_fill.sh export|create|seed|start|status|pull` runs the same shards on GitHub Actions from a public code-only mirror (`$PN_FILL_REPO`, checked out at `$PN_FILL_DIR`; workflow `tools/gha/fill.yml`: 20 free 4-vCPU jobs at a time, 6 h each; config/ charts and the cache travel as an encrypted release asset `seed`, results as encrypted artifacts).
- One solve at a time across processes: `SolveCache.solve` also holds an flock on `<root>/.solve.lock` (`pn/pn/solve/lock.py`). Priority solves (trainer turn and river) leave a marker in `<root>/.solve_priority/` while they wait and run. Ordinary solves wait for them, and the fill's solver subprocess is killed (`Preempted`) as soon as one appears, then retried once no priority solve is waiting.
- `pn library status [--table 6] [--depth 100] [--flops 50]` prints the preflop plan (when there is one) and, per matchup by expected burn, solved and planned boards.
- Library distribution (2026-10-22, `tools/library_pack.py`, stdlib for packing; unpack's reindex and sample import pn). `pack <cache> <out> [--part-bytes 1900000000]` writes the index and every document (gz preferred) with its `.toml` into plain tar volumes `library-NNN.tar`, each under the part size (GitHub release assets cap at 2 GB), the index in the first, plus `MANIFEST.json` (`format`, parts with `name`, `bytes`, `sha256`, `files`; `documents`, `boards`, `matchups`, `aliases` from the index; `pn_version`, `commit`) and `SHA256SUMS`. `unpack <parts dir | archive> <cache>` checks every part against the manifest first, extracts only flat document / request / index names, keeps an existing document or request at least as new as the archived one, merges the index (boards already indexed win) and re-keys it per table / depth like `pn library reindex` (`flop_library.reindex`); it refuses while a fill runs on the root. `sample <cache> <out.tar.gz> --table 6 --depth 100 --line srp|3bp|4bp --seats OPENER,CALLER --boards N` takes the first N boards in index order of the matchup the trainer looks up for that line (`replay` + `matchup_of`), re-derives the first board's key to prove it, and writes them with a trimmed index (no `flops` / `subset` marks). The committed sample `data/fixtures/library_sample.tar.gz` is 6-max 100bb BTN open / BB call, 60 boards (4.7 MB); `./poker.sh quickstart` unpacks it when the cache has no index, so a fresh checkout deals that matchup's flops.

## 2026-10-07: GTO Wizard chain frequencies (stream P2, `pn/pn/train/leaks.py`)

The preflop library root is gone and will not be rebuilt, so without a root every spot frequency comes from GTO Wizard chains (`gtow_walk`, 6-max only; other tables have no solver frequencies). Spot keys, shrinkage and weights are unchanged.
- Each situation key is read at the nearest depth that has it, from `gtowizard.spot_table` (reach-weighted frequencies). Hero's seat is uniform, so every chain adds `reach / 6` to each seat's spot.
- Preflop: unchanged chains (rfi, BB vs SB limp, vs_open, cold, vs_3bet .. vs_6bet; players between the actors fold with their fold share).
- Heads-up flops, `post|<pot>|<ip|oop>` for both players of each line:
  - limped: P(folds to the SB) x P(SB limps, rfi|SB call) x P(BB checks, vs_limp|SB|BB).
  - srp: P(folds before O) x P(O opens) x P(players between fold vs the open) x P(C calls, vs_open|O|C) x P(players behind C fold, their vs_open fold share). Example: P(BB sees the flop vs a BTN open) = P(LJ, HJ, CO fold rfi) x P(BTN opens) x P(SB folds vs BTN open) x P(BB calls).
  - 3bp: the 3-bet reach (as for vs_3bet) x P(O calls, vs_3bet|O|T). Cold-called: the 3-bet reach x P(players between fold, cold) x P(C calls, cold|O|T|C) x P(players behind fold, cold) x P(O folds, vs_3bet|O|T).
  - 4bp+: the 4-bet reach x P(T calls, vs_4bet|O|T).
  - The data has no open + call (squeeze) or 3-bet + cold-call nodes, so the players behind a caller use their plain vs_open / cold fold share and the opener its vs_3bet fold share. Limp-isolation pots and 5-bet+ pots (all-in at these depths) are not counted. Real 100bb data: about 0.40 heads-up flops per hand, srp 0.041 per side per hand.
- `FreqMap.post_lines[<post|pot|side>]`: every flop line into it, highest reach first. `FreqMap.lines` keeps the best one. `Leaks.lines(table, depth, spot)` returns them; `Leaks.line` is the first.
- Postflop spot frequency = P(heads-up flop in pot/side) x P(spot | that flop):
  1. SolveCache conditionals (unchanged grouping). Every heads-up street document now contributes both seats, so a library flop solved with the aggressor flagged also prices the caller's side.
  2. Else the observed share from natural trainer hands.
  3. Else `FIRST_FALLBACK = 1.0` for `flop|first` only. Turn and river spots and facing_bet / facing_raise get no frequency until a document or an observation exists.
- `FreqMap.matchups` stays empty for GTO Wizard maps, so `pn library fill` still orders matchups by its pot-type priors.
- Drills: a postflop spot is drillable when one of its lines replays (trainer preflop engine) into a matchup with at least one solved board in the flop index. The drill uses the highest-reach such line (`Trainer._drill_line`), so a spot is drillable as soon as any matchup of its pot and side is filled. Folding villains' hands come from the GTO Wizard class shares of their steps. The two flop players' hands come from one solved board of the matchup, drawn by weight: the line's weights times that flop document's range (`drills.flop_ranges`: reach at each player's first node), or the range alone when they do not overlap, with the board's cards dead for every seat. So hero's combo is in the library tree and graded (`combo_outside_range` otherwise happened on most drills), and that board stays dealable.
- `state.solve.note = "no_library_solve"` is set only when preflop ends heads-up with betting left and the matchup has no solved board. Hands that end preflop keep `solve = {status: "none", note: null}`. A limp checked by the BB is a heads-up limped flop, so it gets the note while limped boards are unfilled.

## Plan 7 additions (2026-10-10, stream T2): frequency grading, no EVs, GTO only

TexasSolver (`contracts/texas_backend.md`) is the only postflop engine and its documents carry no EVs, so every grade
(trainer preflop and postflop, hand review) comes from frequencies. This section overrides the EV fields above.

Grading (`pn/pn/train/grading.py`, the one constants block): `grade(freqs, chosen) -> GradeResult{grade, freq, best_token, best_freq}`.
`freqs` = action token -> frequency (normalised to sum 1). `best_token` = the most frequent action (first on ties), `best_freq` its frequency,
`freq` the chosen action's (0 when it is not an option). `grade = "best"` if `freq >= BEST_SHARE (0.5) x best_freq`, `"mixed"` if
`>= MIXED_SHARE (0.1) x best_freq`, else `"blunder"`. Postflop: hero's exact combo row (`strategy[combo]`) at the node; preflop: the GTO
Wizard class frequencies of the legal options, else the pure chart (as before; source order changed by stream T6 below). The RNG buckets
are unchanged (same `options[].freq`).

Trainer state, exact changes:
- `feedback` (shape identical preflop and postflop):
  - `grade`: `"best" | "mixed" | "blunder" | "none"` (was `"ev" | "freq" | "none"`). `"none"` = not graded (`note` says why:
    `solver_unavailable`, `multiway_unsolvable`, `off_tree`, `combo_outside_range`).
  - NEW `freq: float | null`: frequency of the chosen action (null when `grade = "none"`).
  - NEW `best_freq: float | null`: frequency of `best`.
  - `best`: the most frequent action (was: max EV postflop). For `combo_outside_range` it is the range summary's most frequent action
    while `grade = "none"`; null on unsolved streets.
  - `freq_error = 1 - freq` (kept, all graded streets; null when ungraded).
  - `loss_bb`: always `null` for texas documents. Only a document with `meta.ev_available = true` still fills it
    (`max(action_ev) - action_ev[chosen]` in bb) and `options[].ev_bb`; the grade stays frequency-based.
  - `options[].ev_bb`: null (same rule as `loss_bb`). `options[].freq` unchanged.
  - REMOVED `gto_best` (there are no exploitative street solves any more).
- `totals`: `{hands, decisions, best, mixed, blunders, blunder_rate|null, preflop_decisions, avg_freq_error|null, hero_net}`.
  `decisions` counts graded decisions only; `blunders` = decisions graded `"blunder"` (was loss > 1 bb);
  `blunder_rate = blunders / decisions`. REMOVED `loss_bb`, `ev_decisions`.
- `rolling`: `{window, hands, rng_match_rate|null, blunder_rate|null, mixed_rate|null, best_rate|null, leaks_top}`. REMOVED `bb_per_100`.
- `source`: `"gto"` only. `POST /api/train/new` with any other source is a 400. Opponents
  (`opponents: [player_id]`) stay: names in `seats`, the picker (`GET /api/train/players`) and archetypes are unchanged, but every villain
  plays the PokerCoaching / GTO Wizard / chart strategy preflop (origin `"pc:..."` / `"gtow:..."` / `"chart"`; the `"profile(<alpha>)"`
  origin is gone) and the solved GTO strategy postflop.
- Library flops: `state.solve.backend = "texas"`; documents whose `meta.backend != "texas"` are never dealt (treated as missing).

`train_decisions.grade` stores `best | mixed | blunder | none`; rows written before Plan 7 keep `ev | freq` and are re-graded from their
`options_json` frequencies and `chosen` when read (rows without frequencies count as ungraded). `loss_bb` stays NULL for texas solves.

`GET /api/train/stats?window=&source=` -> `{window, source, hands, decisions, graded, best, mixed, blunders, best_rate|null, mixed_rate|null,
blunder_rate|null, rng_match_rate|null, rng_decisions, by_street, by_position, by_situation, slices, drill_hands, natural_hands, drills_landed}`.
- Rates are over `graded` decisions (grade best / mixed / blunder); `rng_match_rate` as before (rolled, non-pure decisions).
- Each `by_street` / `by_position` / `by_situation` entry: `{hands, decisions, graded, best, mixed, blunders, best_rate|null, mixed_rate|null,
  blunder_rate|null, rng_match_rate|null}` (`match_rate` renamed `rng_match_rate`).
- `slices[]`: `{from_hand, to_hand, hands, decisions, graded, blunder_rate|null, rng_match_rate|null}`.
- REMOVED everywhere: `loss_bb`, `bb_per_100`, `match_rate`. `pn train stats` prints the same fields.

`GET /api/train/leaks?window=&table=&depth=` -> rows `{spot, label, freq, n, blunders, blunder_rate, expected_blunders_per_100, weight}`,
sorted by `expected_blunders_per_100`, then weight, frequency, spot. REMOVED `loss_per_occ_bb`, `expected_burn_per_100`.
- `n` = graded decisions in the spot, `blunders` = how many of them were blunders.
- `blunder_rate = (blunders + PRIOR_N x global) / (n + PRIOR_N)`, `global = (all blunders + PRIOR_N x PRIOR_BLUNDER_RATE) / (N + PRIOR_N)`,
  `PRIOR_N = 5`, `PRIOR_BLUNDER_RATE = 0.2` (constants in `pn/pn/train/leaks.py`). Preflop and postflop decisions count alike.
- `expected_blunders_per_100 = 100 x freq x blunder_rate`; `weight = (expected_blunders_per_100 + C_EXPLORE x sqrt(ln max(N, 2) / (n + 1))) / sum`,
  `C_EXPLORE = 0.5`. Frequencies, lines and drills are unchanged. `pn train leaks` prints blunder rate and blunders/100.

Root solves: `POST /api/solves/new` returns 400 for `source != "gto"` or any seat with a `player_id` (the "vs <player>" view is gone);
seats with an explicit `range` are still accepted.

Hand review (`pn ev run`, `pn ev show`, `pn ev summary`, `GET /api/hands/{id}/ev`, `GET /api/review/{hero}/ev`):
- `pn ev run` grades with source `"gto"` only (other sources: error). Postflop: one GTO solve (no locks, `review` preset) per heads-up street
  and player; each decision gets `grade(strategy[combo] at the walked node, chosen token)`. Preflop is chart-based and never solved:
  GTO Wizard class frequencies by action kind (`fold | check | call | raise`, bets and all-ins count as `raise`); no cell -> `note =
  "no_preflop_frequencies"`. The note `preflop_root_too_large` and the `off_path_hu` / `no_action_ev` notes are gone; a node without a
  strategy row gives `no_strategy: ...`, a combo with an all-zero row `combo_outside_range`.
- `decision_ev` gains `grade TEXT` (best | mixed | blunder, NULL = not graded), `freq_chosen REAL`, `best_freq REAL` (ALTERed into older
  databases by `pn ev run`). `best` = the most frequent action. `ev_chosen`, `ev_best`, `loss` are NULL for texas documents (filled only
  with `meta.ev_available = true`). Preflop rows also set `gto_freq_chosen = freq_chosen`. Rows from before Plan 7 with `grade` NULL are
  graded again by the next `pn ev run` (unless they only carry a note).
- `/api/hands/{id}/ev?source=gto` (default now `gto`; other `SOURCES` stay readable for old rows) -> rows as before plus `grade`,
  `freq_chosen`, `best_freq` (null on old rows); `ev_chosen_bb`, `ev_best_bb`, `loss_bb` stay, null for texas rows.
- `/api/review/{hero}/ev?source=gto&include_unmodeled=` -> `{player_ids, source, decisions, graded, null, hands, best, mixed, blunders,
  blunder_rate|null, blunders_per_100|null, by_street: [{street, n, graded, best, mixed, blunders}], by_spot: [{spot, n, graded, best,
  mixed, blunders}], top_hands: [{hand_id, hand_no, blunders, decisions}] (hands with >= 1 blunder, most first, max 10), notes,
  compare: {source: totals}, unmodeled: {decisions, blunders}, include_unmodeled}`. `hands` = hands with a graded decision,
  `blunders_per_100 = 100 x blunders / hands`. REMOVED `total_loss_bb`, `loss_per_100`, every `loss_bb` in groups / top hands / unmodeled.

Preflop source order (stream T6, 2026-10-10; `pn/pn/train/preflop.py` `gto_decision`, used for hero grading AND villain actions):
1. **PokerCoaching** 100bb charts (`config/preflop_charts/pokercoaching_100bb.txt`, 6-max / 8-max only) on clean lines (no callers, no
   limpers except the SB limp below): `rfi` (raise = `open <P>`, limp = `limp SB`, else fold), `vs_open` (`3bet` / `call <P> vs <O>`),
   `vs_3bet` (`4bet` / `call <P> vs3bet <T>`), `limp_vs_raise` (SB limped, BB raised: `3bet` / `call SB limp_vs_raise BB`). Pure: the chosen
   action's frequency is 1 or 0, so `options[].freq` are 1/0 and the RNG buckets collapse to one bucket (`pure = true`).
2. **GTO Wizard** class frequencies where PokerCoaching has no chart: vs 4-bet and later, cold 4-bets / cold calls, squeezes (callers),
   limped pots incl. BB vs SB limp (`vs_limp|SB|BB`), and any key missing from the PokerCoaching file. Heads-up has no GTO Wizard spots.
3. The hand-written charts (`chart_dist`) otherwise (heads-up, multi-limper pots, ...).
Raise sizes are unchanged (GTO Wizard size on an exact GTO Wizard line, else the formula).

SB first in: the engine supports limps (an SB `call` with no raise; BB then checks or raises), so the SB plays raise / limp / fold from
PokerCoaching (AA, KK and AKo limp; folding them is a blunder). BB vs the limp grades from GTO Wizard `vs_limp|SB|BB`; SB facing the BB's
raise after limping grades from PokerCoaching `limp_vs_raise` (new situation kind; `situation` key null, no GTO Wizard spot).

- NEW `feedback.preflop_source: "pokercoaching" | "gtowizard" | "chart" | null` (null on postflop decisions): which source graded the
  preflop decision. `feedback.origin` = `"pc:<chart key>"` (e.g. `"pc:open BTN"`), `"gtow:<depth>|<key>"` or `"chart"`.
- `feedback.grid_ref` for preflop decisions: `{kind: "pc", ...}` for `pokercoaching` ones (stream T7 below), `{kind: "gtow", key}` for
  `gtowizard` ones, null for `chart` ones.
- Hand review (`pn ev run`) preflop rows use the same order: PokerCoaching (table from the hand's player count; situations from
  `pn.ev.decisions`, which now also emits `("limp_vs_raise", "SB", "BB")`), else GTO Wizard class frequencies, else `note =
  "no_preflop_frequencies"`. `decision_ev.backend` names the source on preflop rows: `pokercoaching` | `gtowizard` (no schema change).
  Rows graded earlier keep their GTO Wizard grade until `pn ev run --force`.

Library fill (`pn library fill|status`): unchanged structure; `--backend` defaults to (and only accepts) `texas`; a cached document of
another backend is not recorded and is solved again through `SolveCache.solve`. Matchup order uses expected blunders instead of EV burn:
`burn = 100 x P(line) x mean blunder_rate of the pot type's postflop leak rows` (`PRIOR_BLUNDER_RATE = 0.2` without any).

## Plan 7 additions (2026-10-10, stream T7): PokerCoaching chart grid in the trainer, PokerCoaching-first leak frequencies

Trainer feedback of a preflop decision graded by PokerCoaching (`preflop_source = "pokercoaching"`, `origin = "pc:<chart key>"`):
- NEW `feedback.grid_ref = {kind: "pc", key, table, situation, position, other}`: `key` = the chart key of the origin (the first chart of
  the spot, e.g. `"open LJ"`, `"3bet BB vs BTN"`, `"4bet CO vs3bet BTN"`, `"3bet SB limp_vs_raise BB"`); `table` = `"6max" | "8max"`;
  `situation` = `rfi | vs_open | vs_3bet | limp_vs_raise`; `position` = hero's seat; `other` = opener (vs_open), 3-bettor (vs_3bet),
  `"BB"` (limp_vs_raise) or null (rfi). Built by `pn.train.preflop.pc_ref`. Other kinds unchanged: `{kind: "gtow", key}` (GTO Wizard
  graded), `{kind: "solve", name, node_id}` (postflop), null (hand-written chart or ungraded).
- NEW `GET /api/charts/pc?table=&situation=&position=&other=` -> the whole PokerCoaching spot in the `GET /api/gtow/spot` shape:
  `{key, depth: 100, kind: situation, positions: [other?, position], actor, actions: [label], labels: {R|C|F: label}, raise_to_bb: null,
  source: "pokercoaching", cells: {class: {code: share}}, summary: {code: combo share}}`. Codes as `pn.train.preflop.PC_STEPS`: `R` =
  open / 3-bet / 4-bet, `C` = limp (SB rfi) / call, `F` = fold; pure, so each class has one code at 1.0. Labels: rfi Raise / Limp / Fold,
  vs_open and limp_vs_raise 3-bet / Call / Fold, vs_3bet 4-bet / Call / Fold. 404 when PokerCoaching has no chart for the spot.
- UI (`views/train.js`): the feedback panel renders `pc` and `gtow` refs with the charts view's renderer (`spotGrid` in
  `views/charts.js`), hero's class outlined; a `pc` grid is shown open. A preflop decision without a ref shows one line naming its source
  ("graded by the hand-written charts").

Leak / drill frequencies (`pn/pn/train/leaks.py` `gtow_walk`, overrides "GTO Wizard chain frequencies" above): the action shares of the
chains are PokerCoaching first, in the solver's seat-range order (`pn.solve.sources.seat_range`): `pc_spot_table()` replaces the GTO
Wizard shares of `rfi|<P>` (SB `call` = limp), `vs_open|<O>|<P>` and `vs_3bet|<O>|<T>` (over the opener's PokerCoaching open range) for
every 6-max spot PokerCoaching charts; a pure chart gives each class frequency 1 for its charted action and 0 otherwise, so a share is the
charted combos over the reaching combos. GTO Wizard shares stay for `vs_limp`, `cold`, `vs_4bet` and later, and any spot without a
PokerCoaching chart. `gtow_walk(..., pokercoaching=False)` reads GTO Wizard only. The `FreqMap` shape, spot keys, lines and drill steps
(folding villains' hands still come from the GTO Wizard class shares of their steps) are unchanged.

## Plan 13 additions (2026-10-15, stream A): per-hand feedback, drill pins, retry / rewind

Backend: `pn/pn/train/session.py` (the state records `Config`, `Hand`, `Session` ... moved to `pn/pn/train/model.py`, still
importable from `pn.train`), routes in `pn/pn/web/train_routes.py`.

State gains:
- `hand_feedback: [feedback, ...]`: every graded feedback object of the current hand, oldest first, retries included (same shape
  as `feedback`). `feedback` stays the latest one (`feedback == hand_feedback[-1]` after an action). `[]` after `next`. A rewind
  keeps the list (the abandoned attempt stays visible; the replayed decisions are appended).
- `feedback.retry: bool` (also on each `hand_feedback` entry): true for a decision made during a replay.
- `replay: bool`: the hand was rewound by `retry` (false again after `next`).
- `pinned_drill: {spot, label, dealt} | null`: the session's pinned drill spot; `label` = the leak table's label
  (`pn.train.spots.label`, the spot id when it cannot be labelled); `dealt: bool` = the current hand was dealt as a drill of
  that spot (false on the hand that was running when the pin was set, and on a pinned deal that fell back to a natural hand).

Drill pinning:
- `Config.drill_spot: str | null` (default null); `POST /api/train/new` accepts `drill_spot`. While set, every deal is a drill of
  that spot (the `drill_share` draw is skipped; a deal that still fails falls back to a natural hand, logged).
- `POST /api/train/{sid}/drill` body `{spot: str | null}` -> state. Pins (or with null unpins) from the NEXT deal on; the current
  hand is untouched. `spot` is a `spot` id from `GET /api/train/leaks`.
- 400 (here and on `/new`) when the spot id is malformed, the trainer has no leak model, or the spot has no dealable line at the
  session's table / depth (`Trainer._drill_line` is None, e.g. a postflop spot whose heads-up flops have no solved board). A
  refused pin leaves the previous pin in place. 404 unknown sid.

Retry / rewind:
- `POST /api/train/{sid}/retry` -> state rewound to just before hero's latest decision this hand. The server keeps the `Hand`
  (immutable) before each hero decision; `next` clears them. Works while the hand runs and after it ended (until `next`).
- 400 when there is no hero decision left to rewind this hand, or while a street solve is running (`solving`). A solve that
  already finished for the abandoned branch (not yet collected) is dropped and its result ignored. 404 unknown sid.
- The rewound decision gets a fresh RNG roll (the next roll of the session's RNG generator; villains' cards and the deck stay
  those of the snapshot, villain choices draw on from the deal generator).
- After a rewind the hand is a replay (`replay: true`): every later decision is graded (`retry: true`, appended to
  `hand_feedback`) but is NOT added to `totals` and NOT written to `train_decisions` (so never reaches rolling stats or leaks).
  Retry again during a replay rewinds to before the latest remaining decision (each rewind pops one snapshot).
- Counting rule (decided by stream A where the plan was open): rewinding never un-counts anything. The original decisions stay
  in `totals`, and every hand is recorded exactly once, at its first finish:
  - rewound after the hand ended: the hand was already recorded; the replay's finish neither re-records it nor bumps
    `totals.hands` / `hero_net`;
  - rewound while the hand ran (2026-10-15 review fix): the first rewind closes the original attempt as abandoned and records
    it there (`train_hands` row, `totals.hands` + 1, `hero_net` and `net_bb` = minus hero's chips put in so far, like a fold
    at that point), with only the original decisions as its `train_decisions` rows and its own `drill_landed`. The replay's
    finish records nothing, so a runout seen in the abandoned branch can never improve the counted net.

## Plan 14 additions (2026-10-15, backend stream): spot importance ("Spot importance - V3")

Backend: `pn/pn/train/importance.py` (formula, tables), `pn/pn/train/textures.py` (flop textures), `pn/pn/train/planner.py`
(drill planning, a mixin of `Trainer`), `pn/pn/train/leaks.py` (`Leaks.importance`), routes in `pn/pn/web/train_routes.py`.

Formula: `priority = freq x pot x (1 - score)^p / max(...)` (the top row is 1; every row 0 when all raw values are 0).
- `score` = trainer accuracy over the window: (best + 0.5 x mixed) / graded, shrunk toward the global score with prior weight
  PRIOR_N = 5: `(sum + 5 x global) / (n + 5)`. The global score is itself `(sum + 5 x 0.7) / (N + 5)` (0.7 = the sheet's
  default GTO Wizard score), so a spot never played is ranked by freq x pot alone. Ungraded decisions do not count.
- `p` (practice parameter, integers 1..5): formations and spot rows use `p_form` (default 3), textures `p_tex` (default 1).
- `pot` (bb): `start` = the pot when hero first decides in the spot, replayed from the spot's line with the trainer's sizes
  (preflop spots: before hero acts, e.g. 1.5 rfi, 4.0 BTN vs CO open; postflop spots and formations: the pot at the start of
  the flop of the spot's highest-reach line, e.g. 5.5 BTN vs BB SRP, 24 BTN vs SB 3BP); a line that does not replay falls back
  to 1.5 preflop or the pot type's prior (limped 2, srp 5.5, 3bp 22, 4bp+ 45). `avg` = the mean pot at hero's recorded
  decisions in that row (`train_decisions.pot_bb`), falling back to the start pot when none is recorded.
- Drill weights: `weight = normalised(priority + 0.05 x sqrt(ln max(N, 2) / (n + 1)))` (N = graded decisions over the rows),
  so never-played rows are still dealt. The weight is not shown as priority.

`GET /api/train/leaks?window=1000&table=6&depth=100&p_form=3&p_tex=1&pot=start` -> spot rows sorted by `-priority` (then
weight, freq, spot). Existing fields stay (`spot, label, freq, n, blunders, blunder_rate, expected_blunders_per_100, weight`;
the blunder fields are computed as in Plan 7 and no longer drive the order; `weight` is the importance drill weight above).
New fields: `priority` (0..1, top = 1), `score` (0..1), `pot_bb`, `formation` (string or null). `n` = graded decisions in
the window. 400 for p outside 1..5, `pot` not start | avg, window < 1, bad table / depth (a non-integer p is a 422 from
validation). `p_tex` is accepted and validated but does not change spot rows.
- `formation` of a preflop spot: the heads-up flop it leads to when hero calls: vs_open -> SRP, vs_3bet / cold -> 3BP,
  vs_4bet+ -> 4BP, between hero and the `vs` position; null for rfi and vs_limp (no known opponent). A postflop spot key
  carries no positions, so its formation is that of its highest-reach line (the formation its drills are dealt in): every
  `post|srp|ip|...` row is labelled e.g. "BTN vs BB - SRP".
- Label format (the sheet's): `<first to act preflop> vs <second> - <SRP|3BP|4BP|Limped>`, e.g. "CO vs BTN - 3BP",
  "SB vs BB - Limped" (4BP covers 4-bet+ pots).

`GET /api/train/importance?window&table&depth&p_form&p_tex&pot` -> `{formations, textures, params: {p_form, p_tex, pot}}`, each
list sorted by `-priority` and normalised to max 1 within the list. Same 400s.
- `formations: [{formation, pot_type: "SRP"|"3BP"|"4BP"|"Limped", positions: [first, second], freq, pot_bb, score, n,
  priority, spots: [spot ids]}]`: one per heads-up flop formation the preflop map reaches (GTO Wizard chains or the library
  root), `freq` = P(hero sees that heads-up flop) per hand with hero's seat uniform (2 x reach / seats; the formations' freqs
  sum to the postflop `post|<pot>|<side>` frequencies). `score` / `n` / avg pot come from postflop decisions recorded with that
  formation (`train_decisions.formation`, written from Plan 14 on; older rows are not counted). `spots` = the spot rows whose
  `formation` is this one, by priority (postflop spot rows only in the top formation of their pot type: to drill a formation
  itself, pin it with `POST /drill {formation}`).
- `textures: [{texture, details, freq, pot_bb, score, n, priority, weight}]`: the eight textures. `freq` = exact share of the
  22,100 flops (`pn.train.textures.FLOP_SHARE`), `score` / `n` / avg pot from postflop decisions with a recorded board (by the
  board's flop), start pot = the formations' freq-weighted mean start pot (the same for every texture, as on the sheet); in
  `avg` mode a texture without a recorded pot takes the mean recorded pot over all textures (the start pot without any). Avg
  pots everywhere count graded decisions only.
  `weight` = the texture draw weight of postflop drills (p_tex).

Textures (`pn.train.textures.texture(board)`; first three cards): Paired (paired or tripled) first, then Monotone (a monotone
flop cannot be paired), then the high card: Ace / King / Queen / Jack / Ten, and Low = 4-9 high (the lowest unpaired flop is
4-high: a 2- or 3-high flop is always paired, so Low is every unpaired, not monotone flop below a ten). Exact counts / shares
over C(52,3) = 22,100: Ace 3960 (.1792), Paired 3796 (.1718), Low 3360 (.1520), King 3300 (.1493), Queen 2700 (.1222),
Jack 2160 (.0977), Ten 1680 (.0760), Monotone 1144 (.0518); sum 1. The sheet's values (.179 .172 .152 .149 .122 .098 .076
.052) are these rounded to three places; no difference beyond rounding.

Sessions:
- `Config` / `POST /api/train/new` accept `p_form` (1..5, default 3), `p_tex` (1..5, default 1), `pot_mode` ("start" | "avg",
  default "start"), `drill_texture` (a texture name or null) and `drill_formation` (a formation label or null; not with
  `drill_spot`); 400 outside. Spot drill weights use p_form / pot_mode.
- `POST /api/train/{sid}/params` body `{p_form?, p_tex?, pot_mode?}` (omitted fields stay) -> state; applies from the next deal
  on (the current hand is untouched). 400 out of range, 404 unknown sid. State gains `params: {p_form, p_tex, pot_mode}`.
- Postflop drills (unpinned texture) draw the flop texture by the texture `weight` among the textures their line's matchup has
  solved boards of, then a solved board of that texture (`flop_library.pick_board` over the matchup's boards of that texture).
- `POST /api/train/{sid}/drill` body `{spot: str | null, texture?: str | null, formation?: str | null}` (omitted fields are
  null: each call sets the whole pin) -> state. `formation` (a label of `GET /importance` formations; 400 with a `spot`, for
  a malformed label, or when the preflop map does not reach it with a solved flop board) deals every hand as a drill of that
  formation's highest-reach line, aimed at hero's first flop decision (target spot `post|<pot>|<ip|oop>|flop|first`), on
  flops of the pinned texture when one is given. `texture` pins postflop drills to flops of that texture from the next deal
  on:
  - with a postflop `spot`: that spot's drills on flops of the texture; with a preflop spot: 400 (a texture only shapes flops);
  - alone (`spot` and `formation` null): every hand drills a dealable postflop spot drawn by spot `weight` among those whose
    matchup has a solved board of the texture (the `drill_share` draw is skipped, as for a pinned spot);
  - no solved board of the texture for the spot (or for any dealable postflop spot): the drill is dealt on any solved flop and
    a warning is logged (`pn.train.session` logger: "no solved <Texture> flop ...");
  - 400 for an unknown texture or without a leak model; a refused pin leaves the previous pin in place.
- `pinned_drill: {spot, label, dealt, texture, formation} | null`: non-null while a spot, a formation or a texture is pinned.
  `spot` null for a formation or texture pin; `label` = the spot's label, else the formation label, else "<Texture> flops";
  `dealt` = the current hand was dealt as a drill of the pinned spot / formation (when one is pinned) aimed at the pinned
  texture (when one is pinned): false after a texture fallback. It stays true when the hand then leaves the drill's line (hero
  deviates preflop, the pot goes multiway, or the target street passes): such a hand plays on naturally on any flop and is
  told apart by `drill` = null (the web chip says "this hand left the drill line").

Storage (additive; older rows stay NULL): `train_decisions.board TEXT` (board cards at the decision, e.g. "Kd7s2c3h"; NULL
preflop), `train_decisions.pot_bb REAL` (pot at the decision in bb, every street; needed for the "avg" pot) and
`train_decisions.formation TEXT` (the heads-up formation on postflop streets; NULL preflop and multiway; needed to score
formations: a postflop spot key has no positions). Added to `schema.sql` for new databases and by `pn.train.stats.ensure`
(ALTER TABLE ... ADD COLUMN, before every read or write; a concurrent reader migrating first is not an error) for existing
ones. Replay decisions (Plan 13 retry) are still never written.

## Plan 15 additions (2026-10-15, backend stream): performance per spot (GTO closeness)

Backend: `pn/pn/train/performance.py` (pure functions over a sqlite connection), route in `pn/pn/web/train_routes.py`.
Trainer decisions only (real-game decision_ev is not used).

`GET /api/train/performance?window=&table=&depth=&since=` -> 200 (empty DB: empty `rows` / `trend`, null scores).
- Filters (empty = none): `table` 6 | 8, `depth` 1..2^31-1, `since` ISO date or datetime (played_at >= since, compared in
  UTC; a naive value is taken as UTC), then `window` = the newest N (1..2^31-1) of the remaining hands (`all` or empty =
  every hand). 400 on a non-integer / out-of-range value or an unparseable `since`. Responses are gzipped on request.
- Decision kinds: `graded` = grade best | mixed | blunder (Plan 7); `legacy` = grade `ev` / `freq` (pre-Plan 7, not
  re-graded here); `ungraded` = anything else (grade "none" with a note: multiway, off tree, combo outside range, no
  solve). Only graded decisions enter best / mixed / blunders / closeness / recent / trend; every decision counts in `n`.
- Closeness of a graded decision = freq(chosen) / max option freq from options_json (1 = the solver's top action, 0 = never
  taken or not an option; capped at 1); a decision without usable frequencies is graded but has no closeness. Means over
  the decisions that have one, 4 decimals. `rng_match` = share of matched_rng over decisions with a roll in non-pure spots
  (null without any), of any kind; `rng_n` = that denominator (weight it by `rng_n` when merging rows).

```
{totals: {decisions, graded, legacy, ungraded, best, mixed, blunders, closeness, rng_match, rng_n},
 rows: [{spot, label, street, position, family, pot_type, n, graded, legacy, best, mixed, blunders, closeness,
         rng_match, rng_n, last_played}],
 axes: {positions: [...], families: [{id, label, street, pot_type}]},
 trend: [{bucket: "<from>-<to>", from, to, date, n, closeness, blunder_rate}]}
```
- A row = one (spot, hero position) pair (`position` = train_decisions.position). Rows are sorted by `axes.positions` order,
  then family column order, then spot.
- `GET /api/train/performance/recent?position=&family=&window=&table=&depth=&since=` (position and family required, else
  400; same filters) -> `{position, family, recent: [{played_at, hand_class, chosen, chosen_freq, best, best_freq, grade,
  closeness, spot, label}]}` = the cell's newest 20 graded decisions over all its spots, newest first (`best` = the stored
  best token; freqs from options_json, null when absent). Served per cell to keep the list response small.
- `family` (heatmap column; `pn.train.spots` kinds only): preflop = the faced kind `rfi | vs_limp | vs_open | vs_3bet |
  vs_4bet+ | cold` (labels RFI, vs limp, vs open, vs 3-bet, vs 4-bet+, 3-bet cold), `pot_type` null; postflop =
  `<pot>|<street>|<first|facing_bet|facing_raise>` with IP / OOP collapsed (the row is hero's position), labels like
  "SRP flop first", "3BP turn vs bet", "4BP+ river vs raise", `pot_type` = limped | srp | 3bp | 4bp+. A NULL or
  unparseable spot, or one whose faced / pot / street / situation is not one of those -> family `other` (label "Other", `street` null in axes; the row keeps the decision's street).
  Drillable (POST /api/train/new {drill_spot}) = any row whose family is not `other`.
- `axes.families` = the families present, ordered preflop (faced order above), flop, turn, river (each by pot limped,
  srp, 3bp, 4bp+, then first, facing_bet, facing_raise), `other` last. `axes.positions` = the seats of `table`
  (6: LJ HJ CO BTN SB BB; 8: UTG UTG1 LJ HJ CO BTN SB BB) or, without the filter, the union of the seats of the table sizes
  in the filtered hands (6-max seats when there are none), in preflop order; any other position present is appended.
- `trend`: graded decisions in play order (played_at, then seq) in consecutive buckets of max(5, ceil(graded / 20))
  decisions (at most 20 buckets, the last may be smaller), oldest first; `date` = the bucket's last played_at date (UTC).

## Plan 16 additions (2026-10-15, stream X): 8-max / 200bb sessions, flop index aliases, dealable drills
- Flop index aliases (`pn.train.flop_library.FlopIndex`, `pn.train.library_alias`): an index entry may be
  `{label, pot, positions, depth, table, alias_of: <mid>}` with no `boards`. `boards()`, `accuracy()` and `pick_board` of
  an alias read the target's boards (one level; an alias of an alias is refused), `record()` under an alias raises.
  `pn library alias --table 8 --depth <100|200> [--sample N] [--dry-run]` aliases every 8-max matchup whose preflop
  line has no UTG / UTG1 action (85 per depth) to the 6-max matchup of the same line and depth, after checking that the
  8-max flop key of `--sample` indexed boards of the target (all with 0) equals the recorded document key (probe boards
  against the 6-max key while the target has none). One refusal writes nothing; a matchup with boards of its own is
  left alone; idempotent. `pn library status` adds, per table/depth (6/8 x 100/200), solved (subset / full by the
  entry's `subset`), aliased (with boards), missing (limped pots outside 6-max 100bb flagged as skipped on purpose) and
  a state column per matchup row.
- 8-max and 200bb sessions deal library flops like 6-max 100bb: an aliased 8-max line deals and grades the 6-max
  document (same key). No board -> the hand still ends at the flop (`solve.note = "no_library_solve"`), now with
  `solve.detail` (new key, null otherwise): "limped pots are not solved at <t>-max <d>bb (Plan 16): <label>" for a limped
  pot outside 6-max 100bb, else "no solved flop for <label> yet". The trainer page shows "limped pot not solved, ran out"
  for the first.
- Leaks / importance / drills at table 8 (`leaks.gtow_walk`, no library root exists): the GTO Wizard 6-max chain walk
  over the 8 seats, hero seat uniform over 8. UTG / UTG1 keys (`rfi|EP:UTG`, `vs_open|EP:UTG|<seat>`,
  `vs_3bet|EP:UTG1|<seat>` ...) come from the PokerCoaching 9-handed charts shifted one seat (Plan 16 P1) and their
  drill steps sample hands from those charts (`Step.src = ("pc", (table, kind, position, other), codes)`). Keys without a
  chart (cold calls of an EP open's 3-bet, EP vs 4-bet, EP limps) count as folds / zero reach. With UTG and UTG1 folded
  the chains equal the 6-max ones times P(both fold) x 6/8.
- `GET /api/train/dealable?table=&depth=&spot=<id>&spot=...` -> `{table, depth, dealable: {spot: bool}}`: true when
  `POST /api/train/new` with that `drill_spot` would deal (same check, `Trainer.dealable`); 400 on a bad table / depth;
  all false without a leak model. /train/performance shows "Drill this spot" only for dealable rows (shown when the
  check itself fails).
