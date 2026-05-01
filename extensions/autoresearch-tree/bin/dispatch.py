#!/usr/bin/env python3
"""dispatch.py — spawn N pi agents in parallel with zoom-targeted contexts.

Reads <project>/autoresearch-tree.config.json for parallelism + model.
Writes session manifest at <project>/sessions/iter-NNN/manifest.json so
heal.py can detect timeouts.

Each agent gets:
- zoom context file (built by zoom.py)
- skill manifest pointer (autoresearch-tree)
- completion-CLI instruction

The pi processes run detached; this script returns once they're spawned.
Caller (driver.sh) polls heal.py to monitor + heal.

Usage:
    dispatch.py <project_root> <iter_n>
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parent.parent  # extensions/autoresearch-tree
ZOOM_PY = PLUGIN_ROOT / "bin" / "zoom.py"
CLI_PY = PLUGIN_ROOT / "bin" / "cli.py"

# Env vars Claude Code injects so its own agent can use the user's Anthropic
# subscription (Token Plan). If pi inherits these, every spawned subagent
# silently consumes that same quota and competes with the interactive CC
# session for it. Scrub them so pi falls back to its own configured provider
# (typically minimax via ~/.pi/agent/settings.json).
ENV_VARS_TO_SCRUB = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_MODEL",
    "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
    "CLAUDE_CODE_EMIT_TOOL_USE_SUMMARIES",
    "CLAUDE_CODE_ENABLE_ASK_USER_QUESTION_TOOL",
    "CLAUDE_CODE_DISABLE_CRON",
    "CLAUDE_AGENT_SDK_VERSION",
    "CLAUDECODE",
)


def _scrubbed_env() -> dict[str, str]:
    """Inherited env minus Claude-Code-injected Anthropic credentials."""
    env = {k: v for k, v in os.environ.items() if k not in ENV_VARS_TO_SCRUB}
    return env


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project_root")
    ap.add_argument("iter_n", type=int)
    args = ap.parse_args()

    root = Path(args.project_root).resolve()
    cfg_path = root / "autoresearch-tree.config.json"
    if not cfg_path.exists():
        print(f"ERR: no config at {cfg_path}", file=sys.stderr)
        return 1
    cfg = json.loads(cfg_path.read_text())

    n = int(cfg.get("agent_dispatch", {}).get("claude_max_parallel", 1))
    big_split = float(cfg.get("big_idea_vs_small_idea_split", 0.3))
    timeout_min = int(cfg.get("agent_timeout_mins", 10))

    iter_dir = root / "sessions" / f"iter-{args.iter_n:03d}"
    iter_dir.mkdir(parents=True, exist_ok=True)

    # Pick zoom level + target per agent.
    targets = _pick_targets(root, n)

    manifest = {
        "iter": args.iter_n,
        "started_at": int(time.time()),
        "timeout_seconds": timeout_min * 60,
        "agents": [],
    }

    for slot, (level, target) in enumerate(targets):
        # Decide big-vs-small based on config split when "auto"
        if level == "auto":
            level = "big" if random.random() < big_split else "small"
            if level == "small" and target is None:
                level = "big"  # no target → fall back to big

        agent_id = f"a{slot:02d}-{uuid.uuid4().hex[:8]}"
        sess_dir = iter_dir / agent_id
        sess_dir.mkdir(parents=True, exist_ok=True)

        # Build zoom context
        zoom_cmd = ["python3", str(ZOOM_PY), str(root), str(args.iter_n), agent_id, "--level", level]
        if level == "small" and target:
            zoom_cmd.extend(["--target", target])
        ctx_path = subprocess.run(zoom_cmd, capture_output=True, text=True, check=True).stdout.strip()

        # Scaffold a node file before agent starts — agent fills body only
        scaffold_info = _scaffold_node_for_agent(root, args.iter_n, agent_id, level, target)
        if scaffold_info:
            print(f"scaffolded {scaffold_info['node_type']} node: {scaffold_info['node_id']}")

        # Spawn pi (detached). Output -> sess_dir/output.log
        pi_args = _build_pi_args(cfg, ctx_path, agent_id, args.iter_n, sess_dir, scaffold_info)
        log_file = sess_dir / "output.log"
        with open(log_file, "wb") as logf:
            proc = subprocess.Popen(
                pi_args,
                stdout=logf,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                cwd=str(root),
                env=_scrubbed_env(),
            )
        agent_record = {
            "id": agent_id,
            "slot": slot,
            "level": level,
            "target": target,
            "pid": proc.pid,
            "started_at": int(time.time()),
            "status": "running",
            "context_file": ctx_path,
            "log_file": str(log_file),
            "command": " ".join(shlex.quote(a) for a in pi_args),
        }
        (sess_dir / "agent.json").write_text(json.dumps(agent_record, indent=2))
        manifest["agents"].append(agent_record)
        print(f"spawned {agent_id} pid={proc.pid} level={level} target={target or '-'}")

    (iter_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"manifest: {iter_dir / 'manifest.json'}")
    return 0


def _pick_targets(root: Path, n: int) -> list[tuple[str, str | None]]:
    """Choose (zoom_level, target_node_id) for each of N slots.

    Strategy v1:
    - 1st slot: big idea (always — broad exploration)
    - rest:    small idea targeting top-attractor chains (highest descendant count)

    Returns list of (level, target_id_or_None).
    """
    out: list[tuple[str, str | None]] = []
    out.append(("big", None))
    if n <= 1:
        return out
    # Pull top attractors from INJECTION.md
    inject = (root / "context" / "INJECTION.md").read_text(encoding="utf-8")
    targets: list[str] = []
    in_block = False
    for line in inject.splitlines():
        if line.startswith("## attractive ideas"):
            in_block = True
            continue
        if in_block and line.startswith("## "):
            break
        if in_block and line.lstrip().startswith("- "):
            # `- idea:foo :: K descendants`
            payload = line.lstrip()[2:].split(" :: ")[0].strip()
            if payload:
                targets.append(payload)
    while len(out) < n:
        if targets:
            t = targets[(len(out) - 1) % len(targets)]
            out.append(("small", t))
        else:
            out.append(("big", None))
    return out


def _scaffold_node_for_agent(
    root: Path, iter_n: int, agent_id: str, level: str, target: str | None
) -> dict | None:
    """Decide what node type to scaffold and pre-create the file skeleton.

    Returns a dict with node_type, node_id, parent, slug or None if nothing to scaffold.
    """
    import uuid

    NODE_TYPES = ("hypothesis", "experiment", "verdict", "mvp", "outcome", "bigger-outcome", "app-purpose")

    # Pick node type: big=idea→hyp, small=extend existing chain
    if level == "big":
        node_type = "hypothesis"
    else:
        # small zoom: extend from target — decide step based on target type
        if target:
            if target.startswith("hypothesis:"):
                node_type = "experiment"
            elif target.startswith("experiment:"):
                node_type = "verdict"
            elif target.startswith("verdict:"):
                node_type = "mvp"
            elif target.startswith("mvp:"):
                node_type = "outcome"
            elif target.startswith("outcome:"):
                node_type = "bigger-outcome"
            else:
                node_type = "hypothesis"
        else:
            node_type = "hypothesis"

    # Generate slug
    slug = f"{agent_id}-{(uuid.uuid4().hex[:6])}"
    node_id = f"{node_type}:{slug}"
    parent = target or ""

    # Write the scaffold file
    type_dir = node_type.replace("-", "_")
    node_dir = root / "nodes" / type_dir
    node_dir.mkdir(parents=True, exist_ok=True)
    node_file = node_dir / f"{slug}.md"

    if node_file.exists():
        return None

    prompts = {
        "hypothesis": "## Hypothesis\n\nWhat is the testable claim?\nWhat would prove it? What would disprove it?\n\n",
        "experiment": "## Experiment\n\nWhat did you do? What happened?\nInclude command/inputs and actual outputs.\n\n## Evidence\n\nRaw output, logs.\n\n",
        "verdict": "## Verdict\n\nproved | disproved | inconclusive_lean_proved:N | inconclusive_lean_disproved:N\n\n## Evidence\n\nWhat supports this?\n\n## Confidence\n\n0.0 – 1.0\n\n",
        "mvp": "## MVP\n\nWhat does this do?\n\n## Inputs\n\nWhat does it take?\n\n## Outputs\n\nWhat does it produce?\n\n",
        "outcome": "## Outcome\n\nInput shape:\n\nOutput shape:\n\nBehavior:\n\nEdge cases:\n\n",
        "bigger-outcome": "## Bigger Outcome\n\nWhat purpose does this serve?\n\n",
        "app-purpose": "## App Purpose\n\nTop-level mission?\n\n",
    }

    body = prompts.get(node_type, "")
    fm = [
        "---",
        f"id: {node_id}",
        f"type: {node_type}",
        f"parents:\n  - {parent}",
        "next_edges: []",
        "---",
        "",
        f"# {node_id}",
        "",
    ]
    node_file.write_text("\n".join(fm) + body)
    return {"node_type": node_type, "node_id": node_id, "parent": parent, "slug": slug, "path": str(node_file)}


def _build_pi_args(
    cfg: dict,
    context_file: str,
    agent_id: str,
    iter_n: int,
    sess_dir: Path,
    scaffold_info: dict | None = None,
) -> list[str]:
    dispatch_cfg = cfg.get("agent_dispatch", {})
    pi_bin = os.environ.get("PI_BIN", "/home/ubuntu/.npm-global/bin/pi")
    args = [
        pi_bin,
        "--append-system-prompt", f"@{context_file}",
        "--append-system-prompt", (
            f"You are agent {agent_id} on iteration {iter_n}. "
            f"Your job: fill in the scaffolded node file below, then signal done."
        ),
    ]
    if scaffold_info:
        args.extend([
            "--append-system-prompt", (
                f"SCAFFOLDED NODE FILE: {scaffold_info['path']}\n"
                f"Node type: {scaffold_info['node_type']}  "
                f"Node ID: {scaffold_info['node_id']}  "
                f"Parent: {scaffold_info['parent']}\n"
                f"FILL IN the body of that file. Do NOT rewrite frontmatter.\n"
                f"When done, run: python3 {CLI_PY} done {iter_n} {agent_id} "
                f"--verdict <state> --confidence <0..1> --node-id {scaffold_info['node_id']} "
                f"--parent {scaffold_info['parent']}"
            ),
        ])
    else:
        args.extend([
            "--append-system-prompt", (
                f"When complete, run: python3 {CLI_PY} done {iter_n} {agent_id} "
                f"--verdict <state> --confidence <0..1> --node-id <id> --parent <parent>"
            ),
        ])
    # Allow extra prompt-from-skill
    skill_prompt = (PLUGIN_ROOT / "lib" / "agent-prompt.md")
    if skill_prompt.exists():
        args.extend(["--append-system-prompt", f"@{skill_prompt}"])
    # Initial user message: the task
    args.append(f"Begin iteration {iter_n} as agent {agent_id}. Read your zoom context, do the work, signal done.")
    return args


if __name__ == "__main__":
    sys.exit(main())
