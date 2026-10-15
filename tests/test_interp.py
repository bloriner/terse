"""Interpreter tests: every op, plus the runtime error codes."""
import pytest

import terse
from terse import parse, run, run_source
from terse.errors import TerseRuntimeError


def ev(src, gas=100_000):
    """Evaluate source, returning (value, output)."""
    return run_source(src, gas=gas)


def val(src, gas=100_000):
    return ev(src, gas=gas)[0]


def out(src, gas=100_000):
    return ev(src, gas=gas)[1]


def _rcode(src, gas=100_000):
    with pytest.raises(TerseRuntimeError) as excinfo:
        run_source(src, gas=gas)
    return excinfo.value.code


# --- arithmetic -------------------------------------------------------------


def test_arithmetic():
    assert val("(add 1 2)") == 3
    assert val("(sub 5 3)") == 2
    assert val("(mul 2 3)") == 6
    assert val("(div 7 2)") == 3.5          # true division, never floor
    assert val("(div 6 2)") == 3.0
    assert val("(mod 7 3)") == 1
    assert val("(pow 2 10)") == 1024
    assert val("(neg 5)") == -5
    assert val("(neg -3)") == 3


def test_int_float_mixing():
    assert val("(add 1 2.5)") == 3.5
    assert isinstance(val("(add 1 2.5)"), float)
    assert val("(mul 2 0.5)") == 1.0


def test_str_concat_via_add():
    assert val('(add "a" "b")') == "ab"


def test_add_mixed_str_number_is_e208():
    assert _rcode('(add "a" 1)') == "E208"


# --- comparisons ------------------------------------------------------------


def test_comparisons():
    assert val("(eq 1 1)") is True
    assert val("(eq 1 1.0)") is True       # cross int/float compare by value
    assert val("(ne 1 2)") is True
    assert val("(lt 1 2)") is True
    assert val("(le 2 2)") is True
    assert val("(gt 3 2)") is True
    assert val("(ge 3 3)") is True
    assert val("(lt 2 1)") is False
    assert val('(eq "a" "a")') is True


# --- logic: short-circuit, deciding operand ---------------------------------


def test_and_or_return_deciding_operand():
    assert val("(and 1 2 3)") == 3
    assert val("(and 0 5)") == 0
    assert val('(or 0 "" "x")') == "x"
    assert val("(or false false)") is False
    assert val("(and true true)") is True


def test_and_short_circuits():
    # The skipped branch would divide by zero; no error means it never ran.
    assert val("(and false (div 1 0))") is False
    assert val("(and 0 (div 1 0))") == 0


def test_or_short_circuits():
    assert val("(or true (div 1 0))") is True
    assert val('(or "x" (div 1 0))') == "x"


def test_not():
    assert val("(not false)") is True
    assert val("(not true)") is False
    assert val("(not 0)") is True          # truthiness, not strict boolean
    assert val('(not "x")') is False
    assert val("(not null)") is True


# --- control flow ------------------------------------------------------------


def test_if():
    assert val("(if true 1 2)") == 1
    assert val("(if false 1 2)") == 2
    assert val("(if false 1)") is None     # no else -> null
    assert val("(if 1 2 3)") == 2          # truthy condition


def test_while_noop():
    value, output = ev('(while false (print "x"))')
    assert value is None
    assert output == ""


def test_for_over_list():
    assert out("(for i (list 1 2 3) (print i))") == "1\n2\n3\n"


def test_for_over_string_iterates_chars():
    assert out('(for c "ab" (print c))') == "a\nb\n"


def test_for_value_is_null():
    assert val("(for i (list 1) 1)") is None


def test_seq():
    assert val("(seq 1 2 3)") == 3
    assert val("(seq)") is None


# --- let / shadowing / recursion --------------------------------------------


def test_let_basic():
    assert val("(let x 5 (add x 1))") == 6


def test_let_shadowing():
    assert val("(let x 1 (let x 2 (add x 10)))") == 12
    assert val("(let x 1 (seq (let x 2 x) x))") == 1


def test_letrec_fib():
    src = ("(let fib (fn (n) (if (le n 1) n "
           "(add (fib (sub n 1)) (fib (sub n 2))))) "
           "(fib 10))")
    assert val(src) == 55


