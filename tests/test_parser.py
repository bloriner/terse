"""Parser tests: valid programs -> expected AST tuples; malformed input -> ParseError codes."""
import pytest

from terse import parse, try_parse
from terse.errors import ParseError

ALL_KEYS = {"code", "phase", "op", "pos", "message", "expected", "got", "hint"}


# --- valid programs -> AST -------------------------------------------------


def test_simple_call_ast():
    assert parse("(add 1 2)") == ("add", 1, 2)


def test_let_ast():
    assert parse("(let x 5 (add x 1))") == (
        "let", "x", 5, ("add", ("var", "x"), 1))


def test_fn_params_are_string_lists():
    ast = parse("(fn (a b) (add a b))")
    assert ast == ("fn", ["a", "b"], ("add", ("var", "a"), ("var", "b")))
    assert isinstance(ast[1], list)
    assert all(isinstance(p, str) for p in ast[1])


def test_call_ast():
    # Head-position call: (f 1 2) == ("call", ("var", "f"), 1, 2).
    assert parse("(f 1 2)") == ("call", ("var", "f"), 1, 2)


def test_call_head_computed():
    # Computed function position: ((fn (x) x) 5).
    assert parse("((fn (x) x) 5)") == (
        "call", ("fn", ["x"], ("var", "x")), 5)


def test_call_zero_args():
    assert parse("(f)") == ("call", ("var", "f"))


def test_bare_call_word_is_var_call():
    # `call` is no longer an op; (call f 1 2) is a call to var `call`.
    assert parse("(call f 1 2)") == (
        "call", ("var", "call"), ("var", "f"), 1, 2)


def test_if_ast():
    assert parse("(if (lt x 10) x (neg x))") == (
        "if", ("lt", ("var", "x"), 10), ("var", "x"), ("neg", ("var", "x")))


def test_for_ast():
    assert parse("(for i (range 10) (print i))") == (
        "for", "i", ("range", 10), ("print", ("var", "i")))


def test_dict_ast():
    assert parse('(dict "a" 1 "b" 2)') == ("dict", "a", 1, "b", 2)


def test_bare_symbol_is_varref():
    assert parse("x") == ("var", "x")


def test_literals():
    assert parse("42") == 42
    assert parse("-7") == -7
    assert parse("3.5") == 3.5
    assert parse("-2.5") == -2.5
    assert parse("true") is True
    assert parse("false") is False
    assert parse("null") is None
    assert parse('"hi"') == "hi"
    assert parse(r'"a\nb\tc\"d\\e"') == 'a\nb\tc"d\\e'


def test_uppercase_true_is_symbol_not_literal():
    assert parse("True") == ("var", "True")


def test_multiple_top_level_forms_become_seq():
    assert parse("(print 1) (print 2)") == (
        "seq", ("print", 1), ("print", 2))


def test_single_form_is_not_wrapped():
    assert parse("(add 1 2)") == ("add", 1, 2)


def test_comments_and_whitespace_ignored():
    src = """
    ; a full-line comment
    (add 1 ; trailing comment
         2)
    """
    assert parse(src) == ("add", 1, 2)


def test_deeply_nested():
    assert parse("(add (mul 2 3) (sub 10 (div 8 2)))") == (
        "add", ("mul", 2, 3), ("sub", 10, ("div", 8, 2)))


# --- malformed input -> ParseError with the right code ----------------------


def _code(src):
    with pytest.raises(ParseError) as excinfo:
        parse(src)
    return excinfo.value.code


def test_e100_unclosed_paren():
    assert _code("(add 1") == "E100"


def test_e100_empty_program():
    assert _code("") == "E100"
    assert _code("   ; only a comment\n  ") == "E100"


def test_e104_unterminated_string_in_form():
    # An unterminated string inside a form is E104 (bad literal).
    assert _code('(print "abc') == "E104"


def test_e101_stray_rparen():
    assert _code(")") == "E101"


def test_e101_empty_list():
    assert _code("()") == "E101"


def test_e101_non_symbol_head():
    assert _code("(1 2 3)") == "E101"


def test_unknown_head_is_call_not_e102():
    # Unknown heads are head-position calls now, not parse errors.
    # A typo like (ad 1 2) parses fine; the interpreter's E200 suggests
    # 'add' at runtime (see test_interp.py).
    assert parse("(ad 1 2)") == ("call", ("var", "ad"), 1, 2)


def test_if_nary_ast():
    assert parse("(if a b c d e)") == (
        "if", ("var", "a"), ("var", "b"), ("var", "c"), ("var", "d"),
        ("var", "e"))


def test_def_ast():
    assert parse("(def fib (n) (fib n))") == (
        "def", "fib", ["n"], ("call", ("var", "fib"), ("var", "n")))


def test_fold_ast():
    assert parse("(fold (acc x) 0 (list 1) acc)") == (
        "fold", ["acc", "x"], 0, ("list", 1), ("var", "acc"))


def test_juxtaposed_parens_parse():
    # No whitespace needed around parens.
    assert parse("(eq(mod i 15)0)") == (
        "eq", ("mod", ("var", "i"), 15), 0)


def test_e103_arity_too_few():
    assert _code("(add 1)") == "E103"


def test_e103_arity_too_many():
    assert _code("(add 1 2 3)") == "E103"


def test_e103_dict_odd_args():
    assert _code('(dict "a" 1 "b")') == "E103"


def test_e103_and_needs_two():
    assert _code("(and true)") == "E103"


def test_e104_unterminated_string():
    assert _code('"abc') == "E104"


def test_e104_bad_escape():
    assert _code(r'"a\qb"') == "E104"


def test_e104_newline_in_string():
    assert _code('"ab\ncd"') == "E104"


def test_e105_let_non_symbol_name():
    assert _code("(let 5 1 2)") == "E105"


def test_e105_for_non_symbol_name():
    assert _code('(for "i" (range 3) 1)') == "E105"


def test_e105_fn_params_not_a_list():
    assert _code("(fn x x)") == "E105"


def test_e105_fn_non_symbol_param():
    assert _code("(fn (1) 1)") == "E105"


# --- try_parse --------------------------------------------------------------


def test_try_parse_success():
    ast, errs = try_parse("(add 1 2)")
    assert ast == ("add", 1, 2)
    assert errs == []


def test_try_parse_failure_returns_none_and_dicts():
    ast, errs = try_parse("(add 1)")
    assert ast is None
    assert len(errs) == 1
    d = errs[0]
    assert set(d.keys()) == ALL_KEYS
    assert d["code"] == "E103"
    assert d["phase"] == "parse"
    assert d["op"] == "add"


def test_try_parse_never_raises():
    for bad in ["(add 1", "(add 1)", '"abc', "(let 5 1 2)", ""]:
        ast, errs = try_parse(bad)
        assert ast is None
        assert isinstance(errs, list) and len(errs) == 1


def test_parse_error_fields():
    with pytest.raises(ParseError) as excinfo:
        parse("(add 1")
    err = excinfo.value
    assert err.code == "E100"
    assert err.phase == "parse"
    assert err.pos is not None
    assert set(err.pos.keys()) == {"line", "col", "index"}
    assert isinstance(err.message, str) and err.message
