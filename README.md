# Poker

A free, local study tool for 6-max and 8-max No-Limit Hold'em cash games. It ingests PokerNow
hand logs and profiles every player, solves heads-up postflop spots with the vendored
[TexasSolver](https://github.com/bupticybee/TexasSolver), and runs a web trainer that deals flops
from a **precomputed flop library of about 187,000 solved boards** and grades each of your
decisions against the solver's frequencies. Everything runs on one laptop, and the web UI binds to
127.0.0.1 only.

What it is not:

- No multiway postflop: only heads-up flop, turn and river spots are solved or graded.
- No preflop solving: preflop ranges come from fixed charts ([docs/preflop-charts.md](docs/preflop-charts.md)).
- No EVs: grades come from the solver's action frequencies, not expected values.
- No heads-up (2-handed) table: that mode is parked.

## Requirements

- [uv](https://docs.astral.sh/uv/getting-started/installation/). It installs Python 3.13 (pinned
  in `pn/.python-version`) and the locked dependencies (`pn/uv.lock`).
- cmake and a C++17 compiler with OpenMP, to build TexasSolver:
  - macOS: `brew install cmake libomp`
  - Debian/Ubuntu: `apt install cmake libomp-dev python3-dev`

## Quickstart

```sh
./poker.sh setup        # builds TexasSolver, creates pn/.venv
./poker.sh quickstart   # sample library, sample game, one solve, web UI
```

Then open http://127.0.0.1:8765 and click **Train**.

`quickstart` unpacks the 60-board sample flop library (6-max, 100bb, BTN opens and BB calls) into
`data/solves/cache`, ingests the bundled synthetic sample game into `data/pn.sqlite`, runs one
turn solve of about 5 s, and starts the web UI. It takes about a minute after `setup`. With only
the sample library, the trainer deals flops for BTN-vs-BB single-raised pots; other hands end at
the flop (see [Getting the full flop library](#getting-the-full-flop-library)).

## Running `pn` directly

`poker.sh` wraps the `pn` command-line tool (`./poker.sh` alone prints its verbs). To call `pn`
yourself, always from the repo root (paths such as `data/pn.sqlite` are relative to it):

```sh
source pn/.venv/bin/activate
pn --help
# or, without activating:
uv run --project pn pn --help
```

`PN_HERO` is your PokerNow player id. Hero-centric commands (`./poker.sh solve`, `./poker.sh
study` without a player, `./poker.sh ingest` of a CSV export) need it; `pn` commands take
`--hero <id>` instead. In the bundled sample game
the hero is `hero000001`. Other settings are in [docs/configuration.md](docs/configuration.md).

## Getting the full flop library

The full library is about 5.5 GB, published as 3 release parts (each under 1.9 GB, with
`MANIFEST.json` and `SHA256SUMS`) at <https://github.com/RubberDucke825/PokerNow-Analysis-and-Trainer/releases/tag/library-v1>. Download all parts into one
directory, then:

```sh
uv run --project pn python tools/library_pack.py unpack <dir with the parts> data/solves/cache
```

`unpack` verifies every part's checksum and merges into any existing library, so the sample can
stay. Without the full library the trainer deals flops only for matchups in the library and
otherwise ends the hand at the flop. Coverage, file format and how to extend the library are in
[docs/flop-library.md](docs/flop-library.md).

## Workflow in brief

| step | what | command |
|---|---|---|
| ingest | load a PokerNow game (URL, game id, saved log or CSV export) into SQLite | `pn fetch <url> --name "Full Name=<id>"`, `pn import-csv <export.csv> --hero <id>` |
| profile | stats per player and position, shrunk toward population priors, vs GTO | `pn profile all`, `pn pos <player>`, `pn tips <player>` |
| review | hero's deviations from GTO: justified exploit, leak or unknown | `pn review hero000001` |
| solve | build a heads-up postflop spot from a real hand and solve it (hand ids: `pn hands list --player hero000001`; 3+ player spots are refused) | `pn spot <hand_id> --street flop --hero hero000001 --out data/solves/spot.toml`, then `pn solve run data/solves/spot.toml --preset review` (up to 3.5 min on 8 GB; `--preset trainer` is quick but coarse) |
| train | play hands against GTO bots, graded per decision | `pn web`, then Train; `pn train stats` |

`pn <command> --help` lists every option. How the trainer deals and grades is in
[docs/trainer-spots.md](docs/trainer-spots.md); reading its logs is in
[docs/session-logs.md](docs/session-logs.md).

## Documentation

| doc | contents |
|---|---|
| [docs/preflop-charts.md](docs/preflop-charts.md) | where preflop ranges come from, lookup order, game assumptions |
| [docs/trainer-spots.md](docs/trainer-spots.md) | how the trainer deals hands, picks drills and grades answers |
| [docs/session-logs.md](docs/session-logs.md) | the trainer's SQLite tables and finding leaks in them |
| [docs/exploitability.md](docs/exploitability.md) | what the solver's convergence number means and the targets used |
| [docs/computational-limits.md](docs/computational-limits.md) | what is solved, what is simplified, runtime and memory |
| [docs/configuration.md](docs/configuration.md) | environment variables, presets, `config/`, `poker.sh` verbs, data paths |
| [docs/flop-library.md](docs/flop-library.md) | the precomputed flop library: coverage, format, download, extending |
| [contracts/README.md](contracts/README.md) | data formats and APIs that cross module boundaries |
| [DESIGN.md](DESIGN.md) | the web UI's visual system |
| [CONTRIBUTING.md](CONTRIBUTING.md) | setup, tests, solve-cache rules, commits |
| [THIRD_PARTY.md](THIRD_PARTY.md) | vendored components and their licences |

## Layout

```
pn/                       Python package and CLI `pn`, with tests (pn/tests)
  pn/                     ingest, profile, adapter, solve, ev, train, live, web, cli
third_party/texassolver/  TexasSolver, console branch, AGPL-3.0 (UPSTREAM.md: commit, local patch)
tools/                    build_texassolver.sh, library_pack.py, chart converters, cloud fill scripts
config/                   preflop charts, population priors, GTO baselines, sizing table
contracts/                cross-boundary formats (hand history, spot TOML, solver output, trainer API)
data/fixtures/            synthetic sample game, ledger, sample flop library
data/gtowizard/           GTO Wizard preflop frequencies
docs/                     methodology docs, UI design handoff (docs/design/)
tasks/                    project plan and lessons
poker.sh                  one entry point for setup, quickstart, ingest, study, solve, web, test
```

Runtime data (`data/pn.sqlite`, `data/solves/`) is created on first use and git-ignored.

## Testing

`./poker.sh test` runs ruff and pytest (or, in `pn/`: `uv run --frozen pytest -q`). Tests that
need the TexasSolver binary skip when it is not built; see [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

This repository's own code is MIT-licensed ([LICENSE](LICENSE)). TexasSolver, vendored in
`third_party/texassolver/` with one small local patch, is AGPL-3.0; `pn` runs it as a separate
process. Details and the other bundled components are in [THIRD_PARTY.md](THIRD_PARTY.md).

**Data provenance.** The preflop charts in `config/preflop_charts/` and the frequencies in
`data/gtowizard/` were transcribed by the maintainer in 2026-10 from PokerCoaching's preflop chart PDF
and GTO Wizard's free tier. They are included so that the trainer and the published flop library
reproduce exactly. They are not covered by the MIT licence. If you need different ranges, replace
the files ([Customising](docs/preflop-charts.md#customising)); every flop that uses them must then be
re-solved.
