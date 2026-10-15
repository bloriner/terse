#!/usr/bin/env python3
"""Mine a corpus of Terse programs for repeated AST subtrees (DreamCoder-style).

Usage:
    python tools/mine_corpus.py            # exact-match subtree report
    python tools/mine_corpus.py --holes    # also run 1-hole anti-unification

Corpus sources (deterministic, sorted):
  - bench/tasks/*.terse
  - examples/*.terse
  - string literals in tests/test_*.py that parse as Terse

Output: ranked table of repeated subtree patterns with counts.
"""

from __future__ import annotations

import ast as py_ast
import glob
import os
import sys
from collections import Counter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from terse.parser import parse  # noqa: E402


# ---------------------------------------------------------------------------
# corpus collection
# ---------------------------------------------------------------------------

def _collect_files() -> list[tuple[str, str]]:
    """Return [(origin, source), ...] from .terse files, sorted."""
    out = []
    for path in sorted(glob.glob(os.path.join(REPO, "bench", "tasks", "*.terse"))):
        with open(path, encoding="utf-8") as f:
            out.append((f"bench/tasks/{os.path.basename(path)}", f.read()))
    for path in sorted(glob.glob(os.path.join(REPO, "examples", "*.terse"))):
        with open(path, encoding="utf-8") as f:
            out.append((f"examples/{os.path.basename(path)}", f.read()))
    for path in sorted(glob.glob(os.path.join(REPO, "tools", "corpus", "*.terse"))):
        with open(path, encoding="utf-8") as f:
            out.append((f"tools/corpus/{os.path.basename(path)}", f.read()))
    return out


def _collect_test_strings() -> list[tuple[str, str]]:
    """Extract string literals from tests/ that parse as Terse programs."""
    out = []
    for path in sorted(glob.glob(os.path.join(REPO, "tests", "test_*.py"))):
        with open(path, encoding="utf-8") as f:
            tree = py_ast.parse(f.read())
        for node in py_ast.walk(tree):
            if isinstance(node, py_ast.Constant) and isinstance(node.value, str):
                s = node.value
                if "(" in s or s.strip().startswith(";"):
                    try:
                        parse(s)
                    except Exception:
                        continue
                    out.append((f"tests/{os.path.basename(path)}:L{node.lineno}", s))
    # deterministic: sort by origin then content
    out.sort(key=lambda t: (t[0], t[1]))
    return out


def build_corpus() -> list[tuple[str, str]]:
    return _collect_files() + _collect_test_strings()


# ---------------------------------------------------------------------------
# subtree mining
# ---------------------------------------------------------------------------

def _walk(node):
    """Yield every subtree of an AST (tuples and lists)."""
    if isinstance(node, tuple):
        yield node
        for x in node:
            yield from _walk(x)
    elif isinstance(node, list):
        yield node
        for x in node:
            yield from _walk(x)


def _size(node) -> int:
    """Node count of a subtree."""
    if isinstance(node, tuple):
        return 1 + sum(_size(x) for x in node)
    if isinstance(node, list):
        return 1 + sum(_size(x) for x in node)
    return 1


def _is_trivial(node) -> bool:
    """Skip leaves, bare vars, and the seq-wrapped program root."""
    if not isinstance(node, (tuple, list)):
        return True
    if len(node) == 2 and node[0] == "var":
        return True
    return False


def mine_exact(corpus):
    """Count exact-match subtrees across the corpus.

    Each program contributes each distinct subtree shape once per program
    (presence per program, not raw occurrence count) to avoid one long
    program dominating.
    """
    counts: Counter = Counter()
    origins: dict = {}
    sizes: dict = {}
    n_programs = 0
    for origin, src in corpus:
        try:
            prog = parse(src)
        except Exception:
            continue
        n_programs += 1
        seen = set()
        for sub in _walk(prog):
            if _is_trivial(sub):
                continue
            key = repr(sub)
            if key in seen:
                continue
            seen.add(key)
            counts[key] += 1
            origins.setdefault(key, []).append(origin)
            sizes[key] = _size(sub)
    return n_programs, counts, origins, sizes


