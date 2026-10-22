# The flop library

A flop solve takes minutes to hours, so the trainer never solves a flop while you play. It deals
heads-up flops only from a precomputed library of TexasSolver solves: 187,514 boards over 341 preflop
lines, at 6-max and 8-max, 100bb and 200bb. This page says what is in it, its file format, how the
trainer uses it, how to get it and how to extend it.

Related: [exploitability.md](exploitability.md) (what "1 % of pot" means),
[computational-limits.md](computational-limits.md) (the tree each board was solved with),
[trainer.md](../contracts/trainer.md) "2026-10-07: library-first heads-up flops" and "Plan 16
additions", [strategy_output.md](../contracts/strategy_output.md) (document format).

## Coverage

A **matchup** is one heads-up preflop line at one table size and depth, for example
`srp BB-BTN 100bb: BTN raise, BB call`. Lines are enumerated by `matchups()`
(`pn/pn/train/flop_library.py:141-166`): limp + check, open + call, 3-bet + call, 4-bet + call,
limp + isolation raise + call, and a 3-bet cold-called after the opener folds; everyone else folds.
Pot types: `limped`, `srp` (single-raised), `3bp` (3-bet), `4bp+` (4-bet or more).

Counted from the index (`data/solves/cache/hu_flop_library.json`, 2026-10-17):

| table | depth | pot | matchups solved | 8-max lines aliased to 6-max | boards per matchup | boards | target (% pot) |
|---|---|---|---|---|---|---|---|
| 6-max | 100bb | limped | 5 | | 2 | 10 | 1 |
| 6-max | 100bb | srp | 30 | | 1,755 | 52,650 | 1 (51,298), 0.5 (1,344), 0.3 (8) |
| 6-max | 100bb | 3bp | 35 | | 1,755 | 61,425 | 1 |
| 6-max | 100bb | 4bp+ | 15 | | 1,755 | 26,325 | 1 |
| 6-max | 200bb | srp | 30 | | 184 | 5,520 | 2 |
| 6-max | 200bb | 3bp | 35 | | 184 | 6,440 | 2 |
| 6-max | 200bb | 4bp+ | 15 | | 184 | 2,760 | 2 |
| 8-max | 100bb | limped | 0 | 5 | | 0 | |
| 8-max | 100bb | srp | 26 | 30 | 184 | 4,784 | 1 |
| 8-max | 100bb | 3bp | 49 | 35 | 184 | 9,016 | 1 |
| 8-max | 100bb | 4bp+ | 13 | 15 | 184 | 2,392 | 1 |
| 8-max | 200bb | limped | 0 | 5 | | 0 | |
| 8-max | 200bb | srp | 26 | 30 | 184 | 4,784 | 2 |
| 8-max | 200bb | 3bp | 49 | 35 | 184 | 9,016 | 2 |
| 8-max | 200bb | 4bp+ | 13 | 15 | 184 | 2,392 | 2 |

Totals:

| item | value |
|---|---|
| index entries | 511 (341 with boards, 170 aliases) |
| board entries | 187,514, each a distinct document key |
| by target | 1 %: 155,250; 2 %: 30,912; 0.5 %: 1,344; 0.3 %: 8 |
| documents on disk | 189,284 `.json.gz` + 189,284 `.toml` |
| size | 5.5 GB on disk; index 26.9 MB |

Reading the table:

- **1,755** is every canonical flop (suits treated as interchangeable, `pn/pn/solve/flops.py:18`).
  **184** is a weighted subset stratified by texture and high card (`subset_flops`,
  `pn/pn/train/library_fill.py:93-123`); its weights reproduce the full texture and high-card mix,
  so the trainer deals realistic boards, just fewer distinct ones.
- **Aliases**: an 8-max line in which UTG and UTG1 only fold is the same spot as the 6-max line, so its
  index entry points at the 6-max matchup instead of holding boards
  (`pn/pn/train/library_alias.py:1-10`). 85 per depth.
- **Limped pots** are solved only at 6-max 100bb, and only on 2 flops (AcKcQd, Kc9c8d). Elsewhere they
  were skipped on purpose (`SKIPPED_POTS`, `library_alias.py:26`); the trainer runs those boards out
  with no betting. The 5 limped aliases at 8-max 200bb point at 6-max 200bb limped matchups that have
  no entry, so they deal nothing.
- The 1,770 documents beyond the 187,514 indexed boards are other cached solves. TODO(confirm): what
  they are (likely the 1 % documents replaced by finer passes, plus web UI solves).

