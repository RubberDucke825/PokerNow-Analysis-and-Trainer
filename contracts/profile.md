# Player Profile (JSON) — output of `pn profile <canonical_id>`; read by the web UI, tips and hero review

```json
{
  "canonical_id": "hal0000001", "display_name": "hal", "aliases": ["hal0000001"],
  "hands": 412, "showdowns_seen": 31, "updated_at": "ISO",
  "stats": {
    "vpip": { "post_mean": 0.31, "n": 412, "k": 128, "ci90": [0.27, 0.35], "prior_mean": 0.26 },
    "pfr": {...}, "three_bet": {...}, "fold_to_three_bet": {...}, "limp": {...}, "cold_call": {...}, "steal": {...}, "squeeze": {...},
    "cbet_flop": {...}, "cbet_turn": {...}, "cbet_river": {...},
    "fold_to_cbet_flop": {...}, "fold_to_cbet_turn": {...}, "fold_to_cbet_river": {...},
    "check_raise_flop": {...}, "donk_flop": {...}, "probe_turn": {...},
    "af_flop": {...}, "af_turn": {...}, "af_river": {...},
    "wtsd": {...}, "wsd": {...}, "wwsf": {...}
  },
  "by_position": { "BTN": { "vpip": {...}, "pfr": {...} }, ... },
  "by_players_in_pot": { "hu": {...}, "multiway": {...} },          // postflop stats split
  "fold_vs_size": { "flop": { "25": {...}, "33": {...}, "50": {...}, "100": {...} }, "turn": { "75": {...}, "150": {...} }, "river": {...} },
  "bet_size_hist": { "flop": { "0-30": 12, "30-45": 40, "45-70": 22, "70-120": 9, "120+": 3 }, ... },
  "preflop_range_est": { "BTN": { "open": { "width_pct": 0.48, "range_text": "...", "evidence_hands": 7 } }, ... },
  "lines": [ { "line": "pre:raise|flop:bet|turn:bet", "n": 9, "shown": [ {"cards":"AhKd","strength_class":"top_pair"} ], "bluff_ratio": 0.22 } ],
  "vs": { "<other_canonical_id>": { "hands_together": 200, "fold_to_their_bet": {...}, "three_bet_vs": {...}, "vpip_when_in": {...}, "net_vs": 3200 } },
  "archetype": { "label": "calling_station|aggressive|punching_bag|nit|tag|lag|unknown", "scores": { "calling_station": 0.71, ... }, "confidence": 0.6 },
  "tips": [ "Folds 68% (n=22, CI 52-81) to 150% turn bets: overbet turns with air.", ... ]
}
```
Every stat object = `{post_mean, n, k, ci90, prior_mean, gto, dev_pop, dev_gto}` where `gto` = GTO baseline frequency for that stat/spot from `config/gto_baselines.json` (seeded from published GTO Wizard-style aggregate reports; preflop values imported from GTO Wizard frequencies by `pn baselines import-gtowizard`; null if none), `dev_pop` = post_mean − prior_mean and `dev_gto` = post_mean − gto, each expressed in CI-aware units (0 if the CI contains the baseline; otherwise distance from the nearest CI edge). Profiles are built from ALL hands each player played against anyone (not hero-centric). Add top-level `"leaks": [ {stat, spot, player_freq, population, gto, n, direction, exploit} ]` ranked by |dev_gto| then n.
Previously: `{post_mean, n, k, ci90, prior_mean}` where `k` = prior pseudo-count (Beta-Binomial: posterior mean = (prior_mean*k + successes)/(k+n)). Multinomial action distributions use Dirichlet with the same scheme. Priors from `config/population_priors.json` (seeded with standard live-game population numbers; recomputed from DB with `pn priors rebuild`, hierarchical: population → position → player). Research + justify `k` per stat in pn/profile/PRIORS.md.
