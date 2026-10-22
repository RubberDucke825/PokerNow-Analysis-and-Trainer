# Configuration

Everything a user may change: environment variables, solve presets, files in `config/`, the
`poker.sh` verbs, the web server, and where data lives.

Run `pn` from the repo root, with `uv run --project pn pn ...` or after `source pn/.venv/bin/activate`:
data paths such as `data/pn.sqlite` are relative to the working directory.

## Environment variables

`pn` reads them in one place, `pn/pn/settings.py`, at call time. Defaults marked "under the repo
root" resolve against the repo root wherever you run; `PN_DB`'s default is relative to the working
directory. An empty `PN_HERO`, `PN_TEXAS`, `PN_TEXAS_RESOURCES`, `PN_CONFIG_DIR`, `PN_WEB_PORT` or
`PN_WEB_HOST` counts as unset.

| variable | default | controls | read in |
|---|---|---|---|
| `PN_DB` | `data/pn.sqlite` | the SQLite database (hands, players, trainer history) | `settings.py:27-28`, through `default_db()` (`pn/pn/profile/source.py:21-22`) in study and solve commands and through `settings.db()` in ingest commands (`pn/pn/ingest/db.py:20-22`); `poker.sh:19` |
| `PN_HERO` | none (see note) | your player id for `poker.sh` ingest, study and solve, `pn live`, `pn ev summary` | `settings.py:31-32`; `pn/pn/cli/solve_cmds.py:45-49`, `pn/pn/cli/ev_cmds.py:115-117`; `poker.sh:18` |
| `PN_TEXAS` | `third_party/texassolver/build/console_solver` | the TexasSolver binary (may include arguments) | `settings.py:35-36`; `pn/pn/solve/texas.py:51-58` |
| `PN_TEXAS_RESOURCES` | `<binary dir>/../resources`, else `third_party/texassolver/resources` | solver lookup tables (`-r`) | `settings.py:39-40`; `pn/pn/solve/texas.py:61-69` |
| `PN_SOLVE_CACHE` | `data/solves/cache` (under the repo root) | solve documents, the flop index, solve lock and fill pid file | `settings.py:43-44`; `pn/pn/solve/cache.py:77-78`; `poker.sh:116` (quickstart) |
| `PN_LIBRARY_DIR` | `data/solves/library` (under the repo root) | old preflop root documents (read only) | `settings.py:47-48`; `pn/pn/solve/library.py:12-13` |
| `PN_CACHE_GZIP` | `1` | `0` writes new documents as plain `.json` instead of `.json.gz` | `settings.py:51-52`; `pn/pn/solve/docstore.py:44-45` |
| `PN_CONFIG_DIR` | `config` (under the repo root) | where charts and the other config files are read | `settings.py:55-57`; `pn/pn/profile/paths.py:14-15` |
| `PN_WEB_PORT` | `8765` | web UI port | `settings.py:60-61`; default of `pn web --port` (`pn/pn/cli/web_cmds.py:19`) |
| `PN_WEB_HOST` | `127.0.0.1` | web UI bind address | `settings.py:64-65`; default of `pn web --host` (`pn/pn/cli/web_cmds.py:20`) |
| `PN_EV` | `0` | `1` makes `./poker.sh study` also run `pn ev run` (slow: postflop solves) | `poker.sh:57` |

Notes:

- `PN_HERO` has no default. When it is unset, `pn live now` / `pn live watch` without `--hero` exit
  with "set --hero or PN_HERO" (`pn/pn/cli/solve_cmds.py:45-49`), `pn ev summary` without a player
  with "set PLAYER or PN_HERO" (`pn/pn/cli/ev_cmds.py:115-117`), and `./poker.sh` CSV ingest, `study`
  without a player and `solve` with "set PN_HERO" (`poker.sh:43,49,66`). `pn spot --hero` defaults
  to the hand's hero, not to `PN_HERO`.
