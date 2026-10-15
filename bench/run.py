"""TTCP benchmark harness (v0.1 skeleton).

TTCP = "tokens to first correct program": how many tokens an AI coding
agent must emit to produce a working solution for a task. v0.1 measures
SOLUTION TOKENS ONLY -- the size of each task's reference solution written
in Terse versus idiomatic Python. Repair-loop accounting (tokens spent on
failed attempts plus fixes, driven by the interpreter's structured errors)
lands in v0.2.

For each task in bench/tasks/ (<name>.terse + <name>.py), this harness:
  (a) runs the .terse through the interpreter, capturing stdout;
  (b) runs the .py via subprocess, capturing stdout;
  (c) asserts identical stdout (reports MISMATCH otherwise);
  (d) counts tokens of each source file with tiktoken `cl100k_base` when
      importable, else a documented fallback (whitespace/punctuation
      split), clearly labeled as such;
  (e) prints a markdown table: task | terse tokens | python tokens |
      ratio (terse/python) | stdout match, plus which tokenizer was used.

Expected interpreter interface (terse.interp, built by the interpreter
worker): any ONE of these is enough --
  * terse.interp.run_file(path)  -> stdout str, or (result, stdout)
  * terse.interp.run_source(src)  -> stdout str, or (result, stdout)
  * terse.interp.run(...)        -> as above
  * `python -m terse.interp <file.terse>` printing the program's stdout
If none is available the task is reported as SKIP (no interpreter) and the
token counts are still reported.

Run from the repo root:
    python -m bench.run
Exit code is 1 if any task MISMATCHes or errors, 0 otherwise (SKIPs from a
missing interpreter do not fail the run).
"""

from __future__ import annotations

import argparse
import io
import re
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "bench" / "tasks"

ENCODINGS = ("cl100k_base", "o200k_base", "p50k_base")


# --- tokenizers ---------------------------------------------------------------

_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def _fallback_count(src: str) -> int:
    """Fallback token estimate: split into word and punctuation tokens."""
    return len(_TOKEN_RE.findall(src))


def _get_tokenizer(encoding: str = "cl100k_base") -> tuple[str, object]:
    """Return (label, encode_fn) for the requested tiktoken encoding.

    Raises SystemExit(2) with a clear message if tiktoken or the
    encoding is unavailable.
    """
    try:
        import tiktoken  # type: ignore
    except Exception:
        print("error: tiktoken is not installed; cannot use "
              f"--tokenizer {encoding}", file=sys.stderr)
        sys.exit(2)
    try:
        enc = tiktoken.get_encoding(encoding)
    except Exception as exc:
        print(f"error: tiktoken encoding {encoding!r} is unavailable: {exc}",
              file=sys.stderr)
        print(f"supported encodings: {', '.join(ENCODINGS)}", file=sys.stderr)
        sys.exit(2)
    return f"tiktoken {encoding}", enc.encode


def _legacy_tokenizer() -> tuple[str, object]:
    """Original default: cl100k_base, else the documented fallback."""
    try:
        return _get_tokenizer("cl100k_base")
    except SystemExit:
        return "fallback (whitespace/punctuation split)", _fallback_count


def _count_tokens(encode_fn, path: Path) -> int:
    src = path.read_text(encoding="utf-8")
    toks = encode_fn(src)
    return len(toks) if not isinstance(toks, int) else toks


# --- running the .terse side through the interpreter --------------------------

def _capture(fn, *args):
    """Call fn(*args); return its stdout as str, or None if inconclusive."""
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            ret = fn(*args)
    except SystemExit:
        ret = None
    if isinstance(ret, str):
        return ret
    if isinstance(ret, (tuple, list)):
        strs = [x for x in ret if isinstance(x, str)]
        if strs:
            return strs[-1]
    printed = buf.getvalue()
    return printed if printed else None


