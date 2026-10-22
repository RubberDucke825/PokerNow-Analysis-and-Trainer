# Trainer: spots, drills and grading

This page explains how the web trainer deals a hand, which spots it chooses to drill, and
how it grades your answers. The API contract with every field is
[contracts/trainer.md](../contracts/trainer.md). Preflop ranges are explained in
[preflop-charts.md](preflop-charts.md). What gets saved after each hand is in
[session-logs.md](session-logs.md).

The code is in `pn/pn/train/`. The main file is `session.py`; the state records are in
`model.py`.

## Session settings

A session is created with a `Config` (`pn/pn/train/model.py:35-89`). The web UI sends it to
`POST /api/train/new`.

| Field | Default | Allowed | Line |
|---|---|---|---|
| `table` | 6 | 6 or 8 | `model.py:37` (`TABLE_SEATS`, `pn/pn/solve/request.py:124-127`) |
| `depth` | 100 | 100 or 200 bb | `model.py:23,38` |
| `hero_seat` | random | a seat name | `model.py:39` |
| `source` | `gto` | `gto` only | `model.py:24,41` |
| `rng_enabled` | true | | `model.py:43` |
| `window` | 1000 hands | >= 1 | `model.py:44` |
| `drill_share` | 0.8 | 0..1 | `model.py:45` |
| `drill_spot`, `drill_texture`, `drill_formation` | none | pins, see below | `model.py:46-48` |
| `p_form`, `p_tex` | 3, 1 | integers 1..5 | `model.py:49-50`, `importance.py:34` |
| `pot_mode` | `start` | `start` or `avg` | `model.py:51` |

Blinds are 50 / 100 cents (`session.py:77-78`). Stacks reset to `depth` every hand. The
button moves one seat per hand (`session.py:178`). Every bot plays the chart / solver
strategy; there are no opponent-specific bots (`session.py:14-15`).

## One hand, step by step

The loop is `Trainer._advance` (`pn/pn/train/session.py:262-281`). It lets bots act until
it is your turn, a solve is running, or the hand is over.

1. **Deal.** With probability `drill_share` the hand is a drill (see "Choosing spots");
   otherwise a natural deal (`_deal_hand`, lines 228-239; roll at `planner.py:58`).
2. **Preflop.** Bots act from the preflop charts (`pre.villain_action`,
   `pn/pn/train/preflop.py:362-374`). Bots keep pots heads-up: they do not limp, cold-call
   into a third player or squeeze (`heads_up_dist`, lines 393-414). You may still make the
   pot multiway yourself.
3. **Flop.** A flop is never solved live. If exactly two players are left, the trainer looks
   up the pot's *matchup* (positions, pot type, depth; `flop_library.matchup_of`,
   `pn/pn/train/flop_library.py:132-138`) in the flop index `hu_flop_library.json`, and
   draws one solved board by its weight (`pick_board`, lines 347-363). The board is dealt
   under a suit permutation that avoids the live hole cards. The cached solve becomes the
   flop's tree (`_library_flop`, `session.py:416-428`).
   - **No solved board** for the matchup: the hand ends at the flop with solve note
     `no_library_solve` (`session.py:86, 419-424`). The rest of the board is run out and the
     pot settled at showdown (`_finish`, lines 511-536). A fresh clone has only the
     60-board sample for one matchup (after `./poker.sh quickstart`), so other heads-up
     flops end this way until you add boards.
   - **Three or more players** on any postflop street: no solve for the rest of the hand,
     note `multiway` (`session.py:374-378`).
4. **Turn and river.** The next street is lifted from the leaf of the previous street's tree
   (`_lift`, `session.py:642-648`) and solved live with the `trainer` preset
   (`session.py:403`): at most 300 iterations, stop at 0.5% of the pot, 10 s nominal
   (`pn/pn/solve/request.py:46-47`). The runner kills the solver 5 s after its time limit
   (`pn/pn/train/postflop.py:23,34-40`). If the tree has no leaf to lift from, note
   `off_tree` (`session.py:389-391`).
5. **Fallback.** If a street's solve fails, or the hand leaves the solved tree, play goes on
   with a fixed policy (`session.py:443-445, 344-349`). Your options become fold / check /
   call, bet 33% or 75% pot (raise 100% when facing a bet), all-in
   (`postflop.py:25-27, 202-224`). Fallback bots check when free and call a bet of at most
   half the pot, else fold (`postflop.py:227-231`). Fallback decisions are not graded.
6. **End.** The hand is settled, counted in the totals and written to the database
   (`_finish` / `_record`, `session.py:511-559`).

While a solve runs, `GET /api/train/{sid}` returns `solving: true`; poll it.

## Grading

Every decision gets one of four grades (`pn/pn/train/grading.py:1-63`):

| Grade | Rule |
|---|---|
| `best` | your action's frequency >= 0.5 x the most frequent action's frequency (`BEST_SHARE`, line 16) |
| `mixed` | >= 0.1 x the most frequent action's frequency (`MIXED_SHARE`, line 17) |
| `blunder` | below that |
| `none` | no frequencies to compare with (line 20) |

The frequencies come from:

