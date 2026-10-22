# Contributing

## Setup

Prerequisites: [uv](https://docs.astral.sh/uv/getting-started/installation/), cmake and a C++17
compiler with OpenMP (macOS: `brew install cmake libomp`; Debian/Ubuntu: `apt install cmake
libomp-dev python3-dev`).

```sh
./poker.sh setup
```

This builds `third_party/texassolver/build/console_solver` (tools/build_texassolver.sh) and runs
`uv sync --all-extras --frozen` in `pn/`, which creates `pn/.venv` from `pn/uv.lock` with the
Python pinned in `pn/.python-version`. Do not install with pip; the lock is the only supported
dependency set. If you add or bump a dependency, edit `pn/pyproject.toml`, run `uv lock` in `pn/`
and commit the lock change on its own.

## Tests and lint

From `pn/`:

```sh
uv run --frozen pytest -q            # full suite, a few minutes
uv run --frozen ruff check pn tests  # lint; must be clean
```

`./poker.sh test` runs both.

Tests that need the real solver (`test_ev_real.py`, the real-solve tests in
`test_solve_texas.py`) skip when `console_solver` is not built, so the suite runs without a C++
toolchain. Every other test uses a fake solver (`tests/fixtures/fake_texas.py`); no test starts a
real solve by accident. Run the suite with the solver built before sending anything that touches
`pn/pn/solve/`, `third_party/texassolver/` or the build script. CI runs both variants (the
`python` and `solver` jobs in `.github/workflows/ci.yml`).

## Solve cache key

Solved strategies are cached under a key computed from the full spot (`pn/pn/solve/request.py`,
`pn/pn/solve/cache.py`). The key includes the solve budget (iterations, target exploitability,
time limit), the thread count and the bet sizes written by the preset. Changing any of these
changes every key, and every previously solved document, including the flop library, stops being
found.

So do not change `DEFAULT_SIZES` (`pn/pn/solve/build.py`, `pn/pn/solve/texas.py`),
`SOLVE_PRESETS` or the `Budget` defaults (`pn/pn/solve/request.py`) without a plan to re-solve
what the change invalidates. Say so in the PR.

## Contracts

`contracts/README.md` and the files it lists are the source of truth for every data format
(hand history, profiles, ranges, spot TOML, the solver command file, strategy JSON, trainer API).
Changing a contract means updating every consumer in the same change, and the contract file
first.

## Behaviour preservation

Solver output must not change by accident. `pn/tests/test_solver_golden.py` pins the command text
each preset sends to TexasSolver and the resulting cache keys. A PR that touches the solver path
must keep those tests green; if a change is intended, update the golden values in the same PR and
explain why the key or command changed and what has to be re-solved.

The vendored TexasSolver (`third_party/texassolver/`, AGPL-3.0) carries one local patch, listed in
`third_party/texassolver/UPSTREAM.md`. Keep local changes small and additive, list each one there,
and rebuild with `tools/build_texassolver.sh`.

## Commits

One logical change per commit, message as `type: description`:

```
fix: keep the focus ring on the Fold button

Optional body: why, not what.
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`.

Do not commit personal data: real player names or PokerNow ids, game logs, databases
(`*.sqlite`), solve output (`data/solves/`) or screenshots of a live database. Test fixtures use
anonymised data.

## License

By contributing you agree that your contribution is licensed under the MIT license (`LICENSE`).
Changes inside `third_party/texassolver/` are under AGPL-3.0 (see `THIRD_PARTY.md`).