def _run_terse(path: Path) -> tuple[str, str | None, str]:
    """Run a .terse file through the interpreter.

    Returns (status, stdout, note) where status is "ok", "no-interp", or
    "error".
    """
    interp = None
    try:
        from terse import interp  # type: ignore
    except ImportError:
        interp = None

    if interp is not None:
        src = path.read_text(encoding="utf-8")
        candidates = []
        for name, arg in (
            ("run_file", str(path)),
            ("run_source", src),
            ("run", src),
        ):
            fn = getattr(interp, name, None)
            if callable(fn):
                candidates.append((name, fn, arg))
        fn = getattr(interp, "main", None)
        if callable(fn):
            candidates.append(("main", fn, None))
        for name, fn, arg in candidates:
            try:
                out = _capture(fn) if arg is None else _capture(fn, arg)
            except TypeError:
                continue  # wrong signature for this candidate; try next
            except Exception as exc:  # noqa: BLE001 - report, don't crash
                return ("error", None,
                        f"terse.interp.{name} raised "
                        f"{type(exc).__name__}: {exc}")
            if out is not None:
                return ("ok", out, f"terse.interp.{name}")

    # Fallback: interpreter CLI as a subprocess.
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "terse.interp", str(path)],
            capture_output=True, text=True, timeout=120, cwd=str(ROOT),
        )
    except Exception as exc:  # noqa: BLE001
        return ("no-interp", None, f"interpreter subprocess failed: {exc}")
    if "No module named" in (proc.stderr or ""):
        return ("no-interp", None,
                "terse.interp not importable and has no CLI "
                "(interpreter worker hasn't landed it yet)")
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        return ("error", None,
                f"CLI exited {proc.returncode}: "
                f"{err[-1] if err else 'no stderr'}")
    return ("ok", proc.stdout, "python -m terse.interp")


def _run_python(path: Path) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, str(path)],
        capture_output=True, text=True, timeout=120, cwd=str(ROOT),
    )
    return proc.returncode, proc.stdout, proc.stderr


# --- main ---------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="TTCP benchmark: Terse vs Python token ratios.")
    parser.add_argument(
        "--tokenizer", default=None, choices=ENCODINGS,
        help=("tiktoken encoding to count with "
              "(default: cl100k_base, with legacy fallback)"))
    args = parser.parse_args(argv)

    if not TASKS.is_dir():
        print(f"error: tasks directory not found: {TASKS}", file=sys.stderr)
        return 2

    if args.tokenizer is None:
        tok_label, encode_fn = _legacy_tokenizer()
    else:
        tok_label, encode_fn = _get_tokenizer(args.tokenizer)
    print(f"tokenizer: {tok_label}\n")

    names = sorted(p.stem for p in TASKS.glob("*.terse"))
    if not names:
        print("no tasks found in bench/tasks/", file=sys.stderr)
        return 2

    rows = []
    failed = False
    for name in names:
        terse_path = TASKS / f"{name}.terse"
        py_path = TASKS / f"{name}.py"
        if not py_path.exists():
            print(f"warning: {name}.py missing, skipping task", file=sys.stderr)
            continue

        terse_tokens = _count_tokens(encode_fn, terse_path)
        py_tokens = _count_tokens(encode_fn, py_path)
        ratio = terse_tokens / py_tokens if py_tokens else float("nan")

        status, terse_out, note = _run_terse(terse_path)
        if status == "no-interp":
            match = f"SKIP ({note})"
        elif status == "error":
            match = f"ERROR ({note})"
            failed = True
        else:
            rc, py_out, py_err = _run_python(py_path)
            if rc != 0:
                err = py_err.strip().splitlines()
                match = f"ERROR (python exited {rc}: {err[-1] if err else ''})"
                failed = True
            elif terse_out == py_out:
                match = "MATCH"
            else:
                match = "MISMATCH"
                failed = True

        rows.append((name, terse_tokens, py_tokens, ratio, match))

    print("| task | terse tokens | python tokens | ratio (terse/python) | stdout match |")
    print("|---|---|---|---|---|")
    for name, tt, pt, ratio, match in rows:
        print(f"| {name} | {tt} | {pt} | {ratio:.2f} | {match} |")
    print()
    print("TTCP v0.1 reads the ratio column: tokens for an equivalent "
          "reference solution in Terse vs idiomatic Python. Values < 1 mean "
          "the Terse solution is token-cheaper for an agent to emit.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
