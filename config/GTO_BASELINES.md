# GTO baselines (`gto_baselines.json`)

**Status: preflop = real GTO Wizard numbers; the rest = SEED values.** They are approximate equilibrium aggregate frequencies for 100bb NLHE
cash: 6-max, 3x opens, no rake. Our own solver batch runs (`mwsolver`, stream H) will
replace them. Loader: `pn.profile.baselines`. Each profile stat object compares its
posterior with `gto` (`dev_gto`, CI-aware). `leaks` ranks the confident deviations.

## Structure
- `stats.<stat>.default`: the all-spots aggregate, used for the profile's overall `stats`,
  `by_depth` and pairwise `vpip_when_in` / `three_bet_vs`.
- `stats.<stat>.by_position.<POS>`: used by `by_position`. A missing position gives `gto: null`;
  it never falls back to the default, because e.g. the BB's VPIP is not the overall VPIP.
- `stats.<stat>.hu` / `.multiway`: used by `by_players_in_pot`.
- `stats.<stat>.IP` / `.OOP`: used by the profile's `by_position.IP` / `.OOP` postflop cells
  (`pn.profile.spots`), and by `pn pos --split ip|oop`.
- Positional preflop cells (`by_position.<POS>`, `by_position_vs_opener`, `pn pos`) do not read
  this file when the player's situations map to GTO Wizard spots: their `gto` is the GTO Wizard
  frequency averaged over the situations actually faced (see `pn/pn/profile/POSITIONS.md`).
- `fold_vs_size.<street>.<bucket>`: used by `fold_vs_size`.
- `af_*` are on the aggression-factor scale.

## Real data vs seeds
**From GTO Wizard (real):** rfi, steal, limp, three_bet, cold_call, fold_to_three_bet,
four_bet, fold_to_four_bet (3-bettor vs 4-bet) and fold_to_steal (SB/BB vs CO/BTN/SB open), by position and as defaults. Each has `"source": "gtowizard:..."`.
`pn baselines import-gtowizard` computes them from `data/gtowizard/cash6m_cev_2.5x_freqs.json`
(6-max cash, cEV, 100bb, 2.5x opens, free tier, scraped 2026-10-01):
- Frequencies are combo-weighted over the hands that reach each node. The opener's range comes
  from its RFI frequencies; the 3-bettor's from its vs-open 3-bet frequencies.
- by_position is the mean over the spots for that seat. For example BB three_bet is the mean
  of the BB's 3-bet % vs each opener. The default is the mean over all spots, not weighted by
  how often each spot occurs.
- GTO Wizard's UTG is stored as our LJ.
- 8-max UTG, UTG1 and UTG2 and the LJ three_bet value keep the older seeds.
- four_bet mixes the opener's 4-bet vs a 3-bet with cold 4-bets, matching our stat (a raise
  facing exactly two raises).
- These are 2.5x-open solutions, while our default open is 3x.

**Still seeds:** everything else (vpip/pfr, squeeze, all postflop stats, fold_vs_size). The
table below is for those.

## Where the seed numbers come from
| group | basis |
|---|---|
| preflop RFI / steal / VPIP / PFR by position | Widths of our own charts (`config/preflop_charts`). These approximate GTO Wizard's free 100bb 6-max preflop solutions and the Upswing / PokerCoaching charts. These charts are recalled from memory, not solver output (see their README). BB VPIP 42% is the typical defend-vs-all-opens aggregate. |
| 3-bet by position (BB ≈ 12%, SB ≈ 11%, BTN ≈ 9%) | GTO Wizard preflop aggregates. BB vs CO is about 12–15%, BTN vs CO about 9–11%, and the totals are lower vs EP. |
| fold_to_three_bet 45%, four_bet 20% | Opener-vs-3bet aggregates in solver preflop trees: folds 40–50%, 4-bets 15–25%. |
| squeeze 7%, fold_to_squeeze 65%, call_vs_squeeze 25% | Squeeze nodes in multiway preflop solutions: tighter defence than vs a plain 3-bet. These are coarse. |
| limp 0% (SB 10%) | 100bb cash solutions are raise-or-fold except for some SB completes. |
| cbet_flop 55% (hu 60%, multiway 35%) | GTO Wizard flop aggregate reports. BTN vs BB single-raised pot is about 55–65% at mixed sizes; OOP and 3-bet pots are lower; multiway is much lower. |
| fold_to_cbet_flop 38% (hu 37%, multiway 45%) | The same reports. Vs a 33% c-bet the BB folds about 35–45%. |
| cbet_turn 50%, cbet_river 50%, probe_turn 35%, check_raise_flop 12%, donk_flop 5% | GTO Wizard turn and river aggregate reports and common solver summaries. Donking is rare in IP-aggressor single-raised pots. |
| IP / OOP splits: cbet_flop 62/40%, cbet_turn 55/45%, fold_to_cbet_flop 36/40%, raise_cbet_flop 10/13%, bet_vs_missed_cbet_flop 60/45% | GTO Wizard aggregate reports: the in-position preflop raiser c-bets far more often; OOP defenders raise more of their continuing range. Seeds. |
| raise_cbet 12% / 8% / 6% (flop / turn / river), bet_vs_missed_cbet 55% flop / 50% turn, float_turn 50% | Solver aggregates for stabbing after a missed c-bet and for raising c-bets. Seeds, coarse. |
| agg_freq 45% / 42% / 40% (flop / turn / river) | PT4 AFq = (bets + raises) / (bets + raises + calls + folds) from solver HUD aggregates. Seeds. |
| five_bet (GTO Wizard vs_4bet raise%); fold_to_five_bet 50%, six_bet 5%, fold_to_six_bet 20% | five_bet is real. The others are seeds: at 100bb a 5-bet is usually all-in, so the opener folds about half its 4-betting range, rarely re-raises, and a 3-bettor who 5-bet rarely folds to a 6-bet. They are replaced by `pn baselines import-gtowizard` once vs_5bet/vs_6bet spots are scraped. |
| fold_vs_size | Near the minimum-defence alpha `b/(1+b)`, slightly below it on the flop where equity realisation matters, and at or above it on the river. Flop: 25 → 0.20, 33 → 0.26, 50 → 0.33, 100 → 0.47. Turn: 75 → 0.42, 150 → 0.55. River: 75 → 0.43, 150 → 0.58. |
| af_* ≈ 2.0–2.2, wtsd 30%, wsd 52%, wwsf 47% | Typical full-game solver bot / GTO-bot HUD aggregates. These are the roughest seeds. |

References:
- GTO Wizard blog and aggregate reports (gtowizard.com/blog): c-bet and defence frequencies.
- M. Janda, *Applications of No-Limit Hold'em* (2013): minimum defence frequency and alpha.
- Upswing Poker and PokerCoaching.com free preflop charts: preflop widths.

## Known limitations (v1)
- The baselines are 6-max aggregates. For heads-up and 3–5-handed tables the right GTO
  VPIP/PFR is much higher, so overall VPIP "leaks" from short-handed games overstate the gap.
  Per-position baselines are less affected. Positional preflop cells sidestep this: heads-up
  seats (`BTN_HU`, `BB_HU`) compare with GTO Wizard's SB-vs-BB spot, and short-handed seats with
  the 6-max spot of the same seat. Postflop table-size-specific baselines are v2 (solver).
- They are texture-agnostic and size-agnostic, except `fold_vs_size`.
- Leaks require n ≥ 10 opportunities (`pn.profile.leaks.MIN_LEAK_N`).