- `PN_WEB_PORT` / `PN_WEB_HOST` only set the defaults of `pn web --port` / `--host`; the flags win
  (`pn/pn/cli/web_cmds.py:19-20`). Empty means the default (`settings.py:60-65`).
- `PN_DB` default is relative. Run `pn` from the repo root, where `data/pn.sqlite` resolves
  (`poker.sh` changes there, `poker.sh:16`). Ingest commands (`pn fetch`, `pn import-csv`, `pn alias`,
  `pn hands list`, `pn hand show`, `pn validate`) read `PN_DB` too: their `--db` defaults to it
  (`pn/pn/cli/ingest_cmds.py:31-33`, `pn/pn/ingest/db.py:20-22`) and an explicit `--db` wins.
  `./poker.sh ingest` still passes `--db "$PN_DB"` (`poker.sh:43-44`).
- Cloud fill scripts read their own variables: `PN_FILL_REPO` (`<owner>/<repo>` of the public fill
  mirror) and `PN_FILL_DIR` (its local checkout), both required with no default
  (`tools/gha_fill.sh:14,18-19`; `tools/cloud_fill.sh bootstrap` needs `PN_FILL_REPO`,
  `tools/cloud_fill.sh:70`), `GCP_PROJECT`, `GCP_ZONE`, `DISK_GB`, `DISK_TYPE`
  (`tools/gcp_box.sh`), `SHARD_BASE`, `SHARD_TOTAL` (`tools/cloud_fill.sh`). See
  [flop-library.md](flop-library.md).

## Solve presets

Defined in `SOLVE_PRESETS`, `pn/pn/solve/request.py:45-52`. Details and the meaning of "target" are in
[exploitability.md](exploitability.md).

| preset | max it | target % pot | bets / street | flop sizes | pick it when |
|---|---|---|---|---|---|
| `trainer` | 300 | 0.5 | 3 | sizing table | you want a quick answer; the trainer uses it for live turn/river |
| `review` | 600 | 0.3 | 2 | sizing table | reviewing a real hand (`./poker.sh solve`, `pn ev run`, the web default) |
| `library` | 400 | 0.3 | 2 | first size only | building flop library documents; the trainer reads only these |

Use: `pn solve run spot.toml --preset review`. Without `--preset` the spot TOML's own `[game]`
budget is used ([tree_config.md](../contracts/tree_config.md)).

**Warning: the budget is part of the cache key.** `spot_key` hashes the whole spot, including
`max_iterations`, `target_exploitability`, `time_limit_sec` and `threads`
(`pn/pn/solve/cache.py:101-103`, `pn/pn/solve/request.py:68-69`). If you edit a preset or change the
thread count (8, `request.py:21`), every previously cached document under that preset stops matching.
For the `library` preset this makes the whole downloaded library invisible to the trainer. The thread
count is in the key even though it does not change the result, so a 4-core machine must still use 8
threads to hit the shared library (the cloud fill did, `contracts/trainer.md` line 154).

## Files in `config/`

| file | what it is | read by |
|---|---|---|
| `preflop_charts/pokercoaching_100bb.txt` | PokerCoaching 100bb charts, the first range source | `pn/pn/profile/charts.py` |
| `preflop_charts/gtowizard_100bb.txt`, `gtowizard_200bb.txt` | charts from GTO Wizard data, the second source | same |
| `preflop_charts/common.txt`, `6max.txt`, `8max.txt`, `hu.txt` | hand-written fallback charts per table | same (`TABLE_FILES`, `charts.py:11`) |
| `preflop_charts/hand_ranking.txt` | hand order used to build ranges | same |
| `preflop_charts/README.md` | where each chart comes from and its key format | people |
| `sizing_table.toml` | flop bet sizes per board texture (`flop_bets = "auto"`) | `pn/pn/solve/texas.py:122-127` |
| `gto_baselines.json` | reference stats (VPIP, RFI, 3-bet...) for profiles; part GTO Wizard, part seed values | `pn/pn/profile/baselines.py` |
| `population_priors.json` | seed priors for player profiles (shrinkage toward a typical live pool) | `pn/pn/profile/priors.py` |
| `realization.toml` | leaf realization factors from the old depth-limited engine; validated when a spot names it, never read by TexasSolver | `pn/pn/adapter/validate.py:94-122` |
| `GTO_BASELINES.md` | notes on `gto_baselines.json` | people |

