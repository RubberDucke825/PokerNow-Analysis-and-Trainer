# Preflop charts (100bb and 200bb cash, NLHE)

Open sizes differ by source. PokerCoaching assumes 2.5bb IP / 3.5bb OOP opens with an ante at 9
players. GTO Wizard is 6-max cEV with 2.5x opens. The hand-written files were written as 3x opens.
The trainer opens 2.5bb (`pn/pn/train/preflop.py:23`); `pn solve new` builds 3bb opens
(`pn/pn/solve/request.py:131`).

## Three kinds of chart file

**1. PokerCoaching.com charts** (`pokercoaching_100bb.txt`), the primary source.
Extracted on 2026-10-10 from the PokerCoaching preflop charts PDF (`preflop-charts.pdf`, 14 pages)
by `tools/charts_from_pdf.py` (`cd pn && uv run python ../tools/charts_from_pdf.py <pdf> --out
../config/preflop_charts/pokercoaching_100bb.txt`). 9-handed, 100bb with ante, PURE as
published; 2.5bb opens IP / 3.5bb OOP, 3-bet 3x IP / 3.5x OOP, 4-bet 2.5x IP / 2.75x OOP. The
tool reads each 13x13 grid image by cell colour and checks every chart against the combo counts
printed in its legend (all 68 charts match; the counts were read off the legends by eye and are
hard-coded in the tool, since the PDF text layer does not carry them).
- Seats are the 9-handed names (UTG UTG1 UTG2 LJ HJ CO BTN SB BB); 6-max uses LJ..BB. 8-max
  (UTG UTG1 LJ..BB) reads UTG1 for its UTG and UTG2 for its UTG1 (same players behind), as hero
  and as opener/3-bettor (`pn.profile.charts.PC_8MAX_SEAT`).
- Keys: `open <P>`; `3bet|call <H> vs <O>`; `4bet|call <O> vs3bet <T>`. A chart titled
  "X vs A/B" is stored once per opener. Value and bluff colours are merged into the action key;
  `open_value|open_bluff SB`, `3bet_value|3bet_bluff ...`, `4bet_value|4bet_bluff ...` keep the
  split for reference (the loader does not read them).
- SB plays limp/raise here: `open SB` is the raise range (it does not contain AA, KK or AKo,
  which limp), `limp SB` the limp range. "SB Limp vs BB Raise" is `3bet|call SB limp_vs_raise BB`
  (loader actions `limp`, `3bet_limp_vs_raise`, `call_limp_vs_raise`). The trainer and the hand review
  play and grade the SB first in as raise (`open SB`) / limp (`limp SB`) / fold, and the SB facing
  the BB's raise after limping from the `limp_vs_raise` charts (contracts/trainer.md, stream T6).
- `call SB vs CO` and `call SB vs BTN` are empty: the source 3-bets or folds there.
- The UTG+1 RFI vs 3-bet charts cover 202 combos while the UTG+1 RFI chart opens 190; the
  source is inconsistent there and both are kept as published.
- Not in the source (fall through to GTO Wizard / hand-written): vs 4-bet and later, cold
  4-bets / cold calls / squeezes, BB vs SB limp, heads-up.

**2. Real GTO Wizard solutions** (`gtowizard_100bb.txt`, `gtowizard_200bb.txt`).
These were scraped on 2026-10-01 from the user's own **GTO Wizard free-tier** account. They are
6-max cash, chip-EV (cEV), with **2.5x opens**. The free tier caps solutions at 100 spots per
day, and the 3x-open solutions are paywalled, so these are 2.5x, not the 3x of the hand-written
files.
- `gtowizard_100bb.txt` has 71 spots: RFI for every position, 3bet/call vs every open,
  4bet/call vs every 3bet, 5bet/call vs every 4bet, cold-4bet/cold-call spots, and BB vs an
  SB limp.
- `gtowizard_200bb.txt` has RFI and 3bet/call vs every open.
- They are made pure from the exact mixed frequencies (an action at ≥ 50% is in). The exact
  frequencies are kept in `data/gtowizard/cash6m_cev_2.5x_freqs.json`. That file is the source
  for the preflop GTO baselines (`pn baselines import-gtowizard`).
- `limp SB` is empty because SB is treated as raise-or-fold. GTO Wizard's SB actually
  completes 49% at 100bb, and the limp baseline uses that real number.
- Their seats are UTG, HJ, CO, BTN, SB, BB. Their **UTG is our 6-max LJ**.

**3. Hand-written approximations** (`6max.txt`, `8max.txt`, `hu.txt`, `common.txt`).
I wrote these **from memory** of the free published GTO Wizard, Upswing Poker and
PokerCoaching.com charts. They were **not** downloaded, copied, or checked against a solver.
They are now used only where neither PokerCoaching nor GTO Wizard has data:
- 8-max UTG / UTG1 / UTG2 as the acting player;
- heads-up;
- spots asked for without the other player's position (e.g. 4-bet with an unknown 3-bettor).