def test_closure_captures_defining_env():
    # Lexical scoping: f sees x=10 from its definition site, not x=99.
    src = "(let x 10 (let f (fn (y) (add x y)) (let x 99 (f 1))))"
    assert val(src) == 11


# --- fn / call / return ------------------------------------------------------


def test_fn_call():
    assert val("(let f (fn (a b) (add a b)) (f 3 4))") == 7


def test_fn_zero_params():
    assert val("(let f (fn () 42) (f))") == 42


def test_early_return():
    src = ("(let f (fn (n) (seq (if (lt n 0) (return 0)) (mul n 2))) "
           "(f (neg 5)))")
    assert val(src) == 0


def test_no_early_return_taken():
    src = ("(let f (fn (n) (seq (if (lt n 0) (return 0)) (mul n 2))) "
           "(f 21))")
    assert val(src) == 42


def test_higher_order():
    src = ("(let apply (fn (f x) (f x)) "
           "(let inc (fn (n) (add n 1)) (apply inc 41)))")
    assert val(src) == 42


def test_def_fib():
    src = ("(def fib (n) (if (le n 1) n "
           "(add (fib (sub n 1)) (fib (sub n 2))))) "
           "(print (fib 10))")
    assert out(src) == "55\n"


def test_def_evaluates_to_null():
    assert val("(def f (x) x)") is None


def test_def_recursion():
    src = "(def fact (n) (if (le n 1) 1 (mul n (fact (dec n)))))(fact 5)"
    assert val(src) == 120


def test_fold_sum():
    assert val("(fold (acc x) 0 (list 1 2 3 4) (add acc x))") == 10


def test_fold_builds_list():
    assert val("(fold (acc x) (list) (list 1 2) (push acc x))") == [1, 2]


def test_fold_empty():
    assert val('(fold (acc x) 7 (list) acc)') == 7


def test_inc_basic():
    assert val('(inc (dict "a" 1) "a")') == {"a": 2}


def test_inc_missing_key_defaults_zero():
    assert val('(inc (dict) "x")') == {"x": 1}


def test_inc_by():
    assert val('(inc (dict "a" 1) "a" 5)') == {"a": 6}


def test_inc_is_persistent():
    src = '(let d (dict "a" 1) (seq (inc d "a") d))'
    assert val(src) == {"a": 1}


def test_dec():
    assert val("(dec 5)") == 4
    assert val("(dec 0)") == -1


def test_nary_if():
    assert val("(if false 1 false 2 3)") == 3
    assert val("(if true 1 false 2 3)") == 1
    assert val("(if false 1 true 2 3)") == 2
    assert val("(if false 1 false 2)") is None


def test_e200_typo_hint():
    # (ad 1 2) parses as a call now; the runtime suggests 'add'.
    with pytest.raises(TerseRuntimeError) as excinfo:
        val("(ad 1 2)")
    assert excinfo.value.code == "E200"
    assert "did you mean 'add'" in (excinfo.value.hint or "")


# --- collections -------------------------------------------------------------


def test_list_dict_len():
    assert val("(list 1 2 3)") == [1, 2, 3]
    assert val("(list)") == []
    assert val('(dict "a" 1 "b" 2)') == {"a": 1, "b": 2}
    assert val("(len (list 1 2))") == 2
    assert val('(len (dict "a" 1))') == 1
    assert val('(len "abc")') == 3


def test_get():
    assert val('(get (dict "a" 1) "a")') == 1
    assert val("(get (list 10 20) 1)") == 20
    assert val("(get (list 10 20) (neg 1))") == 20  # negative index ok


def test_put_returns_new_dict():
    assert val('(get (put (dict "a" 1) "b" 2) "b")') == 2


def test_put_dict_original_untouched():
    src = '(let d (dict "a" 1) (seq (put d "b" 2) (len d)))'
    assert val(src) == 1


def test_put_list_original_untouched():
    src = "(let l (list 1 2 3) (seq (put l 1 99) l))"
    assert val(src) == [1, 2, 3]
    assert val("(put (list 1 2 3) 1 99)") == [1, 99, 3]