The preflop charts and `data/gtowizard/*_freqs.json` feed every flop solve's ranges and preflop bet
sizes, so they are also part of every library key. Editing them makes the library miss. More in
[flop-library.md](flop-library.md).

`realization.toml` stays in the tree. TexasSolver never reads it. The spot validator checks it
only when a spot TOML names it under `[leaf_model] realization` (`pn/pn/adapter/validate.py:114-122`),
which no spot built by `pn` does (`pn/pn/solve/build.py:60`); a test covers it
(`pn/tests/test_adapter_validate_plan5.py`).

## `poker.sh` verbs

Run from anywhere; it changes to the repo root (`poker.sh:16`).

| verb | does |
|---|---|
| `setup` | builds TexasSolver and the Python environment in `pn/.venv` |
| `quickstart` | after `setup`: unpacks `data/fixtures/library_sample.tar.gz` into `PN_SOLVE_CACHE` (default `data/solves/cache`) unless a flop index is already there; ingests the first `data/fixtures/pokernow_*.json` (with its ledger CSV if present) into `PN_DB`; builds a BTN vs BB single-raised turn spot on 6c4c2dKh (`data/solves/quickstart-turn.toml`) and solves it with the `trainer` preset; starts `pn web` (`poker.sh:110-141`) |
| `ingest <url\|id\|log.json\|export.csv> [--name ...]` | load a PokerNow game into `PN_DB` |
| `study [player]` | profiles, positional matrix, leaks, tips, review (`PN_EV=1` adds `pn ev run`) |
| `solve <hand_id> [street]` | build hero's spot and solve it with the `review` preset |
| `library fill\|status [...]` | `pn library ...` (see [flop-library.md](flop-library.md)) |
| `library-overnight [args]` | `pn library fill` detached; log `data/solves/library/fill.log` |
| `library-status` | whether that fill runs, its log tail, `pn library status` |
| `ev ...` | `pn ev ...` |
| `web` | the study UI |
| `all <game> [...]` | ingest, study, web |
| `test` | ruff and pytest |

Source: `poker.sh:2-14` and the `case` at the end of the file.

## Web server

`pn web` (`pn/pn/cli/web_cmds.py:15-33`) listens on `127.0.0.1:8765` by default. It has **no
authentication**. Keep it on 127.0.0.1. Binding to `0.0.0.0` exposes your hand history and lets anyone
on the network start solves.

## Where data lives

| path | contents | safe to delete? |
|---|---|---|
| `data/pn.sqlite` | your ingested hands, players, aliases, trainer history | no, unless you want to start over |
| `data/solves/cache/<key>.json.gz` + `<key>.toml` | solve documents and the spot each was solved from | yes, but each is a re-solve; deleting library documents makes those flops undealable |
| `data/solves/cache/hu_flop_library.json` | the flop library index | only with the documents; without it the trainer deals no flops |
| `data/solves/cache/registry_names.json` | names of solves made in the web UI | yes; those solves show as `cache-<key>` |
| `data/solves/cache/.solve.lock`, `.solve_priority/`, `.library_fill.pid` | runtime locks | only when no `pn` process runs |
| `data/solves/library/` | old preflop roots (read only) and the `library-overnight` log and pid | yes, when no fill runs |
| `data/gtowizard/` | GTO Wizard frequencies (tracked in git) | no: `pn library fill` refuses to run without them (`pn/pn/cli/solve_cmds.py:328`) |
| `data/fixtures/` | test fixtures (tracked) | no |
| `pn/.venv`, `third_party/texassolver/build/` | build outputs | yes; `./poker.sh setup` rebuilds them |

`data/solves/`, `*.sqlite` and `data/*.log` are git-ignored (`.gitignore`).
