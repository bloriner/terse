"""Rapid density iteration: measure candidate Terse sources vs Python.

Usage: python3 density.py
Compares candidate .terse sources (must produce same stdout as the .py).
"""
import io
import subprocess
import sys
import tiktoken
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent
enc = tiktoken.get_encoding("cl100k_base")


def count(src: str) -> int:
    return len(enc.encode(src))


def run_terse(src: str) -> str:
    from terse import interp
    _, out = interp.run_source(src, gas=1_000_000)
    return out


def run_py(path: Path) -> str:
    proc = subprocess.run([sys.executable, str(path)], capture_output=True,
                          text=True, timeout=60, cwd=str(ROOT))
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def check(name: str, terse_src: str, py_path: str):
    py_src = Path(py_path).read_text()
    t_out = run_terse(terse_src)
    p_out = run_py(Path(py_path))
    match = "MATCH" if t_out == p_out else "MISMATCH!!"
    tt, pt = count(terse_src), count(py_src)
    print(f"{name}: terse={tt} py={pt} ratio={tt/pt:.2f} {match}")
    if match != "MATCH":
        print("  terse out:", repr(t_out[:200]))
        print("  py out:   ", repr(p_out[:200]))
    return tt / pt


FIZZ_PY = "bench/tasks/fizzbuzz.py"
FIB_PY = "bench/tasks/fib.py"
WC_PY = "bench/tasks/wordcount.py"

# --- v0.1 baselines (current bench files, comments stripped for fairness) ---
V01_FIZZ = '(for i (range 1 21)\n  (print (if (eq (mod i 15) 0) "fizzbuzz"\n           (if (eq (mod i 3) 0) "fizz"\n             (if (eq (mod i 5) 0) "buzz"\n               (str i))))))'

# --- v0.2 candidates ---
V02_FIZZ_A = '(for i(range 1 21)(print(if(eq(mod i 15)0)"fizzbuzz"(eq(mod i 3)0)"fizz"(eq(mod i 5)0)"buzz"i)))'

V02_FIB_A = '(let fib(fn(n)(if(lt n 2)n(add(fib(sub n 1))(fib(sub n 2)))))(print(fib 20)))'

V02_WC_A = '(let tally(fn(ws i acc)(if(ge i(len ws))acc(let w(get ws i)(tally ws(add i 1)(put acc w(add(get acc w)1))))))(print(tally(list "apple" "pear" "apple" "fig" "pear" "apple" "kiwi" "fig")0(dict "apple" 0 "fig" 0 "kiwi" 0 "pear" 0))))'

# --- v0.3 candidates: def, fold, inc ---
V03_FIB_A = '(def fib(n)(if(lt n 2)n(add(fib(dec n))(fib(dec(dec n))))))(print(fib 20))'

V03_WC_A = '(print(fold(acc w)(dict)(list "apple" "pear" "apple" "fig" "pear" "apple" "kiwi" "fig")(inc acc w)))'
# Python side for v0.3 wordcount: natural no-pre-seed style (insertion order
# apple, pear, fig, kiwi matches the Terse version)
V03_WC_PY = '''words = ["apple", "pear", "apple", "fig", "pear", "apple", "kiwi", "fig"]
counts = {}
for w in words:
    counts[w] = counts.get(w, 0) + 1
print(counts)
'''

if __name__ == "__main__":
    print("== v0.1 (comment-stripped) ==")
    check("fizzbuzz", V01_FIZZ, FIZZ_PY)
    print("== v0.2 candidates ==")
    check("fizzbuzz", V02_FIZZ_A, FIZZ_PY)
    check("fib", V02_FIB_A, FIB_PY)
    check("wordcount", V02_WC_A, WC_PY)
    print("== v0.3 candidates (def/fold/inc) ==")
    check("fib", V03_FIB_A, FIB_PY)
    # write temp py for wordcount v0.3 comparison
    Path("/tmp/wc03.py").write_text(V03_WC_PY)
    check("wordcount", V03_WC_A, "/tmp/wc03.py")
    print("== v0.4: transform / palindrome / factorial variants ==")
    users = '(list(dict "name" "ann" "age" 28)(dict "name" "bob" "age" 45)(dict "name" "cid" "age" 33))'
    # transform A: defs + map/filter
    tA = ('(def over30(u)(gt(get u "age")30))(def name-of(u)(get u "name"))'
          f'(let users{users}(print(map name-of(filter over30 users))))')
    # transform B: inline lambdas
    tB = (f'(let users{users}'
          '(print(map(fn(u)(get u "name"))(filter(fn(u)(gt(get u "age")30))users))))')
    # transform C: fold (original style, no comments)
    tC = (f'(let users{users}'
          '(print(fold(acc u)(list)users(if(gt(get u "age")30)(push acc(get u "name"))acc))))')
    check("transform-A", tA, "bench/tasks/transform.py")
    check("transform-B", tB, "bench/tasks/transform.py")
    check("transform-C", tC, "bench/tasks/transform.py")
    # palindrome variants
    check("pal-let", '(let s "racecar"(print(eq s(rev s))))', "bench/tasks/palindrome.py")
    check("pal-inline", '(print(eq"racecar"(rev"racecar")))', "bench/tasks/palindrome.py")
    # factorial variants
    check("fact-rec", '(def fact(n)(if(le n 1)1(mul n(fact(dec n)))))(print(fact 6))',
          "bench/tasks/factorial.py")
    check("fact-fold", '(print(fold(acc i)1(range 1 7)(mul acc i)))',
          "bench/tasks/factorial.py")
