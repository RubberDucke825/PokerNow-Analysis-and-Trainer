# Preflop charts

This page explains where the preflop ranges come from, how the code picks one, what it
assumes about the game, and how to change them. The file-by-file key reference is in
[config/preflop_charts/README.md](../config/preflop_charts/README.md). This page does not
repeat it; read that file for the exact key syntax.

Every preflop decision in the repo goes through these charts:

- the trainer's bots and the grading of your preflop decisions (`pn/pn/train/preflop.py`);
- the ranges each player brings to the flop in a solve (`pn/pn/solve/sources.py`);
- the review grading of ingested hands (`pn/pn/ev/grade.py`);
- the priors used to estimate a real player's ranges (`pn/pn/profile/charts.py:230-242`).

## Where the charts live

All chart files are in `config/preflop_charts/`. The code finds that folder through
`pn.profile.paths.config_dir()` (`pn/pn/profile/paths.py:13-15`). Set `PN_CONFIG_DIR` to
use a different `config/` folder.

| File | Source | Who made it |
|---|---|---|
| `pokercoaching_100bb.txt` | PokerCoaching.com preflop charts PDF | extracted by `tools/charts_from_pdf.py` |
| `gtowizard_100bb.txt`, `gtowizard_200bb.txt` | GTO Wizard free tier, scraped by hand | converted by `tools/gtow_convert.py` |
| `6max.txt`, `8max.txt`, `hu.txt`, `common.txt` | written from memory | hand-written |
| `hand_ranking.txt` | Monte Carlo all-in equity vs a random hand | `python -m pn.profile.gen_hand_ranking` |

One more file matters but is not under `config/`:
`data/gtowizard/cash6m_cev_2.5x_freqs.json`. It holds the exact (mixed) GTO Wizard
frequencies. Its path is fixed under the repo root (`pn/pn/profile/gtowizard.py:33`), so
`PN_CONFIG_DIR` does not move it.

## The three sources

### 1. PokerCoaching (primary)

`tools/charts_from_pdf.py` reads the 13x13 grid images out of the PokerCoaching PDF by cell
colour (docstring, `tools/charts_from_pdf.py:1-26`). It checks every chart against the combo
counts printed in the PDF legend; any mismatch aborts the run. The PDF is not in the repo.

What the source assumes (from `config/preflop_charts/README.md`):

| Item | Value |
|---|---|
| Table | 9-handed |
| Stack | 100bb, with ante |
| Open size | 2.5bb in position, 3.5bb out of position (SB) |
| 3-bet size | 3x in position, 3.5x out of position |
| 4-bet size | 2.5x in position, 2.75x out of position |
| Mixing | none: pure as published |

Seat mapping. The PDF uses 9-handed seat names. 6-max uses LJ..BB directly. For 8-max, the
loader shifts the first seats so that the seat has the same number of players behind it:
8-max UTG reads the 9-handed UTG1 chart and 8-max UTG1 reads UTG2. The shift is
`index + 9 - players` (`pn/pn/profile/charts.py:119-124`), with `players` defaulting to 8.
It applies both to hero's seat and to the opener / 3-bettor in a `vs` key.

Not covered by this source: facing a 4-bet and later, cold 4-bets, cold calls, squeezes,
BB vs an SB limp, and heads-up play.

### 2. GTO Wizard (second)

`tools/gtow_convert.py` turns a raw scrape (`data/gtowizard/*_raw.json`) into two outputs
(`tools/gtow_convert.py:1-7`):

- the frequency file `data/gtowizard/<name>_freqs.json`;
- one pure chart file per depth, `config/preflop_charts/gtowizard_<depth>bb.txt`.

| Item | Value |
|---|---|
| Game | 6-max cash, chip EV, no rake modelled |
| Open size | 2.5x |
| Depths | 100bb and 200bb (`charts.GTOW_DEPTHS`, `pn/pn/profile/charts.py:25`) |
| Purification | a hand is in a pure range when that action's frequency is >= 50% (`tools/gtow_convert.py:77-81`) |
| SB first in | raise every hand whose raise + limp frequency is >= 50%; `limp SB` is left empty (`tools/gtow_convert.py:94-99`) |

