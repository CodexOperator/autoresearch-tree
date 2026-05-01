#!/usr/bin/env python3
"""cli.py — agent-facing completion + verdict-emission CLI.

Agents call this to signal they're done. The driver picks up the rest.

Subcommands:
  done <iter_n> <agent_id> --verdict X --confidence Y [--node-id Z] [--parent P] [--notes ...]
  pending <iter_n> <agent_id> --reason "stuck on X"
  scaffold <iter_n> <agent_id> --type <node_type> --parent <parent_id> --slug <slug>
  status <iter_n>      — print all agent statuses for iter
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path


VERDICT_RE = re.compile(
    r"^(proved|disproved|inconclusive_lean_proved:\d{1,3}|inconclusive_lean_disproved:\d{1,3}|pending)$"
)

NODE_TYPES = ("hypothesis", "experiment", "verdict", "mvp", "outcome", "bigger-outcome", "app-purpose")


def _find_root() -> Path:
    """Walk up cwd to find autoresearch-tree.config.json."""
    d = Path.cwd().resolve()
    while d != d.parent:
        if (d / "autoresearch-tree.config.json").exists():
            return d
        d = d.parent
    print("ERR: no autoresearch-tree.config.json found from cwd up", file=sys.stderr)
    sys.exit(1)


def _agent_path(root: Path, iter_n: int, agent_id: str) -> Path:
    return root / "sessions" / f"iter-{iter_n:03d}" / agent_id / "agent.json"


def cmd_done(args: argparse.Namespace) -> int:
    if not VERDICT_RE.match(args.verdict):
        print(f"ERR: invalid verdict '{args.verdict}'. Allowed: proved | disproved | "
              f"inconclusive_lean_proved:N | inconclusive_lean_disproved:N | pending",
              file=sys.stderr)
        return 2
    root = _find_root()
    ap = _agent_path(root, args.iter_n, args.agent_id)
    if not ap.exists():
        print(f"ERR: no agent record at {ap}", file=sys.stderr)
        return 1
    rec = json.loads(ap.read_text())
    rec["status"] = "done"
    rec["finished_at"] = int(time.time())
    rec["verdict"] = args.verdict
    rec["confidence"] = args.confidence
    rec["node_id"] = args.node_id
    rec["parent"] = args.parent
    rec["notes"] = args.notes
    ap.write_text(json.dumps(rec, indent=2))

    # Write or update the node file with verdict info
    if args.node_id:
        node_file = _find_node_file(root, args.node_id)
        if node_file and node_file.exists():
            _append_verdict_to_node(node_file, args.verdict, args.confidence, args.notes)
            print(f"updated verdict in: {node_file}")
        else:
            # Fallback: write verdict node
            verdict_dir = root / "nodes" / "verdict"
            verdict_dir.mkdir(parents=True, exist_ok=True)
            slug = args.node_id.replace(":", "_")
            vfile = verdict_dir / f"{slug}.md"
            fm_lines = [
                "---",
                f"id: verdict:{slug}",
                "type: verdict",
                f"verdict: {args.verdict}",
                f"confidence: {args.confidence}",
            ]
            if args.parent:
                fm_lines.append(f"parents:\n  - {args.parent}")
            fm_lines.append("---")
            body = args.notes or ""
            vfile.write_text("\n".join(fm_lines) + f"\n\n{body}\n")
            print(f"wrote verdict: {vfile}")

    print(f"agent {args.agent_id} status=done verdict={args.verdict}")
    return 0


def cmd_pending(args: argparse.Namespace) -> int:
    root = _find_root()
    ap = _agent_path(root, args.iter_n, args.agent_id)
    if not ap.exists():
        print(f"ERR: no agent record at {ap}", file=sys.stderr)
        return 1
    rec = json.loads(ap.read_text())
    rec["status"] = "pending"
    rec["finished_at"] = int(time.time())
    rec["pending_reason"] = args.reason
    ap.write_text(json.dumps(rec, indent=2))
    print(f"agent {args.agent_id} status=pending reason={args.reason}")
    return 0


def cmd_scaffold(args: argparse.Namespace) -> int:
    """Pre-create a node file skeleton so the agent just fills in the body."""
    root = _find_root()
    node_type = args.node_type
    parent = args.parent
    slug = args.slug
    agent_id = args.agent_id
    iter_n = args.iter_n

    # node_id in canonical form: type:slug
    node_id = f"{node_type}:{slug}"
    # Convert type to directory name
    type_dir = node_type.replace("-", "_")
    node_dir = root / "nodes" / type_dir
    node_dir.mkdir(parents=True, exist_ok=True)
    node_file = node_dir / f"{slug}.md"

    if node_file.exists():
        print(f"SKIP: {node_file} already exists", file=sys.stderr)
        return 0

    # Build frontmatter
    fm_lines = [
        "---",
        f"id: {node_id}",
        f"type: {node_type}",
        f"parents:\n  - {parent}",
        "next_edges: []",
        "---",
        "",
        f"# {node_type}:{slug}",
        "",
    ]

    # Type-specific body prompts
    prompts = {
        "hypothesis": "## Hypothesis\n\nWhat is the testable claim? What would prove it? What would disprove it?\n\n",
        "experiment": "## Experiment\n\nWhat did you do? What happened? Include command/inputs and actual outputs.\n\n## Evidence\n\nRaw output, screenshots, logs.\n\n",
        "verdict": "## Verdict\n\nproved | disproved | inconclusive_lean_proved:N | inconclusive_lean_disproved:N\n\n## Evidence\n\nWhat evidence supports this verdict?\n\n## Confidence\n\n0.0 – 1.0\n\n",
        "mvp": "## MVP\n\nWhat does this script/module do? Show the code or describe the implementation.\n\n## Inputs\n\nWhat does it take?\n\n## Outputs\n\nWhat does it produce?\n\n",
        "outcome": "## Outcome\n\nInput shape (what enters):\n\nOutput shape (what exits):\n\nBehavior (what it does):\n\nEdge cases:\n\n## i/o doc\n\n```\ninputs:\noutputs:\n```\n\n",
        "bigger-outcome": "## Bigger Outcome\n\nWhat module or purpose does this outcome serve?\n\nHow do the child outcomes compose into this?\n\n",
        "app-purpose": "## App Purpose\n\nWhat is the top-level mission this chain serves?\n\n",
    }

    body = prompts.get(node_type, "")
    node_file.write_text("\n".join(fm_lines) + body)
    print(f"scaffolded: {node_file}")

    # Record scaffold in agent.json so cli.py done knows what to update
    ap = _agent_path(root, iter_n, agent_id)
    if ap.exists():
        rec = json.loads(ap.read_text())
        rec["scaffolded_node"] = node_id
        rec["scaffolded_file"] = str(node_file)
        ap.write_text(json.dumps(rec, indent=2))

    return 0


def _find_node_file(root: Path, node_id: str) -> Path | None:
    """Find a node file by its canonical id (type:slug or type/slug)."""
    # node_id like "experiment:foo-r1" or just "foo-r1"
    parts = node_id.split(":", 1)
    if len(parts) == 2:
        ntype, slug = parts
    else:
        return None
    type_dir = ntype.replace("-", "_")
    f = root / "nodes" / type_dir / f"{slug}.md"
    if f.exists():
        return f
    # Try hyphenated
    f2 = root / "nodes" / ntype / f"{slug}.md"
    if f2.exists():
        return f2
    return None


def _append_verdict_to_node(node_file: Path, verdict: str, confidence: float, notes: str) -> None:
    """Add verdict frontmatter fields to an existing node file."""
    content = node_file.read_text()
    if "---" not in content:
        return
    parts = content.split("---", 2)
    if len(parts) < 3:
        return
    fm, body = parts[1], parts[2]
    # Add verdict fields to frontmatter
    fm_lines = fm.strip().splitlines()
    # Remove any existing verdict/confidence lines
    fm_lines = [l for l in fm_lines if not l.startswith(("verdict:", "confidence:"))]
    fm_lines.append(f"verdict: {verdict}")
    fm_lines.append(f"confidence: {confidence}")
    new_fm = "---\n" + "\n".join(fm_lines) + "\n---"
    node_file.write_text(new_fm + "\n" + body)
    # Append notes to body
    if notes:
        with open(node_file, "a") as f:
            f.write("\n" + notes + "\n")


def cmd_status(args: argparse.Namespace) -> int:
    root = _find_root()
    iter_dir = root / "sessions" / f"iter-{args.iter_n:03d}"
    manifest = iter_dir / "manifest.json"
    if not manifest.exists():
        print(f"ERR: no manifest at {manifest}", file=sys.stderr)
        return 1
    m = json.loads(manifest.read_text())
    print(f"iter {args.iter_n}: {len(m['agents'])} agents")
    for a in m["agents"]:
        ap = _agent_path(root, args.iter_n, a["id"])
        rec = json.loads(ap.read_text()) if ap.exists() else a
        print(f"  {rec['id']}: status={rec.get('status')} verdict={rec.get('verdict', '-')} pid={rec.get('pid')}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_done = sub.add_parser("done")
    p_done.add_argument("iter_n", type=int)
    p_done.add_argument("agent_id")
    p_done.add_argument("--verdict", required=True)
    p_done.add_argument("--confidence", type=float, default=0.5)
    p_done.add_argument("--node-id", default=None)
    p_done.add_argument("--parent", default=None)
    p_done.add_argument("--notes", default="")
    p_done.set_defaults(func=cmd_done)

    p_pend = sub.add_parser("pending")
    p_pend.add_argument("iter_n", type=int)
    p_pend.add_argument("agent_id")
    p_pend.add_argument("--reason", required=True)
    p_pend.set_defaults(func=cmd_pending)

    p_scaffold = sub.add_parser("scaffold")
    p_scaffold.add_argument("iter_n", type=int)
    p_scaffold.add_argument("agent_id")
    p_scaffold.add_argument("--type", dest="node_type", required=True, choices=NODE_TYPES)
    p_scaffold.add_argument("--parent", required=True)
    p_scaffold.add_argument("--slug", required=True)
    p_scaffold.set_defaults(func=cmd_scaffold)

    p_stat = sub.add_parser("status")
    p_stat.add_argument("iter_n", type=int)
    p_stat.set_defaults(func=cmd_status)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
