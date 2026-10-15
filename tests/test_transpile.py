"""Transpiler tests: to_python output compiles, execs, and matches the interpreter."""
import contextlib
import io

import pytest

import terse
from terse import parse, run_source, to_python
from terse.errors import TranspileError

# (name, source) — a spread of language features.
PROGRAMS = [
    ("fib",
     "(let fib (fn (n) (if (le n 1) n "
     "(add (fib (sub n 1)) (fib (sub n 2))))) "
     "(print (fib 10)))"),
    ("fib-def",
     "(def fib (n) (if (le n 1) n "
     "(add (fib (sub n 1)) (fib (sub n 2))))) "
     "(print (fib 10))"),
    ("fizzbuzz-fragment",
     "(for i (range 1 6) "
     "(print (if (eq (mod i 3) 0) \"fizz\" "
     "(eq (mod i 5) 0) \"buzz\" i)))"),
    ("nary-if",
     "(print (if false \"a\" true \"b\" \"c\"))"),
    ("letscope",
     "(let x 1 (seq (let x 2 (print x)) (print x)))"),
    ("string-ops",
     "(print (join \"-\" (list (upper \"a\") (lower \"B\") (cat \"x\" 1))))"),
    ("dict-list-ops",
     "(let d (dict \"a\" 1) "
     "(let l (push (list 1) 2) "
     "(print (cat (str (get (put d \"b\" 3) \"b\")) \",\" (str (len l))))))"),
    ("early-return",
     "(let f (fn (n) (seq (if (lt n 0) (return 0)) (mul n 2))) "
     "(print (f (neg 5))))"),
    ("while-accum",
     "(let go (fn (n acc) "
     "(if (le n 0) acc (go (sub n 1) (add acc n)))) "
     "(print (go 100 0)))"),
    ("fold-sum",
     "(print (fold (acc x) 0 (list 1 2 3) (add acc x)))"),
    ("fold-inc",
     "(print (fold (acc w) (dict) (list \"a\" \"b\" \"a\") (inc acc w)))"),
    ("computed-head-call",
     "(print ((fn (x) (mul x 2)) 21))"),
]


def _exec_terse(py_src):
    """Compile-check, exec, and return (namespace, captured stdout)."""
    code = compile(py_src, "<terse-test>", "exec")
    ns = {}
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(code, ns)
    return ns, buf.getvalue()


@pytest.mark.parametrize("name,src", PROGRAMS, ids=[p[0] for p in PROGRAMS])
def test_transpile_matches_interpreter(name, src):
    interp_value, interp_output = run_source(src)

    py_src = to_python(parse(src))
    compile(py_src, "<terse-test>", "exec")  # must be valid Python

    ns, printed = _exec_terse(py_src)
    assert "_terse_result" in ns
    assert ns["_terse_result"] == interp_value
    assert printed == interp_output


def test_to_python_idempotent_across_calls():
    # Regression: terse.to_python must stay the function, not the submodule.
    for _ in range(3):
        py_src = to_python(parse("(add 1 2)"))
        ns, _ = _exec_terse(py_src)
        assert ns["_terse_result"] == 3


def test_generated_code_has_prelude_and_result():
    py_src = to_python(parse('(print "hi")'))
    assert "_terse_result" in py_src
    assert "def _terse_print" in py_src


def test_return_in_expression_position_is_e300():
    with pytest.raises(TranspileError) as excinfo:
        to_python(parse("(fn (x) (add (return 1) 2))"))
    assert excinfo.value.code == "E300"
    assert excinfo.value.phase == "transpile"


def test_return_outside_fn_is_e300():
    with pytest.raises(TranspileError) as excinfo:
        to_python(parse("(return 1)"))
    assert excinfo.value.code == "E300"


def test_return_in_let_body_is_e300():
    # `return` is only legal in the fn body's statement positions;
    # a let body discards its value, so returning from it is rejected.
    src = "(let f (fn (x) (let y 1 (return y))) (f 1))"
    with pytest.raises(TranspileError) as excinfo:
        to_python(parse(src))
    assert excinfo.value.code == "E300"


def test_return_in_fn_body_seq_is_allowed():
    # Statement-position return inside the fn body is fine.
    src = ("(let f (fn (x) (seq (if (lt x 0) (return 0)) x)) "
           "(print (f (neg 3))))")
    py_src = to_python(parse(src))
    ns, printed = _exec_terse(py_src)
    assert printed == "0\n"
    assert ns["_terse_result"] is None


def test_def_in_value_position_is_e300():
    # def is statement-only.
    with pytest.raises(TranspileError) as excinfo:
        to_python(parse("(add (def f (x) x) 1)"))
    assert excinfo.value.code == "E300"


def test_unbound_variable_is_e300_at_transpile_time():
    with pytest.raises(TranspileError) as excinfo:
        to_python(parse("(add x 1)"))
    assert excinfo.value.code == "E300"


def test_shadowing_transpiles_correctly():
    src = "(let x 1 (seq (let x 2 (print x)) (print x)))"
    ns, printed = _exec_terse(to_python(parse(src)))
    assert printed == "2\n1\n"
