# Plan 16 cloud fill runbook (GCP boxes + GitHub Actions)

The setup is the same as at the end of Plan 11: two spot c2d-standard-56 boxes in us-east4-b, one per
GCP project, each running 14 shards (one per 4 vCPU) and walking the fill order **from its light end**
(`--reverse`). GitHub Actions waves on the public fill mirror (`$PN_FILL_REPO`) walk the **same order forward**. Both pools share one
flop index, and you refresh it after every merge. Commands run from the repo root. Lines starting with
`!` are account steps that you run yourself (uploads, dispatches, VM create and delete).

```
ARGS=(--configs 0 --subset 184 --lib 8x100 --lib 6x200 --lib 8x200 --new-only --accuracy 1 --depth-accuracy 200=2)
```

An array works in both zsh and bash: expand it as `"${ARGS[@]}"`, or as `"${ARGS[*]}"` where one string is
wanted. These are the GHA args (tools/gha/fill.yml default). The box default is the same plus `--reverse`
(tools/cloud_fill.sh `DEFAULT_FILL_ARGS`). `pn/tests/test_fill_tooling_args.py` checks both.

## 0. Before any box exists
1. `cd pn && .venv/bin/python -m pytest -q tests/test_fill_tooling_args.py`: the box args include
   `--reverse` and the GHA args do not, so the two pools never walk the same head boards.
2. Local key check: `cd pn && .venv/bin/pn library fill "${ARGS[@]}" --dry-run --db ../data/none.sqlite | grep -E 'sample|matchups|planned'`.
   Expect `8max/100: 88 of 175 (85 aliased, 2 limped skipped)`, `6max/200: 80 of 85`, `8max/200: 88 of 175`,
   `47104 solves planned` (the same three sample lines with `--reverse`), and `trainer lookup same` on all three sample lines.
3. Aliases on the real cache, with no fill or walk running (the command refuses while one runs):
   `cd pn && .venv/bin/pn library alias --table 8 --depth 100 && .venv/bin/pn library alias --table 8 --depth 200`,
   then `.venv/bin/pn library status --table 8` should show `85 aliased` at both depths.

## 1. Mirror and seed (`$PN_FILL_REPO`, checked out at `$PN_FILL_DIR`)
1. `tools/gha_fill.sh export`: the mirror gets the P1 seat shift and the P3/P4 fill code. It must run before
   `seed`/`start`: a mirror with pre-Plan-16 code fails on `--lib` (unknown option).
2. `! tools/gha_fill.sh create` (push the export) and then `! tools/gha_fill.sh seed`.

## 2. Boxes (same machine type and zone as before)
```
! tools/gcp_box.sh create fill-1 c2d-standard-56 us-east4-b
! GCP_PROJECT=<second-project-id> tools/gcp_box.sh create fill-2 c2d-standard-56 us-east4-b
H1=$(GCP_ZONE=us-east4-b tools/gcp_box.sh host fill-1)
H2=$(GCP_PROJECT=<second-project-id> GCP_ZONE=us-east4-b tools/gcp_box.sh host fill-2)
tools/cloud_fill.sh bootstrap $H1; tools/cloud_fill.sh bootstrap $H2   # clone mirror + seed (push stalls from the Mac)
```
The script defaults are still n2d-standard-32 in us-central1-a, so pass the machine type and zone
explicitly. us-central1-a and us-east1-b preempted repeatedly in Plan 11.

## 3. Key compare before the first wave
- Boxes: `tools/cloud_fill.sh keys $H1` and `tools/cloud_fill.sh keys $H2` must both print `keys match`.
  `keys` uses the same args, and order does not change the sample.
- GHA: `! tools/gha_fill.sh start 1 10 "${ARGS[*]} --dry-run"`, then
  `gh run view --log -R "$PN_FILL_REPO" | grep -E ' sample |matchups'` must equal the local lines
  from step 0.2.
If anything differs, stop: the pool would solve boards that the trainer never looks up.

## 4. Start
```
SHARD_BASE=0  SHARD_TOTAL=28 tools/cloud_fill.sh start $H1 14   # default args: ARGS + --reverse
SHARD_BASE=14 SHARD_TOTAL=28 tools/cloud_fill.sh start $H2 14
! tools/gha_fill.sh start 60 330                                  # fill.yml default: ARGS, forward
```
If you pass args by hand to `start`/`resume`, keep `--reverse` for the boxes and leave it out for GHA.
First hour: `tools/cloud_fill.sh status $H1` (and `$H2`). Take the per-board time and MB/doc from the
shard logs and project the GCP cost. Stop and ask if the projection goes past ~$120.

## 5. Each cycle (pull first, then the chain; never overlap a pull with a seed upload or refresh)
1. `tools/cloud_fill.sh pull $H1; tools/cloud_fill.sh pull $H2; ! tools/gha_fill.sh pull`
2. Walk: `cd pn && .venv/bin/pn library fill "${ARGS[@]}" --cached-only --db ../data/none.sqlite`
3. After the walk ends: `pn library alias --table 8 --depth 100` and `--depth 200` (idempotent), then
   `pn library status --table 8` should still show `85 aliased` at both depths.
4. `tools/cloud_fill.sh refresh $H1; tools/cloud_fill.sh refresh $H2; ! tools/gha_fill.sh seed`, then the next
   `! tools/gha_fill.sh start 60 330`.

## 6. Preemption and end
- A preempted box: `! tools/gcp_box.sh start fill-1 us-east4-b`, then
  `SHARD_BASE=0 SHARD_TOTAL=28 tools/cloud_fill.sh resume $H1 14` (fill-2: base 14, its project).
- Done, or at the GCP cap: `tools/cloud_fill.sh stop` on each box, run one last cycle (steps 5.1 to 5.3), then
  `! tools/gcp_box.sh delete fill-1 us-east4-b` and
  `! GCP_PROJECT=<second-project-id> tools/gcp_box.sh delete fill-2 us-east4-b`.
