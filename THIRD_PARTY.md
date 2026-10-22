# Third-party components

This repository's own code is MIT-licensed (see `LICENSE`). The components below keep their own
licenses.

## Vendored source

| Component | Path | License | Notes |
|---|---|---|---|
| TexasSolver | `third_party/texassolver/` | AGPL-3.0 (`third_party/texassolver/LICENSE`) | Modified; see below |
| {fmt} | `third_party/texassolver/ext/fmt/` | MIT, with optional binary-distribution exception (`ext/fmt/LICENSE.rst`) | Bundled by TexasSolver, unmodified |
| GoogleTest | `third_party/texassolver/ext/googletest/` | BSD-3-Clause (`ext/googletest/LICENSE`) | Bundled by TexasSolver, unmodified; googlemock dropped |
| pybind11 | `third_party/texassolver/ext/pybind11/` | BSD-3-Clause (`ext/pybind11/LICENSE`) | Bundled by TexasSolver, unmodified |

### TexasSolver

- Upstream: https://github.com/bupticybee/TexasSolver, branch `console`, commit
  `6dfb65b4d7ed081da509e8d8c3d82138c4708267` (2026-08-19). Details in
  `third_party/texassolver/UPSTREAM.md`.
- How it is used: `tools/build_texassolver.sh` builds `console_solver`; `pn` writes a command file
  and runs the binary as a separate process (`pn/pn/solve/texas.py`), then reads its JSON output.
  No TexasSolver code is linked into `pn`.
- Local modification: `src/tools/CommandLineTool.cpp` accepts a `set_raise_limit <n>` command that
  sets the number of bets per street (bet plus raises) before `build_tree`; upstream fixes it at 4.
  Three added lines; nothing else in the source is changed. Some upstream files not needed for the
  console build (docs, tests of bundled libraries, sample outputs) are left out of the copy, as
  listed in `UPSTREAM.md`.
- The modified source is redistributed here under AGPL-3.0. Its license file is kept unchanged.

## Loaded at runtime, not vendored

| Component | Where | License |
|---|---|---|
| Geist, Geist Mono (fonts) | `@import` from Google Fonts in `pn/pn/web/static/tokens.css` | SIL Open Font License 1.1 |

The web UI falls back to system fonts when Google Fonts is unreachable.

## Python dependencies

Installed from PyPI through `pn/uv.lock` and not included in this repository; each package carries
its own license.
