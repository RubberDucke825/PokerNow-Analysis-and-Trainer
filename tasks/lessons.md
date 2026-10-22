# Lessons

- 2026-10-01: Never `git add -A` while parallel agents are writing into the repo; it commits half-finished files from other streams. Always add explicit paths owned by the finished stream.
- 2026-10-01: Agents spawned before a contract change may finish without seeing it (tree agent missed the preflop size-list update). After editing a contract, message every affected agent immediately and ask for an explicit ack in their report.
- 2026-10-01: Reassigning a queued task away from a busy agent races with its inbox; it may have already started. Before spawning a parallel agent for work already queued elsewhere, confirm the first agent has not begun (ask for an ack), or scope the new agent as a verifier instead of a second implementor.
- 2026-10-01: Never `git stash` (or checkout/reset) while parallel agents are writing into the repo; it yanks their uncommitted files out from under them. To compare against HEAD, use `git show HEAD:path` or a worktree.
- 2026-10-07: A plain `git commit -m` commits whatever other streams have staged in the shared index (P's wiring landed in the lead's contracts commit 34db35f). When several streams share one working tree, commit with explicit pathspecs (`git commit -m ... -- <paths>`) or check `git diff --cached --name-only` first, and never `git add` then commit in two steps that another stream can interleave.
- 2026-10-07: Streams must verify the staged set belongs to them before committing; R3's commit swept in R1's three `node_locks` lines in main.rs and left HEAD unbuildable until R1 committed.

- 2026-10-10: Claimed GTO Wizard's scraped charts were the wrong baseline because the scrape notes said "rake cEV"; the user says 43.3% BTN RFI is correct at no rake. Rule: don't call a reference wrong from a memory note; state it as a question and let the user rule.
- 2026-10-10: After a crash, todo.md checkboxes lagged reality (6 merged streams unticked). Rebuild state from `git log`, `git worktree list` and each worktree's `git status` before planning; tick checkboxes at merge time, not at the end.
- 2026-10-10: Start `pn web` from the repo root (or set PN_DB); started from pn/ it silently serves an empty DB (503 on /api/hands).
- 2026-10-10: Removing fields from SpotRequest (Plan 7 cleanup) silently changed every matchup id in the flop index (a hash of the request JSON); the trainer and navigator found no boards. After changing a hashed shape, grep for everything keyed by that hash and re-key or migrate it in the same change (`pn library reindex`).

## 2026-10-11 git hooks
- Never pass `-c core.hooksPath=...` (or `--no-verify`) to `git commit`; the ecc pre-bash hook blocks it and the
  repo's hooks are meant to run. Plain `git commit` works; if a hook fails, fix what it flags.
- 2026-10-12: GHA shards solved ~2,600 3-bet/4-bet boards nobody can use: the preflop engine sizes raises from data/gtowizard/*_freqs.json, the seed did not carry it, so the shards hashed default sizes into every spot key. Before running a hashed workload on another machine, compare one key end to end (local `--dry-run` key vs the remote's) and make the entrypoint fail fast when a key input is missing (`pn library fill` now refuses without the freqs file).
- 2026-10-12: GCP shards re-solved boards GitHub had already done: each shard skips boards by its own index copy, seeded at push time, and nothing refreshed it while other pools kept adding boards. When several pools share one plan, push the merged index to every worker after each merge (`tools/cloud_fill.sh refresh`); a skip list that is not refreshed is a stale cache.
- 2026-10-13: Setting up a cloud box by rsyncing the repo from the laptop stalled repeatedly (and a preempted VM looks the same as a stalled link). Make the box fetch its inputs from somewhere durable (public mirror + release asset) and keep laptop-to-box transfers small; check the VM is still RUNNING before blaming the network.
