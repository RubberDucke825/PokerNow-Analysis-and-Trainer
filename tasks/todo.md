# Public release plan (2026-10-21)

Branch `release/public` (from `feat/graphite-ui` @ 273040e). Goal: a stranger can clone, install,
solve one spot and run one trainer session by following only the README; no private data in the
tree; tests pass; solver behaviour and outputs unchanged.

History is NOT rewritten here (user instruction). Findings are reported; the recommended path is to
publish from a fresh orphan commit of this branch.

## Audit findings (Phase 1)

Secrets: none in tree or history (gitleaks + grep over 262 commits; the 117 hits are SHA-256 cache
keys in a test fixture).

Personal data (tree + history):
- `data/fixtures/*`: a real home game (9 nicknames + PokerNow ids, shown cards, buy-ins, nets);
  ~8 test modules load them; ids/nicknames also in contracts, docstrings, tests, README.
  (A1: anonymised in place.)
- Hero's PokerNow id hardcoded: poker.sh, cli/solve_cmds.py, cli/ev_cmds.py, SKILL.md, tests.
- Owner's full name: ingest SKILL.md, test_ingest_cli.py, test_ingest_db.py, this file (plus an
  alternate hero id).
- 69 of 127 tracked screenshots render the live DB (real names, dates, pots).
- This file: an email, a GCP project id, two more real game ids. The plan 16 cloud-fill runbook:
  email, billing note, home paths.
- Home paths: tools/transcribe, .claude/plans/*, SKILL.md, runbook,
  tests/fixtures/adapter_hu_flop_out.json (scratch path).
- Owner's GitHub identity (private origin, public fill mirror) in cloud tooling + docs.
- Commit author name and email on all 262 commits.

Third-party content:
- HIGH `docs/course/transcripts/` (29 files): paid Upswing course (Uri Peleg) transcripts. Remove.
- HIGH `config/preflop_charts/pokercoaching_100bb.txt`: extracted from PokerCoaching paid PDF.
- HIGH `config/preflop_charts/gtowizard_*.txt`, `data/gtowizard/*.json`, derived
  `config/gto_baselines.json`: scraped GTO Wizard free tier (ToS). The trainer, review grading and
  every library solve's ranges depend on these; removing them changes solver behaviour and cache
  keys -> USER DECISION, left in place.
- MEDIUM `third_party/texassolver`: AGPL-3.0 vendored, one local patch (set_raise_limit),
  UPSTREAM.md says "private, local use only". 52 MB lookup table (GitHub warns > 50 MB).
- No root LICENSE; pyproject has no license field.

Broken / owner-only:
- `pn solve run` prints nothing for up to 210 s (review preset watchdog).
- `uv run pn` from repo root fails (pyproject is in pn/); README never shows `uv run --project pn`.
- Running pn inside pn/ silently creates an empty pn/data/pn.sqlite.
- Fresh clone trainer is preflop-only (empty library); README does not say so.
- Three install paths (README uv, poker.sh pip venv, CI pip); only uv honours uv.lock. No
  .python-version. build_texassolver.sh is macOS/Homebrew only. caffeinate/setsid in poker.sh.
- tools/charts_from_pdf.py needs poppler at /opt/homebrew; the PDF is not in the repo.
- Stale docs: tasks/resume.md, docs/design/graphite/README.md paths, docs/research/solver_repos.md
  (crates/ no longer exists), DESIGN.md port 8780.
- Junk tracked: third_party/texassolver/tmp_log.txt.

Flop library (headline feature): 189,284 cached solve docs (5.5 GB .json.gz), index 26.9 MB,
511 matchups, 187,514 boards. Not published anywhere unencrypted. GitHub release assets cap at
2 GB each -> needs split parts.

## Phase 2 tasks (parallel worktrees, one Opus implementor each)

- [x] A1 privacy + config (branch rel/a1): anonymise fixtures in place (consistent id/nickname map,
      same lengths), update tests/contracts/docstrings; delete docs/course, tools/transcribe,
      screenshots, design handoff html, .claude/plans, tasks/brainstorm+resume,
      docs/runbooks/plan16; scrub SKILL.md, lessons.md, cloud tooling (REPO env, no owner default),
      scratch path in fixture; remove HERO_DEFAULT -> PN_HERO via new pn/pn/settings.py; error
      instead of silently creating an empty DB. Verify: pytest count unchanged, grep clean.
- [x] A2 install + license + CI (branch rel/a2): .python-version, poker.sh setup via uv,
      Linux-capable build_texassolver.sh, CI uses uv --frozen and builds the solver (cached),
      LICENSE (MIT) + THIRD_PARTY.md + UPSTREAM.md rewrite, pyproject license, CONTRIBUTING.md,
      .gitignore gaps, drop tmp_log.txt. Verify: build + full pytest incl. real-solver tests.
- [x] A3 library + quickstart (branch rel/a3): tools/library_pack.py (pack/unpack/sample,
      split < 1.9 GB, sha256 manifest); committed sample library (one matchup, few MB) +
      `./poker.sh quickstart` (setup, unpack sample, ingest fixture, one short solve, web);
      progress output for `pn solve run`; solver-core golden tests (command text per preset,
      cache keys). Verify: quickstart < 5 min on a fresh clone, trainer deals a flop.
- [x] Merge A1..A3 into release/public; full test run.

## Phase 3 tasks

- [x] B1 docs/preflop-charts.md, docs/trainer-spots.md, docs/session-logs.md
- [x] B2 docs/exploitability.md, docs/computational-limits.md, docs/configuration.md,
      docs/flop-library.md
- [x] B3 README rewrite (short, links out), fix stale docs
- [x] C verification: fresh clone follows README only; code review; final grep for private data.

## Decisions left to the user
See the final summary: license (MIT + vendored AGPL), chart data (PokerCoaching / GTO Wizard),
history rewrite vs orphan commit, repo name, fill-mirror fate, anonymised names in fixtures.

## Review (2026-10-22)

Done on `release/public` (38 commits over feat/graphite-ui): fixtures anonymised, course transcripts /
screenshots / private notes removed, personal paths and accounts scrubbed, `pn.settings` + `PN_HERO`,
read commands refuse a missing DB, uv install path + `.python-version`, Linux-capable solver build, CI
builds the solver, MIT LICENSE + THIRD_PARTY.md + CONTRIBUTING.md, `tools/library_pack.py` + 60-board
sample + `./poker.sh quickstart`, solve progress output, golden solver tests, README rewrite, seven
docs/ methodology pages. Tests 881 pass (ruff clean). Fresh-clone check: clone -> setup -> quickstart
-> library flop dealt in ~53 s machine time; `./poker.sh solve` with the review preset can hit the
210 s watchdog on a loaded 8 GB machine.

Left for the user: history (every commit still holds the old fixtures and the author email: publish from
an orphan commit), the scratch path in pn/tests/fixtures/adapter_hu_flop_out.json (edit was declined),
chart-data licensing, LICENSE holder name, repo name, library hosting URL, PokerBank fate, 28 TODO(confirm)
markers in docs/ and README.
