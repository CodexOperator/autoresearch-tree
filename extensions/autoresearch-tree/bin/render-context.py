#!/usr/bin/env python3
"""render-context.py — load a nodes dir, render to ASCII, write INJECTION_FILE.

Output: `context/INJECTION.md` containing:
- ASCII rendering (≤200 lines)
- chain-stat summary
- top-N attractive chains
- big-vs-small idea pool

Usage:
    python3 bin/render-context.py [nodes_dir]
    (default nodes_dir = ./nodes)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = Path(
    os.environ.get("AUTORESEARCH_TREE_PROJECT_ROOT")
    or os.environ.get("PROJECT_ROOT")
    or os.getcwd()
).resolve()
SRC = PLUGIN_ROOT / "src"
sys.path.insert(0, str(SRC))

from collections import defaultdict
from datetime import datetime, timezone

from graph_core import Node
from graph_core.edge import Edge
from graph_core.graph import Graph
from graph_core.loader import load_directory
from renderers import build_representation, render_ascii


def main() -> int:
    nodes_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "nodes"
    if not nodes_dir.is_dir():
        print(f"ERR: nodes dir not found: {nodes_dir}", file=sys.stderr)
        return 1

    g, loaded = load_directory(nodes_dir)
    print(f"loaded {len(loaded)} nodes from {nodes_dir}")

    # Wire parent/child edges from frontmatter; also populate child sets so
    # chain-walking (longest_chain_length, descendant counts) reflects the DAG.
    for ln in loaded:
        for parent_id in ln.node.parents:
            if g.has_node(parent_id):
                try:
                    g.add_edge(Edge(source_id=parent_id, target_id=ln.node.id, relation="spawns"))
                except Exception:
                    pass
                parent_node = g.get_node(parent_id)
                if parent_node is not None:
                    parent_node.children.add(ln.node.id)

    rep = build_representation(g)
    ascii_out = render_ascii(rep)
    lines = ascii_out.splitlines()
    print(f"ASCII rendering: {len(lines)} lines")
    if len(lines) > 200:
        print(f"WARN: exceeded 200 lines ({len(lines)})", file=sys.stderr)

    # Type counts
    by_type: dict[str, int] = defaultdict(int)
    for n in g.nodes:
        by_type[n.type] += 1

    # Chain stats: longest path through idea -> hyp -> task
    longest_len = _longest_chain_length(g)

    # Attractive chains: ideas sorted by descendant count
    idea_attract = []
    for n in g.nodes:
        if n.type == "idea":
            descendants = _count_descendants(g, n.id)
            idea_attract.append((n.id, descendants))
    idea_attract.sort(key=lambda x: -x[1])

    # Build INJECTION.md
    out_lines = [
        "# autoresearch-tree INJECTION CONTEXT",
        f"_generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}_",
        "",
        "## graph snapshot",
        f"- nodes: {len(g)}",
        f"- edges: {g.edge_count}",
        "- by type: " + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())),
        f"- longest chain: {longest_len} hops",
        "",
        "## attractive ideas (descendant count, top 10)",
    ]
    for nid, count in idea_attract[:10]:
        out_lines.append(f"- {nid} :: {count} descendants")

    out_lines.extend([
        "",
        "## ASCII view (≤200 lines)",
        "```",
        ascii_out.rstrip(),
        "```",
        "",
        "## big-vs-small decision",
        "Each iteration MUST first answer: **explore a big idea or small idea?**",
        "- big = fresh chain, broad concept (default 30%)",
        "- small = extend existing chain mid-way (default 70%)",
        "",
        "## verdict taxonomy",
        "`proved | disproved | inconclusive_lean_proved:N | inconclusive_lean_disproved:N | pending`",
        "",
        "## chain rules",
        "- longest-chain attracts but mid-chain join allowed",
        "- forks welcome — same idea may spawn multiple hypotheses",
        "- new ideas spawn from any node type (idea/hypothesis/experiment/verdict)",
        "",
        "## next-step suggestions",
    ])
    # Suggest pending tasks of small effort first
    pending = []
    for ln in loaded:
        if ln.node.type == "task" and "tier-" in " ".join(ln.node.tags):
            pending.append(ln.node.id)
    out_lines.append(f"- pending tasks: {len(pending)} (see nodes/task/)")
    out_lines.append("")

    out_path = PROJECT_ROOT / "context" / "INJECTION.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(out_lines), encoding="utf-8")
    print(f"wrote: {out_path}")
    return 0


def _longest_chain_length(g: Graph) -> int:
    """DFS longest path. OK for small DAG."""
    cache: dict[str, int] = {}

    def depth(nid: str) -> int:
        if nid in cache:
            return cache[nid]
        n = g.get_node(nid)
        if n is None or not n.children:
            cache[nid] = 0
            return 0
        best = 0
        for c in n.children:
            if c == nid:
                continue
            best = max(best, depth(c) + 1)
        cache[nid] = best
        return best

    if not g.node_ids:
        return 0
    return max(depth(nid) for nid in g.node_ids)


def _count_descendants(g: Graph, root: str) -> int:
    """BFS descendants count."""
    seen = {root}
    stack = [root]
    while stack:
        cur = stack.pop()
        n = g.get_node(cur)
        if n is None:
            continue
        for c in n.children:
            if c not in seen:
                seen.add(c)
                stack.append(c)
    return len(seen) - 1  # exclude root itself


if __name__ == "__main__":
    sys.exit(main())
