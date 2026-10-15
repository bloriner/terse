"""Benchmark smoke test: `python -m bench.run` exits 0 and all tasks MATCH."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_bench_run_exits_zero_with_all_matches():
    proc = subprocess.run(
        [sys.executable, "-m", "bench.run"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, (
        f"bench.run exited {proc.returncode}\n"
        f"stdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
    # The markdown table marks each task's stdout comparison; expect a MATCH
    # for every task and no MISMATCH/ERROR rows.
    n_tasks = len(list((ROOT / "bench" / "tasks").glob("*.terse")))
    assert proc.stdout.count("| MATCH |") == n_tasks
    assert "MISMATCH" not in proc.stdout
    assert "| ERROR" not in proc.stdout