Seat mapping (`pn/pn/profile/charts.py:21-27`):

| Our seat | GTO Wizard seat |
|---|---|
| LJ (6-max or 8-max) as hero | UTG |
| HJ, CO, BTN, SB, BB | same name |
| 8-max UTG, UTG1, UTG2 as hero | none: falls through to hand-written |
| 8-max UTG, UTG1, UTG2 as the opener faced | UTG (a slightly wide approximation) |

Depth choice: the nearest of 100 and 200bb first, then 100bb
(`pn/pn/profile/charts.py:90-92`).

### 3. Hand-written (last resort)

`6max.txt`, `8max.txt`, `hu.txt` and `common.txt` were written from memory of published
charts. They are not solver output. Responses are grouped by opener group (EP, MP, CO,
BTN, SB) rather than exact seat (`pn/pn/profile/charts.py:18-19`). `common.txt` is shared by
6-max and 8-max (`pn/pn/profile/charts.py:11-12`).

## Lookup order

There are two lookups. They agree on the order but read different things.

### `chart_range` (pure ranges)

`pn.profile.charts.chart_range` (`pn/pn/profile/charts.py:181-219`) returns one pure range
(combo -> 1.0):

1. PokerCoaching, when the file exists and has the exact key (`pc_range`, lines 155-163).
   An empty entry is a real answer ("never do this"), not a gap.
2. GTO Wizard pure chart files (`gtow_range`, lines 166-178).
3. The hand-written group charts (lines 198-219). A missing key raises `ChartError`.

Steps 1 and 2 only apply to 6-max and 8-max tables (lines 96, 129). Heads-up always uses
`hu.txt`.

### `gto_decision` (trainer and review grading)

`pn.train.preflop.gto_decision` (`pn/pn/train/preflop.py:338-352`) returns action
frequencies for one hand:

1. PokerCoaching, on clean lines only: no callers and no limpers, except SB limp then BB
   raise (`_pc_line`, lines 203-205). Each situation tries chart actions in order, for
   example `rfi` tries `open` then `limp` (`PC_STEPS`, lines 134-139). The answer is pure
   (frequency 1 or 0).
2. GTO Wizard **frequencies** from the JSON file, by hand class (`cell_dist`, lines 122-126).
   These are mixed, not the purified chart files.
3. The pure charts through `chart_range` (`chart_dist`, lines 242-266). Limped and multiway
   pots use the nearest heads-up chart.

The trainer's bots then apply two extra rules:

- bots never limp; the SB's chart limps become raises for the hands PokerCoaching continues
  with after a BB raise, folds otherwise (`sb_limp_as_raise`, lines 377-390);
- bots keep pots heads-up: no limp, no call unless facing a lone raiser, no raise with two
  others already in (`heads_up_dist`, lines 393-414).

## Game assumptions

| Item | Trainer | `pn solve new` | Where |
|---|---|---|---|
| Stack depths | 100, 200bb | any integer bb | `pn/pn/train/model.py:23`; `pn/pn/solve/request.py:161` |
| Blinds | 50 / 100 cents | 50 / 100 cents | `pn/pn/train/session.py:77-78`; `pn/pn/solve/request.py:130,183` |
| Ante | none | 0 by default | trainer posts blinds only (`pn/pn/train/engine.py:91-98`); `SpotRequest.ante = 0` (`pn/pn/solve/request.py:106`) |
| Rake | none | none | no rake anywhere in `pn/pn` |
| Open | 2.5bb, +1bb per limper | 3bb | `pn/pn/train/preflop.py:23-24`; `pn/pn/solve/request.py:131` |
| 3-bet | 3x IP / 3.5x OOP, +1x per caller | 3x IP / 3.5x OOP | `pn/pn/train/preflop.py:25`; `pn/pn/solve/request.py:132` |
| 4-bet | 2.2x the 3-bet | (3bp is the deepest line) | `pn/pn/train/preflop.py:26` |
| 5-bet | all-in | - | `pn/pn/train/preflop.py:294` |

