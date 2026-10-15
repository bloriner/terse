#!/usr/bin/env python3
"""Score a leaderboard submission.

Usage:
    python tools/score_submission.py <submission_dir>

<submission_dir> must contain:
    meta.json               {"model": "...", "date": "YYYY-MM-DD", "notes": "..."}
    solutions/<task>.terse  one per task in bench/tasks/

Every solution is re-run through the interpreter and its stdout is compared
byte-for-byte against the task's Python reference (bench/tasks/<task>.py).
Any mismatch -> the whole submission is rejected (exit 1, no entry printed).
On success, the results.json entry is printed to stdout.

Token counting: tiktoken cl100k_base when importable, else a documented
whitespace/punctuation fallback, labeled in the entry's "tokenizer" field.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "bench" / "tasks"
GAS = 1_000_000

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def _fallback_count(src: str) -> int:
    return len(_TOKEN_RE.findall(src))


def _tokenizer():
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return "tiktoken cl100k_base", enc.encode
    except Exception:
        return "fallback (whitespace/punctuation split)", _fallback_count


def _count(encode_fn, src: str) -> int:
    toks = encode_fn(src)
    return len(toks) if not isinstance(toks, int) else toks


def _run_terse(src: str) -> str:
    from terse import interp

    value, output = interp.run_source(src, gas=GAS)
    return output


def _run_python(path: Path) -> str:
    proc = subprocess.run(
        [sys.executable, str(path)],
        capture_output=True, text=True, timeout=120, cwd=str(ROOT),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"reference {path.name} crashed: {proc.stderr[-200:]}")
    return proc.stdout


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    sub = Path(argv[0])
    meta_path = sub / "meta.json"
    sol_dir = sub / "solutions"
    if not meta_path.is_file() or not sol_dir.is_dir():
        print(f"error: {sub} needs meta.json and solutions/", file=sys.stderr)
        return 2
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    for key in ("model", "date"):
        if key not in meta:
            print(f"error: meta.json missing {key!r}", file=sys.stderr)
            return 2

    tok_label, encode_fn = _tokenizer()
    tasks = sorted(p.stem for p in TASKS.glob("*.terse"))
    per_task: dict[str, dict] = {}
    ratios: list[float] = []
    for task in tasks:
        sol = sol_dir / f"{task}.terse"
        ref_py = TASKS / f"{task}.py"
        if not sol.is_file():
            print(f"error: missing solution for task {task!r}", file=sys.stderr)
            return 2
        terse_src = sol.read_text(encoding="utf-8")
        py_src = ref_py.read_text(encoding="utf-8")
        try:
            terse_out = _run_terse(terse_src)
        except Exception as exc:  # noqa: BLE001
            print(f"REJECTED: {task}.terse failed to run: {exc}", file=sys.stderr)
            return 1
        py_out = _run_python(ref_py)
        if terse_out != py_out:
            print(f"REJECTED: {task} stdout mismatch", file=sys.stderr)
            print(f"--- terse ---\n{terse_out[:500]}", file=sys.stderr)
            print(f"--- python ---\n{py_out[:500]}", file=sys.stderr)
            return 1
        tt = _count(encode_fn, terse_src)
        pt = _count(encode_fn, py_src)
        ratio = round(tt / pt, 2) if pt else 0.0
        per_task[task] = {
            "terse_tokens": tt,
            "python_tokens": pt,
            "ratio": ratio,
            "stdout_match": True,
        }
        ratios.append(tt / pt if pt else 0.0)

    entry = {
        "model": meta["model"],
        "kind": "model",
        "date": meta["date"],
        "per_task": per_task,
        "avg_ratio": round(sum(ratios) / len(ratios), 2),
        "notes": meta.get("notes", ""),
    }
    # Anomaly flags for the publisher's review queue (not silent rejection).
    flags = []
    if entry["avg_ratio"] < 0.5:
        flags.append("avg_ratio<0.5:hold for manual review")
    for task, p in per_task.items():
        if p["ratio"] < 0.4:
            flags.append(f"{task} ratio<0.4:hold for manual review")
    if flags:
        entry["review_flags"] = flags
    print(json.dumps(entry, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