def _generalize(node, hole_at):
    """Replace the subtree at path `hole_at` (tuple of child indices) with ?.

    Returns (pattern, hole_subtree)."""
    if not hole_at:
        return "?", node
    idx = hole_at[0]
    if isinstance(node, (tuple, list)):
        kids = list(node)
        pat_kid, hole = _generalize(kids[idx], hole_at[1:])
        kids[idx] = pat_kid
        return (type(node)(kids) if isinstance(node, tuple) else kids, hole)
    return node, None


def _paths(node, prefix=()):
    """All child-index paths to non-trivial subtrees."""
    if isinstance(node, (tuple, list)):
        yield prefix
        for i, x in enumerate(node):
            yield from _paths(x, prefix + (i,))


def mine_holes(corpus, max_hole_size=6):
    """1-hole anti-unification: patterns with exactly one ? hole.

    For each subtree with >=2 children, try replacing each child subtree
    with a hole and count how many distinct programs contain the pattern.
    Only reports patterns where the hole-filler differs across programs
    (real generalization, not exact match in disguise).
    """
    pat_counts: Counter = Counter()
    pat_fillers: dict = {}
    pat_sizes: dict = {}
    n_programs = 0
    for origin, src in corpus:
        try:
            prog = parse(src)
        except Exception:
            continue
        n_programs += 1
        seen = set()
        for sub in _walk(prog):
            if _is_trivial(sub) or not isinstance(sub, tuple) or len(sub) < 2:
                continue
            # hole may replace any child except the op head (index 0);
            # leaf positions (literals, var names) are the most valuable holes
            for i in range(1, len(sub)):
                child = sub[i]
                if _size(child) > max_hole_size:
                    continue
                kids = list(sub)
                kids[i] = "?"
                pat = tuple(kids)
                key = repr(pat)
                if key in seen:
                    continue
                seen.add(key)
                pat_counts[key] += 1
                pat_fillers.setdefault(key, set()).add(repr(child))
                pat_sizes[key] = _size(sub)
    # keep only patterns with >=2 distinct fillers (genuine abstraction)
    pat_counts = Counter(
        {k: v for k, v in pat_counts.items() if len(pat_fillers[k]) >= 2}
    )
    return n_programs, pat_counts, pat_fillers, pat_sizes


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------

def _fmt_key(key: str) -> str:
    return key if len(key) <= 90 else key[:87] + "..."


def main():
    holes = "--holes" in sys.argv
    corpus = build_corpus()
    print(f"corpus: {len(corpus)} snippets")
    print()

    n, counts, origins, sizes = mine_exact(corpus)
    print(f"=== exact-match subtrees (present in >=2 of {n} programs) ===")
    ranked = [(k, c) for k, c in counts.most_common() if c >= 2]
    for i, (k, c) in enumerate(ranked[:15], 1):
        print(f"{i:2d}. x{c:3d}  size={sizes[k]:2d}  {_fmt_key(k)}")
    print()

    if holes:
        n, pc, fillers, psizes = mine_holes(corpus)
        print(f"=== 1-hole patterns (>=2 distinct fillers, >=2 of {n} programs) ===")
        ranked = [(k, c) for k, c in pc.most_common() if c >= 2]
        for i, (k, c) in enumerate(ranked[:15], 1):
            fills = sorted(fillers[k])
            fshow = ", ".join(f[:40] for f in fills[:4])
            if len(fills) > 4:
                fshow += f", +{len(fills) - 4} more"
            print(f"{i:2d}. x{c:3d}  size={psizes[k]:2d}  {_fmt_key(k)}")
            print(f"    fillers: {fshow}")
        print()


if __name__ == "__main__":
    main()
