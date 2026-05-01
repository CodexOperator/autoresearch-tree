#!/usr/bin/env python3
"""cli.py — agent-facing completion + verdict-emission CLI.

Agents call this to signal they're done. The driver picks up the rest.

Subcommands:
  done <iter_n> <agent_id> --verdict X --confidence Y [--node-id Z] [--parent P] [--notes ...]
  pending <iter_n> <agent_id> --reason "stuck on X"
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

    # If --node-id given, write a verdict node into nodes/verdict/
    if args.node_id:
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

    p_stat = sub.add_parser("status")
    p_stat.add_argument("iter_n", type=int)
    p_stat.set_defaults(func=cmd_status)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
