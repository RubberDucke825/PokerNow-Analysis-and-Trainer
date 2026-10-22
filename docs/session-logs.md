# Digesting trainer logs

Every finished trainer hand is written to SQLite. This page documents the two trainer
tables, what `pn train stats` and `pn train leaks` compute from them, and walks through a
real example of finding a leak with plain SQL.

How hands are dealt and graded is in [trainer-spots.md](trainer-spots.md).

## Where the log is

The trainer writes to the same SQLite file as ingested hands: `--db`, else `PN_DB`, else
`data/pn.sqlite` (`pn/pn/ingest/db.py:16`). The tables are created by
`pn/pn/ingest/schema.sql:110-147`. Older databases get the later columns added by
`pn.train.stats.ensure()` (`pn/pn/train/stats.py:29-50`). Rows are written once, when a
hand ends (`pn/pn/train/session.py:543-559`). A database error is logged and never stops
play. Sessions without a database (`Trainer(db=None)`) write nothing.

Read commands (`hands list`, `hand show`, `validate`, `ev`, `train`, `spot`) stop with
"database not found: ... (run `pn fetch` or pass --db)" instead of creating an empty file;
only ingest commands (`fetch`, `import-csv`, `alias`) create the database.

## Units

Chips are cents everywhere internally (`contracts/README.md:18`). The trainer's big blind is
100 cents (`pn/pn/train/session.py:77`). Columns ending in `_bb` are in big blinds.
Amounts inside `options_json` are cents.

## `train_hands`: one row per hand

| Column | Type | Meaning |
|---|---|---|
| `hand_uid` | TEXT, key | `<session id>-<hand no>` (`session.py:683-684`) |
| `session_id` | TEXT | 12 hex characters, random per session (`session.py:136`) |
| `played_at` | TEXT | UTC ISO time with microseconds, at hand end (`session.py:549`) |
| `table_size` | INTEGER | 6 or 8 |
| `depth` | INTEGER | starting stack in bb, 100 or 200 |
| `source` | TEXT | range source, always `gto` |
| `hero_pos` | TEXT | hero's seat this hand |
| `net_bb` | REAL | hero's result in bb (won minus put in; `session.py:522, 551`). Informational only |
| `opponents_json` | TEXT | JSON list of the villain player ids (null = bot) |
| `drill_spot` | TEXT | spot id the hand drilled; NULL for a natural hand |
| `drill_landed` | INTEGER | 1 if hero reached the drilled spot, 0 if not, NULL for natural hands |

After `retry` the hand is counted once, as first played; the replay is never written
(`session.py:207-225`).

## `train_decisions`: one row per hero decision

Built by `stats.decision_row` (`pn/pn/train/stats.py:178-193`).

| Column | Type | Meaning |
|---|---|---|
| `hand_uid`, `seq` | key | the hand, and 0-based order of hero's decisions in it |
| `street` | TEXT | preflop, flop, turn, river |
| `position` | TEXT | hero's seat |
| `situation` | TEXT | preflop `rfi` / `vs_open` / `vs_3bet` / `vs_4bet+` / `cold`; postflop `first` / `facing_bet` / `facing_raise` (`stats.py:57-68`) |
| `hand_class` | TEXT | hero's class, e.g. `AKo`, `99` |
| `rng` | INTEGER | the 1..100 roll, NULL with RNG off |
| `options_json` | TEXT | JSON list `{token, amount, freq, ev_bb}`; `amount` = street commitment after the action in cents (null for fold/check); `freq` = strategy frequency |
| `chosen` | TEXT | the token hero took |
| `rng_action` | TEXT | the action the roll pointed to (`pn/pn/train/rng.py:51-62`) |
| `best` | TEXT | the most frequent action |
| `loss_bb` | REAL | EV lost in bb; NULL unless the solve had EVs (TexasSolver has none, preflop never) |
| `grade` | TEXT | `best`, `mixed`, `blunder` or `none` (`pn/pn/train/grading.py`); pre-Plan 7 rows may say `ev` / `freq` and are re-graded on read (`stats.py:85-100`) |
| `matched_rng` | INTEGER | 1 if `chosen == rng_action`, 0 if not, NULL without a roll or frequencies |
| `pure` | INTEGER | 1 if one action has >= 99% (`PURE_FREQ`, `rng.py:10`) |
| `spot` | TEXT | spot id, e.g. `post|3bp|ip|flop|facing_bet` (`pn/pn/train/spots.py`) |
| `note` | TEXT | why a decision is ungraded: `multiway_unsolvable`, `off_tree`, `solver_unavailable`, `combo_outside_range` |
| `board` | TEXT | board cards, e.g. `Kd7s2c`; NULL preflop |
| `pot_bb` | REAL | pot before the decision, in bb |
| `formation` | TEXT | heads-up flop, e.g. `BTN vs SB - 3BP`; NULL preflop or multiway |

