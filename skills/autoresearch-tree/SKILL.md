---
name: autoresearch-tree
description: Capillary DAG memory for fast LLM agent onboarding. Idea→Hypothesis→Experiment→MVP→Outcome chains with longest-chain-wins, mid-chain join, free-form branching, and a finite-state verdict taxonomy. Forks pi-autoresearch (originals untouched).
---

# autoresearch-tree

Capillary DAG memory layer that captures research-and-build trajectories as
`Idea → Hypothesis(+) → Experiment → Verdict → MVP → Outcome → Bigger Outcome → App Purpose`
chains. Optimized for fast LLM agent onboarding: longest-chain-wins surfacing,
mid-chain join, free-form branching, structured verdict taxonomy.

## How to Run

### From outside pi (shell)

A symlink at `~/.local/bin/autoresearch-tree` points to the driver. Run from
**inside any project tree** that contains `autoresearch-tree.config.json`:

```bash
cd <project>
autoresearch-tree --max-iters 5 --delay-mins 2
```

The driver walks up from `$PWD` to find the project root, so deep cwds work.

Useful flags:
- `--max-iters N` — total iterations (default 1)
- `--delay-mins M` — sleep between iters (default 0)
- `--smoke` — dry pass: snapshot + render + METRICs, no agent dispatch
- `--no-heal` — disable timeout monitor (debug)

### From inside pi (skill)

When this skill is loaded, the pi agent should invoke the same CLI via Bash:

```
$ autoresearch-tree --max-iters 1
```

Pi agents acting as **builders** (spawned by `dispatch.py`) get a zoom-targeted
context and a completion-CLI instruction injected into their system prompt.
They DO NOT call `autoresearch-tree` themselves — they do the work and signal
done via:

```
$ python3 ~/.pi/agent/git/github.com/davebcn87/pi-autoresearch/extensions/autoresearch-tree/bin/cli.py \
    done <iter_n> <agent_id> --verdict <state> --confidence <0..1> \
    --node-id <id> --parent <parent_id> --notes "<one-line>"
```

## Verdict Taxonomy (finite-state)

```
proved | disproved | inconclusive_lean_proved:N | inconclusive_lean_disproved:N | pending
```
N is integer 0..100. Plus per-verdict: `confidence` (0..1), `evidence_runs`,
`contradicts`, `supports`.

## Project Layout (drop-in portability)

The driver works in any directory tree shaped like:

```
<project>/
├── autoresearch-tree.config.json   # config
├── context/
│   ├── INJECTION.md                # auto-rendered each iter
│   ├── schemas/[*].md              # bracketed-active node-type schemas
│   └── kits/                       # cavekit specs (optional)
├── nodes/                          # auto-generated frontmatter node files
├── sessions/iter-NNN/<agent>/      # per-agent run state + logs
├── src/                            # graph_core, schema_registry, renderers, embeddings
├── bin/snapshot-build-site.py      # build-site → nodes/
└── bin/render-context.py           # nodes/ → INJECTION.md
```

To bootstrap a new project: copy these dirs (minus `nodes/` and `sessions/`)
and edit `autoresearch-tree.config.json`. The driver reads config from there;
no other state lives outside the project root.

## Iteration Anatomy

1. `bin/snapshot-build-site.py` rebuilds `nodes/` from `context/plans/build-site.md`
2. `bin/render-context.py` re-renders `context/INJECTION.md` (≤200-line ASCII)
3. METRIC lines emitted (`longest_chain_length`, `mvp_count`, `outcome_coverage`, ...)
4. `dispatch.py` spawns up to `agent_dispatch.claude_max_parallel` pi subprocesses:
   - Slot 0 → BIG zoom (whole graph)
   - Slot 1+ → SMALL zoom (top-attractor subtree, 2-hop bound)
5. `heal.py` polls every 30s; agents past `agent_timeout_mins` get killed +
   replaced with a healer subagent that diagnoses and patches
6. Agents signal completion via `cli.py done` → writes verdict node +
   marks `agent.json` `status=done`

## Big-Idea-vs-Small-Idea

Each iteration's first agent always explores BIG (broad context, fresh chain
or top-level fork). Remaining agents go SMALL (target = top attractor by
descendant count). Ratio configurable via `big_idea_vs_small_idea_split` in
config.

## Healing

If an agent's pid is alive but `status=running` past timeout, heal.py:
1. SIGTERM (2s grace) → SIGKILL the original
2. Spawn a HEALER pi subagent with the last 4 KiB of the original's log +
   diagnose-and-patch instruction
3. Healer commits a small fix and signals `pending` for the original agent_id
4. If pid is gone w/o completion → mark `failed` (no healer for ghost pids)

## See Also

- Cavekit (project spec): `<project>/context/kits/cavekit-autoresearch-tree-skill.md`
- Build site (task graph): `<project>/context/plans/build-site.md`
- Predecessor (frozen at iter 47): `~/.hermes/agi/`
- Plugin extension: `~/.pi/agent/git/github.com/davebcn87/pi-autoresearch/extensions/autoresearch-tree/`