The counting script is small: load the JSON, walk `matchups`, group by `table`, `depth`, `pot` and
each board's `accuracy`.

## File format

All files live in the solve cache, `data/solves/cache/` (or `PN_SOLVE_CACHE`).

**Documents.** One per solved board, named by its key:

- `<key>.json.gz`: the strategy document ([strategy_output.md](../contracts/strategy_output.md)):
  `meta` (backend, iterations, exploitability, stop reason...), `players`, 1,326 `combos`, and `nodes`
  with per-combo strategies down to the end of the flop. Plain `<key>.json` is also read
  (`pn/pn/solve/docstore.py:1-6`).
- `<key>.toml`: the spot the document was solved from (ranges, board, pot, stacks, sizes, budget;
  [tree_config.md](../contracts/tree_config.md)).

`<key>` is the SHA-256 of the canonical spot TOML (`spot_key`, `pn/pn/solve/cache.py:101-103`).
Anything in the spot changes the key: ranges, pot, stacks, sizes, iteration cap, accuracy target,
thread count.

**Index.** `hu_flop_library.json` (`INDEX_NAME`, `pn/pn/train/flop_library.py:45`; format at
`:183-187`):

```json
{"matchups": {
  "<matchup id>": {"label": "srp BB-BTN 100bb: BTN raise, BB call", "pot": "srp",
                   "positions": ["BB", "BTN"], "depth": 100, "table": "6max", "flops": "all",
                   "boards": {"Kc9c8d": {"key": "<sha256>", "weight": 12, "accuracy": 0.3}}},
  "<8-max matchup id>": {"label": "...", "table": "8max", "alias_of": "<6-max matchup id>"}
}}
```

- The matchup id is the first 16 hex characters of a SHA-256 of the flop request without a board
  (`matchup_id`, `flop_library.py:103-105`).
- `weight` is how many of the 22,100 raw flops the board stands for. It is used when dealing.
- `accuracy` is the **target** of the solve, not the achieved exploitability
  (`library_fill.py:514`).
- `flops: "all"` marks a matchup filled with all 1,755 flops; `subset: 184` one filled from the subset.
  Older 6-max entries carry neither, and no `table` either; an entry without `table` is 6-max (`flop_library.py:297`).

## How the trainer uses it

1. When preflop ends with two players and betting left, the trainer computes the matchup of the hand
   (`matchup_of`, `flop_library.py:132-138`).
2. It draws a board from that matchup's indexed boards, weighted by `weight`, among boards that do
   not clash with live hole cards under some suit permutation, then deals it under a random such
   permutation (`pick_board`, `flop_library.py:347-363`).
3. The cached document is the flop strategy for both bots and for grading. Hero is graded on the
   frequency of the chosen action in hero's combo row ([exploitability.md](exploitability.md)).
4. Turn and river are solved live from the flop document's leaf (`pn.solve.subgame`,
   [computational-limits.md](computational-limits.md)).
5. No board for the matchup: the hand runs out with no postflop betting
   (`solve.note = "no_library_solve"`, [trainer.md](../contracts/trainer.md) line 139).

So with an empty library the trainer is preflop only.

## How to get it

- **Sample**: `./poker.sh quickstart` unpacks `data/fixtures/library_sample.tar.gz` (4.7 MB) into
  `data/solves/cache` (or `PN_SOLVE_CACHE`), unless a flop index is already there (`poker.sh:116-122`).
  It holds one matchup, `6047a0236facd814` (`srp BB-BTN 100bb: BTN raise, BB call`, 6-max), with its
  first 60 boards in index order. It was made with
  `uv run --project pn python tools/library_pack.py sample <cache_dir> <out.tar.gz>`, whose defaults
  are `--table 6 --depth 100 --line srp --seats BTN,BB --boards 60`.
- **Full library**: download the release parts (each under 1.9 GB, with `MANIFEST.json` and
  `SHA256SUMS`) from <https://github.com/RubberDucke825/PokerNow-Analysis-and-Trainer/releases/tag/library-v1> into one directory, then from the repo root:

  ```
  uv run --project pn python tools/library_pack.py unpack <dir with the parts> data/solves/cache
  ```

  `unpack` checks every part against `MANIFEST.json` first, keeps existing documents at least as
  new, merges the index (boards already indexed win), then re-keys it as `pn library reindex` does
  (`--no-reindex` skips that). The reindex imports `pn`, so run it through `uv run --project pn` (or
  the activated venv), not a bare `python`. The parts are made with
  `uv run --project pn python tools/library_pack.py pack <cache_dir> <out_dir> [--part-bytes N]`
  (default 1.9 GB per part).

