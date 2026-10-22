---
name: pokernow-ingest
description: Ingest a PokerNow log (CSV export, game URL/id or saved JSON) into the Poker repo's database, ask the user who each unnamed username is, attach the names, then run the study pass (profiles, hero review) and offer the web UI. Trigger when the user shares a PokerNow CSV/log path or URL, says "ingest", "load this game", "new session", or types /pokernow-ingest.
---

# PokerNow ingest

All commands run from the repo root. The wrapper `./poker.sh`
activates the venv itself; for bare `pn` commands run `source pn/.venv/bin/activate` first.
Hero is the user's own PokerNow id, set as `PN_HERO=<hero id>` (the wrapper refuses hero commands
without it); call it `<hero id>` below. Database: `data/pn.sqlite` (local, untracked).

## 1. Ingest

```sh
./poker.sh ingest <path-or-url> [--ledger <ledger.csv>] [--name "Full Name=<id or username>" ...]
```

- A `.csv` is the logged-in "Download full log" export (it carries hero's hole cards); anything
  else (URL, game id, `.json`) is the public log. The wrapper passes `--hero` for the CSV.
- If a `ledger_<game_id>.csv` sits next to the file, pass it with `--ledger`; it enables the exact
  per-player net cross-check.
- Read the summary it prints: `loaded: N new`, `flagged`, `pot-math problems`, and the stack
  continuity line. Report any non-zero problems to the user verbatim; do not paper over them.
- Re-ingesting the same game is safe (idempotent); a CSV re-import of a game first loaded from the
  public log only adds hole cards.
- A filename with a browser suffix (`... (1).csv`) breaks game-id detection: copy it to the
  scratchpad as `poker_now_log_<game_id>.csv` and ingest the copy.
- **Hero on a new device.** The wrapper tags hero's hole cards with `$PN_HERO`. If the summary
  table shows the user under a different id, hero's cards were not attached. Check:
  `sqlite3 data/pn.sqlite "select count(*), sum(cards is not null and cards!='') from hand_players
  where player_id='<id>'"`. If cards are missing, ask the user to confirm the id is theirs (step 2),
  then re-import with `pn import-csv <csv> --db data/pn.sqlite --hero <id>` (only adds cards) and
  `pn alias assign "<hero full name>" <id> <hero id>`.

## 2. Who is who

```sh
pn alias unnamed --game <game_id> --json      # game_id is in the ingest summary / filename
pn alias list                                 # known people and their ids
```

For every row returned by `unnamed`, ask the user who that username is. Ask in ONE message with
the AskUserQuestion tool when there are at most 4 unknowns (one question per username, options =
the existing full names from `alias list` plus "New person"); otherwise ask in one plain-text list
and let the user answer in bulk ("hal2 is Hal Chen, bob = new, Bob Park"). Never guess a
mapping from a similar-looking username; a wrong merge corrupts two players' stats.

Then attach each answer:

```sh
pn alias assign "Full Name" <player_id>        # use the id from `unnamed`, not the username
```

An existing full name keeps accumulating that person's ids across games. If `assign` reports an
ambiguity or an unknown id, show the message to the user and ask again.

## 3. Study pass

```sh
./poker.sh study                 # profile all + hero's matrix, leaks, tips, review
```

Summarize for the user in a few lines: table of players with hands / VPIP / PFR / net from
`pn profile all`, the top 3 leaks from `pn review <hero id>` (justified vs leak), and anything
flagged during ingest. Offer `./poker.sh web` (http://127.0.0.1:8765) and `./poker.sh solve
<hand_id>` for a specific hand; do not start the web server unless asked.

## Notes

- Usernames are not unique or stable; PokerNow ids are stable per device/login. The database keys
  everything by id and groups ids under a person via `aliases`.
- Hole cards for non-hero players exist only at showdown; the CSV adds hero's cards.
- If ingest fails on a straddle or a multiway all-in hand, that is a known tree-config limit
  (see contracts/tree_config.md); the hand still loads, only `pn spot` refuses it.