Trainer and hand-review grading (`pn.train.preflop.gto_decision`, `pn.ev.grade`) use the same
priority: PokerCoaching pure charts on clean lines, then the exact GTO Wizard frequencies, then
the hand-written charts (contracts/trainer.md "Preflop source order").

## Lookup order (`pn.profile.charts.chart_range`)
PokerCoaching -> GTO Wizard -> hand-written, per (action, hero, opener) key.
0. For 6-max and 8-max tables, use `pokercoaching_100bb.txt` when it has the exact key (any
   stack depth; the source says its charts apply from about 50bb up). An empty entry is a real
   answer (never do this), not a gap.
1. Otherwise, for 6-max and 8-max tables, use the GTO Wizard file at the depth nearest the requested one
   (100 or 200bb), then the 100bb file.
2. Position mapping:
   - As the acting player: our LJ (6-max or 8-max) is their UTG; HJ, CO, BTN, SB and BB map
     to themselves.
   - As the opener faced: 8-max UTG, UTG1 and UTG2 map to their UTG. This is an
     approximation, since a real 8-max UTG range is tighter.
   - STR uses BB.
3. Otherwise, fall back to the hand-written group charts below.

Replace any entry with your own exports: `pn charts import <file> --key "<key>" --file <name>`
writes a pure range in the same `key = range` format. You can also edit the files by hand.

All ranges are **pure**: every hand is fully in or out, with no `:0.5`-style weights. Mixed
hands from the source charts are included when played at least 50% of the time. Where that put
a hand in both a raise range and a call range, the raise range wins. `pn charts validate`
fails if any weight appears.

GTO Wizard keys: `open <POS>`, `3bet|call <HERO> vs <OPENER>`, `4bet|call <OPENER> vs3bet <3BETTOR>`,
`5bet|call <3BETTOR> vs4bet <OPENER>`, `cold4bet|coldcall <POS> vs <OPENER>open <3BETTOR>3bet`, `raise BB vsSBlimp`.

Hand-written format: one spot per line, `<key> = <range text>` (contracts/range_format.md). `#` starts a
comment. Loader: `pn.profile.charts`. CLI: `pn charts show "<key>"`, `pn charts validate`,
`pn charts import <file> --key "<key>" [--file 6max.txt]`.

| file | contents |
|---|---|
| `6max.txt` | RFI (raise to 3x BB) for LJ, HJ, CO, BTN, SB (raise-or-fold) |
| `8max.txt` | RFI for UTG, UTG1, UTG2, LJ, HJ, CO, BTN, SB |
| `hu.txt` | heads-up BTN open; BB 3bet/call vs BTN; BTN vs 3bet; 5bet/call vs 4bet |
| `common.txt` | vs open (3bet/call), vs 3bet (4bet/call), vs 4bet (5bet all-in/call); shared by 6-max and 8-max |
| `hand_ranking.txt` | 169 classes ordered by heads-up all-in equity vs a random hand (computed, not recalled) |

Keys:
- `open <POS>`
- `3bet <HERO> vs <OPENER>` / `call <HERO> vs <OPENER>`. HERO is `IP` (any non-blind seat),
  `SB` or `BB`. OPENER is a group: `EP` = UTG/UTG1/UTG2/LJ, `MP` = HJ, `CO`, `BTN`, `SB`.
- `4bet vs3bet <OPENER>` / `call vs3bet <OPENER>`: the opener facing a 3-bet.
- `5bet vs4bet EP|LATE` / `call vs4bet EP|LATE`: the 3-bettor facing a 4-bet from an early
  (EP/MP) or late opener. 5bet means all-in at 100bb.
- STR (straddle) uses BB charts. In 6-max, UTG labels map to LJ. Heads-up, SB maps to BTN.
- A key that exists in several files (e.g. `open BTN` in 6max, 8max and hu) needs `--file`
  on import.

RFI widths of the hand-written files (the targets are approximate 3x-open widths written down
with the files; no single source is recorded for them):

| seat | width here | target |
|---|---|---|
| UTG (8-max) | 11.6% | ~12% |
| UTG1 (8-max) | 14.5% | ~14% |
| UTG2 (8-max) | 15.8% | ~15–16% |
| LJ | 17.2% | ~17% |
| HJ | 20.7% | ~21% |
| CO | 27.0% | ~27% |
| BTN | 42.7% | ~43% |
| SB (raise only) | 38.8% | ~38% |
| HU BTN | 77.4% | 75–85% |

Simplifications:
- Responses are coarse. They are grouped by opener group rather than exact seat, and 6-max and
  8-max share them.
- SB is treated as 3bet-or-fold, with a token flat range.
- Rake is ignored.
- `hand_ranking.txt` comes from `python -m pn.profile.gen_hand_ranking` (Monte Carlo, 40k
  runouts per class, fixed seed). It matches published PokerStove equities to within about 0.3%.

These charts are **priors** for `preflop_range_est`. Observed frequencies and showdowns
override them as data accumulates. The GTO baselines' preflop numbers
(`config/gto_baselines.json`) now come from the GTO Wizard frequencies, not from these
hand-written widths.