- **preflop:** the chart decision for your hand *class* (`pre.hero_grade`,
  `pn/pn/train/preflop.py:417-437`). PokerCoaching charts are pure, so every action is
  1 or 0 there; GTO Wizard frequencies can be mixed.
- **postflop:** the solver's strategy for your exact combo at the current node
  (`post.hero_grade`, `pn/pn/train/postflop.py:171-196`).

A decision is `none` when it is multiway (`multiway_unsolvable`), off the tree (`off_tree`),
the solver was unavailable (`solver_unavailable`) (`session.py:483-489`), or your combo is
outside the solved range (`combo_outside_range`, `postflop.py:28,195`).

`loss_bb` is the EV you gave up, in big blinds: best action EV minus your action's EV
(`postflop.py:182-184`). It is only filled when the solve document carries EVs
(`meta.ev_available`). TexasSolver documents do not, and preflop never has EVs
(`preflop.py:436`). So in normal use `loss_bb` is always null and grading is by frequency
only.

### The RNG roll

With `rng_enabled`, the trainer rolls 1..100 before each of your decisions
(`session.py:278-279`). The strategy for your hand is laid over 1..100 in order fold <
check < call < bets by size < all-in (`pn/pn/train/rng.py:1-38`). The bucket that holds the
roll is `rng_action`; `matched_rng` says whether you took it. If one action has at least
99% of the weight (`PURE_FREQ`, `rng.py:10`), the spot is `pure` and `rng_action` is that
action whatever the roll (`rng.py:51-62`). The roll changes nothing about the grade; it is a
separate "did you randomise correctly" measure.

## Choosing spots (drills)

### Spot ids

A spot is where hero decides (`pn/pn/train/spots.py:1-13`):

- preflop `pf|<pos>|<faced>|<vs>`, faced = `rfi`, `vs_limp`, `vs_open`, `vs_3bet`,
  `vs_4bet+`, `cold`;
- postflop `post|<pot>|<ip|oop>|<street>|<first|facing_bet|facing_raise>`, pot = `limped`,
  `srp`, `3bp`, `4bp+`.

A *formation* is a heads-up flop such as `BTN vs BB - SRP` (`importance.py:135-137`).
Textures are the eight classes Ace, Paired, Low, King, Queen, Jack, Ten, Monotone
(`pn/pn/train/textures.py:18`).

### Importance

Each spot, formation and texture gets a priority (`pn/pn/train/importance.py:1-14`):

```
priority = freq x pot_bb x (1 - score)^p        normalised so the top row is 1   (lines 81-88)
```

| Term | Meaning | Line |
|---|---|---|
| `freq` | expected times per hand hero decides in this spot | `leaks.py:1-13, 690-710` |
| `pot_bb` | `start`: pot when hero first decides, replayed with the trainer's sizes; `avg`: mean recorded pot | `importance.py:7-10, 119-122, 185-202` |
| `score` | your accuracy: best 1, mixed 0.5, blunder 0, ungraded skipped | `importance.py:46, 67-69` |
| shrinkage | `score = (sum + PRIOR_N x g) / (n + PRIOR_N)`, `PRIOR_N = 5` | `importance.py:36, 77-78` |
| `g` (global score) | all your graded decisions, shrunk toward `PRIOR_SCORE = 0.7` | `importance.py:38, 72-74` |
| `p` | `p_form` (default 3) for spots and formations, `p_tex` (default 1) for textures | `importance.py:34` |

A spot never played has `score = g`, so it ranks by `freq x pot` alone.

`freq` comes, in order of preference, from the preflop library root of the table and depth
(reach of each hero node), else GTO Wizard frequency chains, then for postflop spots the
conditional frequencies from solved street documents in the solve cache, then your own
natural hands, then a fallback of 1.0 for "flop, first to act" (`leaks.py:1-13, 690-710`).
Only pots that reach the flop heads-up count.

The drawing weight adds an exploration bonus so unplayed rows still come up
(`importance.py:91-96`):

```
weight = normalised( priority + C_EXPLORE x sqrt( ln N / (n + 1) ) ),   C_EXPLORE = 0.05
```

`N` is the total graded count over the rows (at least 2), `n` the row's count. Your counts
come from the newest `window` hands at this table size and depth (`leaks.py:502-515`).

### Dealing a drill

`DrillPlanner._plan` (`pn/pn/train/planner.py:53-88`):

1. Draw a spot by weight (`_pick_spot`, lines 104-114), or use the pin.
2. Find a line into it that bots can play heads-up (`heads_up_line`, lines 256-269). For a
   postflop spot the line must also have at least one solved flop board (lines 212-224).
3. Seat hero in the line's position and sample everyone's hole cards from the line's
   ranges; for postflop drills, from the ranges of one solved board drawn by weight
   (lines 226-240).
4. For a postflop drill, draw the flop texture by texture weight among the textures that
   matchup has solved boards of (lines 138-153).
5. Bots are steered along the line (`session.py:303-333`; `drills.postflop_token`). If you
   step off the line, the hand plays on naturally (`session.py:473-478`).