When the line is exactly a GTO Wizard spot and the frequency file is present, the trainer
uses GTO Wizard's own raise size instead of the formula (`raise_to`,
`pn/pn/train/preflop.py:278-294`). Without the frequency file, the formula is always used,
and every flop spot built from the trainer's preflop differs (`has_freqs`, lines 103-107).

Note the mismatches:

- The PokerCoaching ranges assume an ante and 9 players; the trainer has no ante and 6 or 8
  players. TODO(confirm): is using ante charts in a no-ante game intended?
- `pn solve new` opens to 3bb, the trainer to 2.5bb. A 3bb `srp` spot from `pn solve new`
  therefore never matches a trainer flop. TODO(confirm): which open size should
  `pn solve new` use?
- PokerCoaching 4-bets are 2.5x / 2.75x; the trainer 4-bets 2.2x. TODO(confirm).
- The heading of `config/preflop_charts/README.md` says "3x opens" and its table targets
  "3x-open values", but no current source uses 3x opens. TODO(confirm): fix the README title.

## Commands

Run from the repo root with `uv run --project pn pn ...` (or `cd pn && uv run --frozen pn ...`).

| Command | What it does | Code |
|---|---|---|
| `pn charts show "<key>"` | prints the raw entry of `<key>` in every chart file that has it, with its width | `pn/pn/cli/profile_cmds.py:370-383` |
| `pn charts validate` | parses every entry of every chart file; fails on weights, parse errors or a missing file | `pn/pn/cli/profile_cmds.py:386-399`, `pn/pn/profile/charts.py:282-306` |
| `pn charts import <file> --key "<key>" [--file <name>]` | reads an exported range (Pio / GTO Wizard `AA:1,AKs:0.75`, plain list, or `[75]...[/75]`), keeps classes with mean frequency >= 0.5, writes it as a pure entry | `pn/pn/cli/profile_cmds.py:402-424`, `pn/pn/profile/charts.py:328-341` |
| `pn baselines import-gtowizard [--depth 100]` | rebuilds the preflop part of `config/gto_baselines.json` from the frequency file | `pn/pn/cli/profile_cmds.py:427-447` |

`pn charts show` is a file lookup. It does not run the lookup order above. To see which
range the code actually uses, call `pn.profile.charts.chart_range` from Python.

Regenerating from sources:

```sh
# PokerCoaching (needs the PDF and poppler's pdftohtml)
cd pn && uv run python ../tools/charts_from_pdf.py <preflop-charts.pdf> \
    --out ../config/preflop_charts/pokercoaching_100bb.txt
# GTO Wizard (needs your own raw scrape)
python tools/gtow_convert.py data/gtowizard/cash6m_cev_2.5x_raw.json
uv run --project pn pn baselines import-gtowizard
uv run --project pn pn charts validate
```

## Customising

To use your own charts:

1. Edit an existing file, or replace an entry with `pn charts import`. A key that exists in
   several files (for example `open BTN` in `6max.txt`, `8max.txt` and `hu.txt`) needs
   `--file`.
2. Keep ranges pure. `pn charts validate` rejects any weight.
3. Remember the lookup order. An entry in `pokercoaching_100bb.txt` wins over the same spot
   in a GTO Wizard file, which wins over a hand-written file. To make a hand-written chart
   take effect, the higher sources must not have that key (or the files must be absent).
4. The loader reads a fixed list of file names (`TABLE_FILES`, `PC_FILE`, `gtow_file`,
   `CHART_FILES`; `pn/pn/profile/charts.py:11,32,86-87,271`). A new file name is ignored
   unless you add it there.

What breaks when you change a chart:

- **Every postflop cache key.** The solve cache key is the SHA-256 of the canonical spot
  TOML, and the canonical spot includes each player's range text
  (`pn/pn/solve/cache.py:87-103`). The flop ranges come from the preflop charts. So any
  change to a preflop range that reaches the flop changes the keys of every flop document
  for that line. A precomputed flop library (or a library pack from `tools/library_pack.py`)
  then no longer matches, and the trainer finds no solved board for the line.
