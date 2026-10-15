"""Example-program tests: every examples/*.terse must produce its expected stdout,
both via the interpreter and via the transpiler."""
import contextlib
import io
import json
from pathlib import Path

import pytest

from terse import parse, run_source, to_python

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


def _load_expected():
    with open(EXAMPLES / "expected.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize(
    "name", sorted(_load_expected().keys()), ids=sorted(_load_expected().keys())
)
def test_example_stdout_via_interpreter(name):
    expected = _load_expected()
    src = (EXAMPLES / name).read_text(encoding="utf-8")
    value, output = run_source(src)
    assert output == expected[name], (
        f"{name}: interpreter stdout differs\n"
        f"expected {expected[name]!r}\n"
        f"got      {output!r}"
    )


@pytest.mark.parametrize(
    "name", sorted(_load_expected().keys()), ids=sorted(_load_expected().keys())
)
def test_example_stdout_via_transpiler(name):
    expected = _load_expected()
    src = (EXAMPLES / name).read_text(encoding="utf-8")
    py_src = to_python(parse(src))
    compile(py_src, f"<terse:{name}>", "exec")  # must be valid Python
    ns: dict = {}
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(compile(py_src, f"<terse:{name}>", "exec"), ns)
    printed = buf.getvalue()
    assert printed == expected[name], (
        f"{name}: transpiled stdout differs\n"
        f"expected {expected[name]!r}\n"
        f"got      {printed!r}"
    )


def test_every_terse_file_has_an_expectation():
    expected = _load_expected()
    terse_files = {p.name for p in EXAMPLES.glob("*.terse")}
    assert terse_files == set(expected.keys()), (
        f"examples out of sync: files={sorted(terse_files)} "
        f"expectations={sorted(expected.keys())}"
    )