## What `pn train stats` computes

`pn train stats [--window 1000] [--source gto] [--db PATH]`
(`pn/pn/cli/train_cmds.py:29-64`) calls `stats.rolling` (`pn/pn/train/stats.py:131-170`):

- takes the newest `window` hands by `played_at` (all table sizes and depths together);
- counts best / mixed / blunder over **graded** decisions; rates are over graded decisions;
- RNG match rate = share of `matched_rng = 1` among decisions with a roll that are **not**
  pure (`stats.py:111, 120`);
- groups by street, position and situation; a group's `hands` is the number of hands with a
  decision in that group;
- slices of 100 hands, oldest first (`SLICE_HANDS`, `stats.py:23, 156-162`).

The same data is `GET /api/train/stats?window=&source=` (`pn/pn/web/train_routes.py:133-141`)
and the `rolling` block of every trainer state (`session.py:561-574`).

## What `pn train leaks` computes

`pn train leaks [--window 1000] [--table 6] [--depth 100] [--top 20] [--db PATH]`
(`pn/pn/cli/train_cmds.py:67-94`) prints the spot rows of `Leaks.importance`
(`pn/pn/train/leaks.py:645-662`) over the newest `window` hands **of that table and depth**
(`leaks.py:502-515`). Rows are sorted by importance priority
(`priority = freq x pot_bb x (1 - score)^p_form`, see
[trainer-spots.md](trainer-spots.md#importance)). The printed columns:

| Column | Formula | Line |
|---|---|---|
| `freq` | expected decisions per hand in the spot | `leaks.py:690-710` |
| `n` | your graded decisions in the spot | `importance.py:99-116` |
| `blunder rate` | `(blunders + 5 x mean_rate) / (n + 5)`; `mean_rate` = all blunders shrunk toward 0.2 | `importance.py:208-211, 226-227` |
| `blunders/100` | `100 x freq x blunder rate` | `importance.py:211` |
| `weight` | drawing weight: normalised(priority + 0.05 x sqrt(ln N / (n + 1))) | `importance.py:91-96` |

`GET /api/train/leaks` (`train_routes.py:160-165`) also returns `priority`, `score`,
`pot_bb`; `GET /api/train/importance` adds formation and texture rows.

Rows are sorted by `priority`, highest first (`importance.py:125-129`), not by
`blunders/100`; the command's help says so (`train_cmds.py:75`). The example below shows the
two orders differ.

## Worked example

This example builds a scratch database, plays 20 hands through the web API and reads the
log (run 2026-10-22; session ids replaced by `<sid1>`, `<sid2>`). It uses **fake solver documents** from the test fixtures, because a fresh clone has no
flop library. In those documents every hand plays its two options 25% / 75% (check / bet,
or fold / call) (`pn/tests/fixtures/fake_tree.py:1-7`). So the numbers show the mechanics,
not real strategy.

### 1. Drive the hands

The driver script (not in the repo) follows `pn/tests/test_web_api.py:243-277`:

```python
ingest_db.connect(db).close()     # empty DB with the schema
flop_seed.seed(cache_root)        # fake texas flops for every 6-max 100bb matchup
app = create_app(db, out / "solves")
app.state.trainer = Trainer(SolveCache(cache_root, runner=fake_runner), background=False, db=db,
                            leaks=Leaks(db, library=lambda t, d: None, cache_root=cache_root))
# TestClient(app): /api/train/new {"seed": 11, "drill_share": 0} for 10 hands, then
# {"seed": 12, "drill_share": 1} for 10 hands, each via /act and /next
```

`flop_seed`, `fake_tree` and `fake_runner` are in `pn/tests/fixtures/`. It needs the `web`
extra: `uv run --project pn --extra web --extra dev python drive.py <out> pn/tests/fixtures`.

Hero's policy: preflop, take the chart's best action; postflop, check when first and
**fold to every bet**. That is the leak we will look for.

Result: 20 hands, 42 decisions, 42 graded.

### 2. Look at the hands

```sql
SELECT hand_uid, hero_pos, net_bb, drill_spot, drill_landed FROM train_hands ORDER BY played_at;
```

```
hand        hero_pos  net_bb  drill_spot                   drill_landed
<sid1>-5    BB        -1.0                                 (10 natural hands: net 0, -1, -0.5)
<sid2>-3    BTN       -11.5   post|3bp|ip|flop|facing_bet  1
<sid2>-4    SB        -11.5   post|3bp|oop|flop|first      1
<sid2>-10   SB        -11.5   post|3bp|oop|river|first     1
```

Every postflop drill hand lost 11.5bb: hero put in a 3-bet pot preflop and then gave it up.

### 3. Grades by street and situation

```sql
SELECT street, situation, count(*) n, sum(grade='best') best,
       sum(grade='mixed') mixed, sum(grade='blunder') blunder
FROM train_decisions GROUP BY street, situation ORDER BY 1, 2;
```

```
street   situation   n   best  mixed  blunder
flop     facing_bet  5   0     5      0
flop     first       5   0     5      0
preflop  cold        1   1     0      0
preflop  rfi         11  11    0      0
preflop  vs_3bet     5   5     0      0
preflop  vs_4bet+    1   1     0      0
preflop  vs_open     8   8     0      0
river    facing_bet  2   0     2      0
river    first       2   0     2      0
turn     first       2   0     2      0
```

Preflop is clean. Every postflop decision is `mixed`.

### 4. Drill into "facing a bet"

```sql
SELECT spot, hand_class, board, pot_bb, chosen, best, grade, rng, rng_action, matched_rng
FROM train_decisions WHERE situation = 'facing_bet';
```

```
spot                           cls  board       pot_bb  chosen  best  grade  rng  rng_action  m
post|3bp|ip|flop|facing_bet    66   QdJs4c      39.75   fold    call  mixed  93   call        0
post|3bp|ip|flop|facing_bet    QTs  Ah8s3d      36.0    fold    call  mixed  51   call        0
post|3bp|oop|flop|facing_bet   A5s  Kc7s2d      36.0    fold    call  mixed  89   call        0
post|3bp|oop|flop|facing_bet   99   Ah8s3d      36.0    fold    call  mixed  64   call        0
post|3bp|oop|flop|facing_bet   66   Kh7d2c      27.75   fold    call  mixed  17   fold        1
post|3bp|oop|river|facing_bet  K9s  Ad8h3s9hAs  27.75   fold    call  mixed  63   call        0
post|3bp|oop|river|facing_bet  AKo  9s6d5h5s4h  36.0    fold    call  mixed  25   fold        1
```

Each row's `options_json` is
`[{"token": "fold", "freq": 0.25, ...}, {"token": "call", "freq": 0.75, ...}]`.

Reading it: hero folded 7 of 7 times; the strategy folds 25%. A fold is never a blunder
here (0.25 >= 0.1 x 0.75), so the blunder rate alone hides the leak. Two signals show it:

- `best = 'call'` on every row while `chosen = 'fold'`;
- the roll said `call` 5 times out of 7 and hero matched only the 2 rolls that landed in
  the fold bucket (1..25).

A query for this pattern in any log (output from the example):

```sql
SELECT spot, count(*) n, sum(chosen = best) took_best,
       sum(matched_rng) rng_hits, sum(matched_rng IS NOT NULL) rolled
FROM train_decisions WHERE street != 'preflop' AND grade != 'none'
GROUP BY spot ORDER BY n - sum(chosen = best) DESC;
```

```
spot                           n  took_best  rng_hits  rolled
post|3bp|oop|flop|first        5  0          1         5
post|3bp|oop|flop|facing_bet   3  0          1         3
post|3bp|oop|turn|first        2  0          0         2
post|3bp|oop|river|first       2  0          0         2
post|3bp|oop|river|facing_bet  2  0          1         2
post|3bp|ip|flop|facing_bet    2  0          0         2
```

The "first to act" rows are the same story: the fake strategy bets 75%; hero always checked.

### 5. `pn train stats`

```
$ uv run --project pn pn train stats --db <out>/pn.sqlite
20 hands (window 1000), 42 decisions, 42 graded: best 62%, mixed 38%, blunders 0 (0%); RNG match 19% of 16
group                 hands  graded  best  mixed  blunder  rng match
street: preflop       20     26      100%  0%     0%       -
street: flop          7      10      0%    100%   0%       20%
situation: facing_bet 7      7       0%    100%   0%       29%
...                   (turn, river, positions, other situations cut)
hands 1-20            20     42                    0%       19%
```

Preflop RNG match is `-` because every preflop spot here was pure (PokerCoaching charts).
The 19% RNG match over the 16 mixed spots is the clearest single number: a player who
follows the roll matches close to 100%.

### 6. `pn train leaks`

`PN_SOLVE_CACHE` points the scan at the same fake cache:

```
$ PN_SOLVE_CACHE=<out>/cache uv run --project pn pn train leaks --top 12 --db <out>/pn.sqlite
spot                        freq   n  blunder rate  blunders/100  weight
3BP OOP flop first to act   1.65%  5  1.1%          0.018         6.3%
3BP OOP turn first to act   1.26%  2  1.5%          0.019         2.9%
3BP IP flop facing a bet    1.24%  2  1.5%          0.019         2.8%
3BP OOP river first to act  0.97%  2  1.5%          0.015         2.3%
3BP OOP flop facing a bet   0.31%  3  1.3%          0.004         1.1%
4BP+ OOP flop first to act  0.52%  0  2.1%          0.011         1.3%
SRP OOP flop first to act   4.26%  0  2.1%          0.091         1.3%
...
```

The API row for the top spot has `pot_bb 24.0, score 0.6489, priority 1.0`. Checking by
hand: the global score is `(26 x 1 + 16 x 0.5 + 5 x 0.7) / (42 + 5) = 0.7979`; the spot's
score is `(5 x 0.5 + 5 x 0.7979) / (5 + 5) = 0.6489`; its raw priority is
`0.0165 x 24 x 0.3511^3 = 0.0171`. `SRP OOP flop first to act` has never been played, so its
score is the global 0.7979 and its raw priority `0.0426 x 5.5 x 0.2021^3 = 0.0019`, i.e.
0.11 of the top. The 3-bet-pot spots where hero kept giving up now get the most drills.

## Review grades of ingested hands

Real hands you ingested from PokerNow are graded separately into the `decision_ev` table
(`pn/pn/ingest/schema.sql`, `pn/pn/ev/run.py:22-26`), with the same best / mixed / blunder
rule. Postflop uses one `review`-preset solve per (hand, street, player), heads-up only;
preflop uses the charts (`pn/pn/ev/grade.py:1-15`). `ev_chosen`, `ev_best` and `loss` are in
cents and are NULL for TexasSolver solves (`grade.py:159-169`).

| Command | Purpose |
|---|---|
| `pn ev run [PLAYER] [--limit N] [--since ISO] [--force]` | grade missing decisions |
| `pn ev show HAND_ID` | the graded decisions of one hand |
| `pn ev summary [PLAYER]` | a player's totals |

Web: `GET /api/hands/{hand_id}/ev`, `GET /api/review/{hero}/ev`
(`pn/pn/web/ev_routes.py:36-49`). `pn ev summary` without a player uses `PN_HERO` and otherwise stops with
"set PLAYER or PN_HERO" (`pn/pn/cli/ev_cmds.py`).

## Text logs

`data/*.log` files are plain shell redirects (e.g. `nohup pn library fill > data/fill.log
2>&1 &`), git-ignored, with no fixed format. The trainer writes only to SQLite and `logging`.