If any step fails, the hand is dealt naturally and a warning is logged (`planner.py:85-87`).

## Adding spots

### (a) New preflop lines

Preflop spots are chart entries. Add or change entries in `config/preflop_charts/` (see
[preflop-charts.md](preflop-charts.md)). The trainer has no separate spot list: any line the
charts and bots can reach is a spot. Note that bots never limp or play multiway, so such
spots are only reached when you create them yourself.

### (b) New flop boards

Postflop drills need solved flop boards. Two ways:

- Solve them: `pn library fill` (`pn/pn/cli/solve_cmds.py:271-305`,
  `pn/pn/train/library_fill.py`). It solves heads-up flops with TexasSolver and the
  `library` preset (400 iterations, 0.3% pot target, one flop bet size, two bets per street;
  `pn/pn/solve/request.py:50-51`). Order: matchups by expected blunders per 100 hands, then
  boards spread across matchups (`library_fill.py:6-9`). It is resumable.

  | Option | Default | Meaning |
  |---|---|---|
  | `--configs N` | 6 | top N matchups by expected burn (0 = all) |
  | `--flops N\|all` | 50 | texture-stratified canonical flops per matchup (1,755 = all) |
  | `--subset N` | - | the weighted N-flop subset instead of `--flops` |
  | `--table`, `--depth` / `--lib 6x100` | 6, 100 | which library; `--lib` is repeatable |
  | `--accuracy PCT` | preset's | accuracy passes in % of pot, repeatable, coarse to fine |
  | `--depth-accuracy DEPTH=PCT` | - | per-depth accuracy |
  | `--pots` | all | `limped`, `srp`, `3bp`, `4bp+` |
  | `--dry-run` | | print the order, solve nothing |
  | `--cached-only` | | index documents already in the cache |
  | `--shard i/n`, `--reverse` | | split the work over machines |

  The preset must stay `library` and the backend `texas`; the command refuses others, and
  refuses to run without the GTO Wizard frequency file (`solve_cmds.py:326-333`). Each flop
  takes minutes. `pn library status` shows solved / planned boards; `pn library reindex`
  and `pn library alias` maintain the index.

  ```sh
  uv run --project pn pn library fill --configs 2 --flops 5 --dry-run
  nohup uv run --project pn pn library fill --configs 2 --flops 5 > data/fill.log 2>&1 &
  ```

- Unpack a library pack made by `tools/library_pack.py`, or use `./poker.sh quickstart`,
  which unpacks the 60-board sample (BTN open, BB call, 6-max 100bb) into
  `data/solves/cache`. Commands: [flop-library.md](flop-library.md#how-to-get-it).

Library documents are only found if their cache keys match. Keys depend on the preflop
ranges and sizes; see "What breaks" in [preflop-charts.md](preflop-charts.md#customising).

### (c) Pins and parameters

| Pin | Effect | Where |
|---|---|---|
| `drill_spot` | every hand drills this spot (ignores `drill_share`) | `model.py:46` |
| `drill_texture` | postflop drills use this flop texture | `model.py:47`, `planner.py:116-153` |
| `drill_formation` | deal this heads-up flop, aimed at hero's first flop decision | `model.py:48`, `planner.py:167-175` |

A spot pin and a formation pin cannot be combined (`model.py:73-75`). A pin that cannot be
dealt at this table and depth is rejected with a 400 (`planner.py:177-197`).
`GET /api/train/dealable` checks a list of spots in advance (`planner.py:199-210`).

## Web API used by the trainer UI

All in `pn/pn/web/train_routes.py`. Amounts are in cents.

| Route | Purpose | Line |
|---|---|---|
| `GET /api/train/players` | DB players for the opponent picker | 108 |
| `POST /api/train/new` | new session from a `Config`; returns state | 119 |
| `GET /api/train/{sid}` | state; also advances bots after a solve | 222 |
| `POST /api/train/{sid}/act` `{token}` | hero action | 243 |
| `POST /api/train/{sid}/next` | next hand | 248 |
| `POST /api/train/{sid}/retry` | rewind to before your last decision (graded, never counted) | 266 |
| `POST /api/train/{sid}/drill` `{spot, texture, formation}` | set or clear pins | 253 |
| `POST /api/train/{sid}/params` `{p_form, p_tex, pot_mode}` | importance parameters | 260 |
| `GET /api/train/{sid}/preflop_line` | the hand's preflop line breakdown | 227 |
| `GET /api/train/stats` | rolling performance | 133 |
| `GET /api/train/leaks`, `/api/train/importance` | spot rows; spots + formations + textures | 160, 168 |
| `GET /api/train/performance`, `/performance/recent` | per-spot GTO closeness | 184, 191 |
| `GET /api/train/dealable` | which spot pins can be dealt | 202 |

Sessions live in memory (at most 32, `session.py:79`) and are lost on restart.

## TODO(confirm) list

1. `contracts/trainer.md` "Trainer" section (lines 8-54) still describes the pre-Plan 7
   feedback (`grade: "ev"|"freq"`, totals with `loss_bb`); the code uses best / mixed /
   blunder / none. Later sections of the contract supersede it, but the first section was
   not updated.
