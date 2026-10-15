"""Dialect tests: `bench.run --tokenizer` works per encoding and ratios stay sane.

Covers notes/dialects.md: cl100k_base and o200k_base must agree closely,
p50k_base must still pass, and every encoding's ratios must stay within
a sane bound (0.5-1.5) with all tasks MATCHing.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROW_RE = re.compile(r"^\| (\S+) \| (\d+) \| (\d+) \| ([\d.]+) \| (.+) \|$")


def _run_bench(tokenizer):
    proc = subprocess.run(
        [sys.executable, "-m", "bench.run", "--tokenizer", tokenizer],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    return proc


def _parse_rows(stdout):
    rows = {}
    for line in stdout.splitlines():
        m = ROW_RE.match(line.strip())
        if m:
            task, tt, pt, ratio, match = m.groups()
            rows[task] = (int(tt), int(pt), float(ratio), match.strip())
    return rows


def _available_encodings():
    try:
        import tiktoken  # type: ignore
    except ImportError:
        return []
    out = []
    for enc in ("cl100k_base", "o200k_base", "p50k_base"):
        try:
            tiktoken.get_encoding(enc)
            out.append(enc)
        except Exception:
            pass
    return out


def test_tokenizer_flag_runs_per_encoding_with_sane_ratios():
    encodings = _available_encodings()
    assert encodings, "no tiktoken encodings available; cannot test dialects"
    n_tasks = len(list((ROOT / "bench" / "tasks").glob("*.terse")))
    for enc in encodings:
        proc = _run_bench(enc)
        assert proc.returncode == 0, (
            f"--tokenizer {enc} exited {proc.returncode}\n{proc.stderr}"
        )
        assert f"tiktoken {enc}" in proc.stdout
        rows = _parse_rows(proc.stdout)
        assert len(rows) == n_tasks, (
            f"--tokenizer {enc}: parsed {len(rows)} rows, "
            f"expected {n_tasks}\n{proc.stdout}"
        )
        for task, (tt, pt, ratio, match) in rows.items():
            assert match == "MATCH", f"{enc}/{task}: {match}"
            assert 0.5 <= ratio <= 1.5, f"{enc}/{task}: ratio {ratio}"


def test_cl100k_and_o200k_agree():
    """notes/dialects.md: the two modern encodings must stay in lockstep."""
    encodings = _available_encodings()
    if "cl100k_base" not in encodings or "o200k_base" not in encodings:
        return
    ratios = {}
    for enc in ("cl100k_base", "o200k_base"):
        proc = _run_bench(enc)
        assert proc.returncode == 0
        ratios[enc] = {t: r for t, (_, _, r, _) in
                       _parse_rows(proc.stdout).items()}
    assert ratios["cl100k_base"].keys() == ratios["o200k_base"].keys()
    for task in ratios["cl100k_base"]:
        a, b = ratios["cl100k_base"][task], ratios["o200k_base"][task]
        assert abs(a - b) < 0.05, f"{task}: cl100k {a} vs o200k {b}"


def test_invalid_tokenizer_rejected():
    proc = subprocess.run(
        [sys.executable, "-m", "bench.run", "--tokenizer", "not_a_real_enc"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 2, f"expected exit 2, got {proc.returncode}"