def test_range():
    assert val("(range 3)") == [0, 1, 2]
    assert val("(range 1 4)") == [1, 2, 3]
    assert val("(range 0 10 3)") == [0, 3, 6, 9]


def test_push_returns_new_list():
    assert val("(push (list 1) 2)") == [1, 2]
    src = "(let l (list 1) (seq (push l 2) l))"
    assert val(src) == [1]                # original untouched


def test_cat():
    assert val('(cat "a" 1 true)') == "a1True"
    assert val("(cat)") == ""


# --- strings / conversions ----------------------------------------------------


def test_str_int_float():
    assert val("(str 42)") == "42"
    assert val("(str null)") == "None"
    assert val('(int "42")') == 42
    assert val("(int 3.9)") == 3           # float truncation allowed
    assert val('(float "3.5")') == 3.5
    assert val("(float 2)") == 2.0


def test_upper_lower():
    assert val('(upper "ab")') == "AB"
    assert val('(lower "AB")') == "ab"


def test_split_join():
    assert val('(split "a,b,c" ",")') == ["a", "b", "c"]
    assert val('(join ", " (list "a" "b"))') == "a, b"
    assert val('(join "-" (list))') == ""


# --- print / assert ------------------------------------------------------------


def test_print_returns_none_and_captures():
    value, output = ev('(print "hello")')
    assert value is None
    assert output == "hello\n"


def test_print_multiple_args():
    assert out("(print 1 \"two\" 3.0)") == "1 two 3.0\n"


def test_print_no_args_prints_blank_line():
    assert out("(print)") == "\n"


def test_assert_passes():
    value, output = ev('(assert (gt 2 1) "x>0")')
    assert value is None
    assert output == ""


def test_assert_fails_e204():
    assert _rcode('(assert false "boom")') == "E204"
    with pytest.raises(TerseRuntimeError) as excinfo:
        run_source('(assert false "boom")')
    assert "boom" in str(excinfo.value)


# --- runtime errors ------------------------------------------------------------


def test_e200_unbound_variable():
    assert _rcode("x") == "E200"
    assert _rcode("(add x 1)") == "E200"


def test_e201_division_by_zero():
    assert _rcode("(div 1 0)") == "E201"
    assert _rcode("(mod 1 0)") == "E201"
    assert _rcode("(div 1.0 0.0)") == "E201"


def test_e202_missing_key_and_bad_index():
    assert _rcode('(get (dict "a" 1) "b")') == "E202"
    assert _rcode("(get (list 1 2) 5)") == "E202"
    assert _rcode('(put (list 1 2) 9 0)') == "E202"


def test_e203_conversion_error():
    assert _rcode('(int "abc")') == "E203"
    assert _rcode('(float "xyz")') == "E203"
    assert _rcode('(int "3.9")') == "E203"   # no silent truncation of strings


def test_e205_gas_exhaustion():
    assert _rcode("(while true 1)", gas=50) == "E205"


def test_gas_sufficient_terminates():
    assert val("(while false 1)", gas=50) is None


def test_e206_calling_non_function():
    assert _rcode("(let x 42 (x 1))") == "E206"
    assert _rcode('(let x "f" (x 1))') == "E206"


def test_e207_wrong_arg_count():
    assert _rcode("(let f (fn (a b) a) (f 1))") == "E207"
    assert _rcode("(let f (fn (a) a) (f 1 2))") == "E207"


def test_e208_type_errors():
    assert _rcode('(neg "x")') == "E208"
    assert _rcode('(len 42)') == "E208"
    assert _rcode('(upper 42)') == "E208"
    assert _rcode('(push 42 1)') == "E208"


def test_runtime_error_phase_and_fields():
    with pytest.raises(TerseRuntimeError) as excinfo:
        run_source("(div 1 0)")
    err = excinfo.value
    assert err.code == "E201"
    assert err.phase == "runtime"
    assert err.op == "div"
    assert "E201" in str(err)


def test_run_accepts_ast_directly():
    value, output = run(parse('(print (add 40 2))'))
    assert value is None
    assert output == "42\n"


def test_interpreter_importable_lazily():
    # `terse.run` / `terse.run_source` resolve through the package __getattr__.
    assert callable(terse.run)
    assert callable(terse.run_source)