About 5.5 GB of free disk is needed for the unpacked library (plus the parts while unpacking).

The library only works if your inputs hash to the same keys as the ones it was built with: the
tracked `config/preflop_charts/*`, `data/gtowizard/*_freqs.json`, the `library` preset in
`pn/pn/solve/request.py:50-51` and 8 threads. Change any of these and the trainer finds no boards.
`pn library fill --dry-run` prints a sample key per library and checks it against the trainer's
lookup (`_dry_run`, `pn/pn/cli/solve_cmds.py:393-425`); `trainer lookup same` means your setup matches.

## How to extend it

All commands run from the repo root (`uv run --project pn pn library ...`). Options are in
`pn/pn/cli/solve_cmds.py:272-307`.

```
pn library status --table 6 --depth 100 --flops all     # solved vs planned per matchup
pn library fill --configs 0 --flops all --pots srp --accuracy 1        # one pot type, all flops
pn library fill --configs 0 --subset 184 --lib 8x100 --lib 6x200 --lib 8x200 \
    --new-only --accuracy 1 --depth-accuracy 200=2                     # the 8-max / 200bb fill
pn library alias --table 8 --depth 100                   # write 8-max aliases (after the fill)
```

- **Order**: matchups by expected value of a solved board (how often the line occurs x how often
  players blunder in that pot type), then interleaved so every matchup gets boards early
  (`library_fill.py:1-11`). `--reverse` walks the order from the light end.
- **Resumable**: boards already indexed at an equal or finer target are skipped. One fill per cache
  root (a pid file). A trainer solve preempts the fill, which then retries.
- **Accuracy passes**: `--accuracy 1 --accuracy 0.5` runs one pass per value; each is a new key and
  a full re-solve ([exploitability.md](exploitability.md)).
- **Inputs check**: the fill refuses to run without the GTO Wizard frequencies or chart files the keys
  hash (`solve_cmds.py:328-332`).
- **Sharding**: `--shard i/n` takes every n-th item of the same order. Run n fills on separate
  `PN_SOLVE_CACHE` roots, copy the documents into one cache, then record them with
  `pn library fill <same args> --cached-only` (no solving, one index write).

Cloud tooling (all driven from your machine; you run the account steps):

- **GitHub Actions**: `tools/gha_fill.sh export|create|seed|start|status|pull` and the workflow
  `tools/gha/fill.yml`. It runs shards on free 4-vCPU runners of a public mirror repo; config and the
  cache travel as an encrypted release asset, results as encrypted artifacts. The mirror repo is set by
  `PN_FILL_REPO=<owner>/<repo>` and its local checkout by `PN_FILL_DIR`; both are required, with no
  default (`tools/gha_fill.sh:14,18-19`). You need your own repo and the `gh` CLI.
- **Google Cloud**: `tools/gcp_box.sh create|host|status|start|delete` makes a spot VM;
  `tools/cloud_fill.sh` pushes or bootstraps the box, starts shards, pulls documents and refreshes the
  index on the box. `tools/cloud_runbook.md` is the full cycle (pull, `--cached-only` walk, alias,
  refresh, next wave).
- Always compare one key end to end before a remote run (`--dry-run` locally and on the remote): a
  machine missing an input solves boards nobody can look up (`tools/cloud_runbook.md` section 3).

Cost from document metadata: a 100bb SRP board at 1 % took a median 196 s on 8 threads, a 3-bet pot
22 s, a 4-bet pot 7 s ([computational-limits.md](computational-limits.md)).

## Provenance caveat

Every document's ranges come from the **preflop charts**, not from a preflop solve: PokerCoaching
charts first, then GTO Wizard frequencies, then hand-written charts
([solve_service.md](../contracts/solve_service.md) line 13). Preflop bet sizes come from the GTO
Wizard data (`data/gtowizard/`). The library is therefore the equilibrium of the postflop game
**given those ranges**. If a chart is wrong, every board of its lines is solved correctly for the wrong
ranges. Licensing of the chart sources is an open question for the release (`tasks/todo.md` lines
32-36). TODO(confirm): the pre-release notes record one known bad input, the 100bb CO open vs SB flat
range (SB calls too wide), which feeds the srp SB-CO solves; confirm and decide whether to state it
here.

## TODO(confirm)

1. What the 1,770 cached documents outside the index are.
2. Download URL of the full library parts.
3. The known too-wide SB flat range in the srp SB-CO lines.