- The same is true for the GTO Wizard frequency file: it sets the trainer's raise sizes,
  which set pots and stacks, which are in the key. `pn library fill` refuses to run without
  it (`pn/pn/cli/solve_cmds.py:326-329`).
- Cached `lru_cache` reads: `chart_entries` and `write_chart_key` clear the read cache, but
  a running web server keeps other cached values. Restart it after editing.

`tools/library_pack.py` does not record which chart files a pack was built from; its
MANIFEST.json holds part checksums, document counts and the pn version. A mismatch shows up
only as cache misses (the trainer finds no document for the key it computes).

## Licensing note

`pokercoaching_100bb.txt` is extracted from PokerCoaching's paid PDF.
`gtowizard_*.txt`, `data/gtowizard/*.json` and the preflop part of
`config/gto_baselines.json` come from GTO Wizard's site. Both are third-party content. This
repo does not grant any right to them. If you publish or redistribute this repo, you must
decide whether you may ship those files.

Without them the code falls back to the hand-written charts. This was checked on
2026-10-22 with a scratch copy of `config/` from which `pokercoaching_100bb.txt`,
`gtowizard_100bb.txt` and `gtowizard_200bb.txt` were deleted, and `PN_CONFIG_DIR` pointing
at it:

| Check | Full config | Without the two sources |
|---|---|---|
| `pn charts show "open BTN"` | 6max, 8max, hu entries | same output: `6max.txt` and `8max.txt` 42.7%, `hu.txt` 77.4% |
| `chart_range("6max","open","BTN")` | 51.1% (PokerCoaching) | 42.7% (6max.txt) |
| `chart_range("6max","3bet","BB","CO")` | 14.3% | 7.4% (`3bet BB vs CO`, common.txt) |
| `chart_range("6max","call_3bet","CO","BTN")` | 11.5% | 5.6% (`call vs3bet CO`) |
| `chart_range("8max","open","UTG")` | 14.3% | 11.6% (8max.txt) |
| `chart_range("6max","limp","SB")` | 48.6% | `ChartError: unknown action 'limp'` |
| `pn charts validate` | exit 0 | lists 3 "missing file" rows, exit 1 |

So the fallback works for open, call, 3-bet, 4-bet and 5-bet lookups. Three caveats:

- `limp` and the `*_limp_vs_raise` actions exist only in PokerCoaching. Without it,
  `chart_range` raises for them. No caller asks it for `limp`: the trainer's `chart_dist`
  uses open / 3bet / 4bet / 5bet only (`pn/pn/train/preflop.py:249-256`), solve ranges go
  through `CHART_ACTION` (`pn/pn/adapter/spot_ranges.py:28-29`), and `prior_range` uses
  open / call / 3bet. The SB limp line simply has no PokerCoaching data to use.
- `pn charts validate` exits 1 when a source file is missing. TODO(confirm): should a
  missing optional source be a warning instead?
- The GTO Wizard frequency file sits in `data/gtowizard/`, not `config/`. The test above
  left it in place. Removing it also changes the trainer's raise sizes, the leak model's
  frequencies, and every flop cache key (see above). That case was not run here.

## TODO(confirm) list

1. PokerCoaching charts assume an ante and 9 players; the trainer plays without ante at 6 or
   8 players. Intended?
2. `pn solve new` opens to 3bb (`pn/pn/solve/request.py:131`); the trainer opens to 2.5bb
   (`pn/pn/train/preflop.py:23`). Which is right for `pn solve new`?
3. PokerCoaching 4-bet sizes (2.5x / 2.75x) vs the trainer's 2.2x (`pn/pn/train/preflop.py:26`).
4. `config/preflop_charts/README.md` title and RFI table say "3x opens"; no source uses 3x.
5. `pn charts validate` fails (exit 1) when an optional third-party file is absent.
