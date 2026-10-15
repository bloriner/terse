# Terse v0.2 — Language Specification

Terse is an AI-native code language: a machine-designed prefix HIR (high-level
intermediate representation) for AI coding agents. Agents write Terse;
Terse runs on a tree-walking interpreter and transpiles to readable Python.

v0.2 is the density pass: the surface syntax was re-measured against
`cl100k_base` and reworked until reference solutions beat idiomatic Python
on tokens (see §10). Semantics are unchanged from v0.1; only the surface
syntax got denser.

## 1. Design goals

**Why prefix.** Infix syntax exists for human eyes — precedence tables,
associativity, and implicit grouping are all parsing hazards for a token
stream. Prefix puts the op first, so the structure of every form is known
after one token, arities are checkable left-to-right, and there is exactly
one way to parenthesize any expression. A model never has to decide whether
`a + b * c` needs parens, because the question cannot be asked.

**Why canonical.** One spelling per construct, enforced by the grammar and
the parser. `if` is always prefix; there is no ternary, no statement/if
distinction at the syntax level. `true`/`false`/`null` are lowercase, always.
Bare symbols in expression position are always variable references; binding
positions always take plain names. Canonicality shrinks the output
distribution the model must learn and makes diffs, caches, and dedup exact.

**Why dense.** Every token an agent emits costs time and money. v0.2 was
shaped by measurement, not aesthetics: `(op` fuses to one BPE token, so op
names are free; whitespace and paren pairs are the real costs. The syntax
below minimizes both: juxtaposed parens (`(f(g x))`), head-position calls
(`(fib 20)` not `(call fib 20)`), n-ary `if`, and fusion ops (`fold`, `inc`,
`keep`) for the hottest patterns. Readability is not a goal — Terse is
written by models, and `to_python` exists for human eyes.

**TTCP metric.** Terse is evaluated with TTCP — *Time-To-Correct-Program*:
wall-clock time from task prompt to a program that passes the task's tests,
including all repair iterations. Syntax errors cost iterations, so the
primary optimization target is *first-try validity*: the GBNF grammar
(`terse/grammar.gbnf`) makes syntax errors impossible under constrained
decoding, and the typed error protocol (`E100`–`E300`) makes the remaining
semantic errors one-shot fixable by feeding the error dict back to the model.

## 2. Concrete syntax (EBNF)

```ebnf
program    ::= { ws | comment } expr { (ws | comment)+ expr } { ws | comment } ;
expr       ::= list | atom ;
list       ::= "(" { ws | comment } (symbol | list) { (ws | comment)+ expr }
               { ws | comment } ")" ;
atom       ::= number | string | "true" | "false" | "null" | symbol ;
number     ::= [ "-" ] digit { digit } [ "." digit { digit } ] ;
string     ::= '"' { string-char | escape } '"' ;
escape     ::= "\\" ( '"' | "\\" | "n" | "t" ) ;
symbol     ::= letter { letter | digit | "_" | "?" | "!" | "+" | "-" } ;
comment    ::= ";" { ? any char except newline ? } ;
ws         ::= ? whitespace (space, tab, newline, CR) ? ;
digit      ::= "0" … "9" ;
letter     ::= "A" … "Z" | "a" … "z" ;
string-char::= ? any char except '"' , '\' , newline, CR ? ;
```

Notes:

- A program is one or more top-level forms. Multiple forms are wrapped in
  `("seq", ...)` by the parser; a single form is the program as-is.
- `;` starts a comment running to end of line. Whitespace is insignificant,
  and **no whitespace is required around parens**: `(f(g x))`, `(eq(mod i 15)0)`
  are valid. Whitespace is still required between adjacent atoms
  (`(add 1 2)`, not `(add 12)`).
- The list head is an op name, a variable (head-position call), or a
  computed function `((fn (x) x) 5)`.
- Negative numbers lex as part of the literal only when `-` is immediately
  followed by a digit (`-7` is a number; `- 7` is the symbol `-` then `7`).
- Only the four escapes `\"`, `\\`, `\n`, `\t` are supported. A raw newline
  inside a string is an error (E104).

## 3. AST contract

All workers (parser, interpreter, transpiler, benchmarks) share one AST:

- Node = Python tuple `("opname", arg1, arg2, ...)`.
- Literals are raw Python values: `int`, `float`, `str`, `bool`, `None`.
- Variable reference = `("var", "name")`.
- Binding positions stay plain strings: `("let", "x", value, body)`,
  `("for", "i", iterable, body)`, `("fn", ["a", "b"], body)`,
  `("def", "f", ["a"], body)`, `("fold", ["acc", "x"], init, coll, body)`.

Examples:

| source                              | AST                                                        |
|-------------------------------------|------------------------------------------------------------|
| `(add 1 2)`                         | `("add", 1, 2)`                                            |
| `(let x 5 (add x 1))`               | `("let", "x", 5, ("add", ("var", "x"), 1))`                |
| `(fn (a b) (add a b))`              | `("fn", ["a", "b"], ("add", ("var", "a"), ("var", "b")))`  |
| `(fib 20)`                          | `("call", ("var", "fib"), 20)`                             |
| `(def fib (n) (fib n))`             | `("def", "fib", ["n"], ("call", ("var","fib"), ("var","n")))` |
| `(if a b c d e)`                    | `("if", ("var","a"), ("var","b"), ("var","c"), ("var","d"), ("var","e"))` |
| `(fold (acc x) 0 l (add acc x))`    | `("fold", ["acc","x"], 0, ("var","l"), ("add", ("var","acc"), ("var","x")))` |
| `(for i (range 10) (print i))`      | `("for", "i", ("range", 10), ("print", ("var","i")))`      |
| `(dict "a" 1 "b" 2)`                | `("dict", "a", 1, "b", 2)`                                 |
| `42`                                | `42`                                                       |
| `x`                                 | `("var", "x")`                                             |

## 4. Op catalog

Arity notation: `n` = exactly n; `n+` = n or more; `n-m` = n to m; `even` =
even count.

| op | arity | arg types | semantics | example |
|----|-------|-----------|-----------|---------|
| `add` | 2 | number,number | addition | `(add 1 2)` → `3` |
| `sub` | 2 | number,number | subtraction | `(sub 5 3)` → `2` |
| `mul` | 2 | number,number | multiplication | `(mul 2 3)` → `6` |
| `div` | 2 | number,number | **true division** (never floor) | `(div 7 2)` → `3.5` |
| `mod` | 2 | number,number | remainder | `(mod 7 3)` → `1` |
| `pow` | 2 | number,number | exponentiation | `(pow 2 10)` → `1024` |
| `neg` | 1 | number | unary negation | `(neg 5)` → `-5` |
| `dec` | 1 | number | decrement: x − 1 | `(dec 5)` → `4` |
| `eq` `ne` | 2 | any,any | equality / inequality | `(eq 1 1)` → `true` |
| `lt` `le` `gt` `ge` | 2 | comparable | ordering | `(lt 1 2)` → `true` |
| `and` | 2+ | any | n-ary, **short-circuits** | `(and a b c)` |
| `or` | 2+ | any | n-ary, **short-circuits** | `(or a b)` |
| `not` | 1 | any | logical negation of truthiness | `(not false)` → `true` |
| `if` | 2+ | cond/then pairs + optional else | n-ary branch; all-false + no else → `null` | `(if c1 e1 c2 e2 els)` |
| `while` | 2 | (cond, body) | loop while cond is truthy; value `null` | `(while (lt x 10) (print x))` |
| `for` | 3 | (name:str, iterable, body) | iterate; binds name each pass; value `null` | `(for i (range 3) (print i))` |
| `seq` | 0+ | any | evaluate in order; value = last (or `null` if empty) | `(seq a b)` |
| `let` | 3 | (name:str, value, body) | bind name in body scope (letrec for closures); value = body | `(let x 5 (add x 1))` → `6` |
| `fn` | 2 | (params:list[str], body) | first-class function value (closure) | `(fn (a b) (add a b))` |
| `def` | 3 | (name:str, params:list[str], body) | **top-level** named (recursive) function; value `null` | `(def fib (n) ...)` |
| `fold` | 4 | ([acc x]:list[str], init, coll, body) | loop with accumulator; body value → next acc; returns final acc | `(fold (acc x) 0 l (add acc x))` |
| `return` | 1 | any | return from enclosing `fn` | `(return 42)` |
| `assert` | 1-2 | (cond, msg?) | E204 if cond is falsy | `(assert (gt x 0) "x>0")` |
| `list` | 0+ | any | build a list | `(list 1 2 3)` |
| `dict` | even | alternating key/value | build a dict | `(dict "a" 1)` |
| `get` | 2 | (coll, key/index) | dict key or list index; E202 on miss | `(get d "a")` |
| `put` | 3 | (coll, key/index, value) | **returns NEW collection** (persistent update) | `(put d "b" 2)` |
| `inc` | 2-3 | (dict, key, by?) | **returns NEW dict** with key incremented by `by` (default 1); missing key counts as 0 | `(inc d "a")` |
| `len` | 1 | list/dict/str | length | `(len (list 1 2))` → `2` |
| `range` | 1-3 | ints | like Python range → list | `(range 10)`, `(range 1 10 2)` |
| `push` | 2 | (list, value) | **returns NEW list** with value appended | `(push (list 1) 2)` |
| `map` | 2 | (fnexpr, coll) | list of f(x) | `(map f (list 1 2))` |
| `filter` | 2 | (pred, coll) | elements where pred is truthy | `(filter pred l)` |
| `keep` | 2 | (fnexpr, coll) | filter+map fusion: non-null f(x) values | `(keep f l)` |
| `rev` | 1 | list/str | reversed list or string | `(rev "abc")` → `"cba"` |
| `cat` | 0+ | any | string concat of `str(arg)` each | `(cat "a" 1)` → `"a1"` |
| `str` | 1 | any | convert to string | `(str 42)` → `"42"` |
| `int` | 1 | str/float/bool | convert to int; E203 on failure | `(int "42")` → `42` |
| `float` | 1 | str/int/bool | convert to float; E203 on failure | `(float "3.5")` |
| `upper` | 1 | str | uppercase | `(upper "ab")` → `"AB"` |
| `lower` | 1 | str | lowercase | `(lower "AB")` → `"ab"` |
| `split` | 2 | (str, sep) | split on separator → list | `(split "a,b" ",")` |
| `join` | 2 | (sep, list) | join list with separator | `(join ", " (list "a" "b"))` |
| `print` | 0+ | any | print args; **returns `null`** | `(print "hi" x)` |

**Function calls.** There is no `call` op in v0.2. A list whose head is not
a known op is a call: `(fib 20)` ≡ `("call", ("var","fib"), 20)`. A list head
may also be a computed function: `((fn (x) x) 5)`. A typo'd op name
(e.g. `(ad 1 2)`) parses as a call and fails at runtime with E200, whose
hint suggests the intended op (`did you mean 'add'?`) — the repair loop
keeps its signal.

`var` is an AST-level node only (`("var", name)`); it is not a surface op —
bare symbols in expression position become `("var", name)` automatically.

## 5. Canonical spelling rules

- Exactly one spelling per construct. `if` is always prefix; there is no
  infix operator, no ternary, no statement/if distinction at the syntax level.
- Keywords are lowercase, always: `true`, `false`, `null`. (Uppercase
  variants are plain symbols → unbound variables, not literals.)
- Bare symbols in expression position are always variable references.
  Binding positions (`let`/`for` name, `fn`/`def` params, `fold` bindings)
  are always plain names, never `("var", ...)` nodes and never expressions.
- `div` is always true division; there is no integer-division op.
- Collections are immutable-by-convention: `put`, `push`, and `inc` return
  new collections; there is no in-place mutation op.
- An op name in head position always wins over a variable: user code cannot
  shadow ops.

## 6. Type rules

Terse is dynamically typed. Documented coercion rules (the interpreter
and transpiler must agree on these):

- `int` + `float` → `float` for all arithmetic ops (`(add 1 2.5)` → `3.5`).
- `str` + `str` via `add` concatenates (`(add "a" "b")` → `"ab"`).
  Mixed `str` + number via `add` is a type error (E208); use `cat`.
- Truthiness is Python-like: `false`, `null`, `0`, `0.0`, `""`, empty
  list/dict are falsy; everything else is truthy. `if`/`while`/`and`/`or`/
  `not`/`assert`/`filter` all use truthiness, not strict booleans.
- `eq`/`ne` compare by value across int/float (`(eq 1 1.0)` → `true`);
  other cross-type comparisons are E208.
- `int("3.9")` → E203 (no silent truncation of strings); `int(3.9)` →
  `3` (float truncation is allowed, Python-like).
- `keep` drops only `null` results (like Clojure); `filter` drops all
  falsy elements.

## 7. Gas / sandbox model

Every evaluation runs under a gas budget (default 1,000,000): each op
evaluation (and each loop iteration / call) consumes gas; when the budget is
exhausted the interpreter aborts with E205 `gas-exhausted`. Programs cannot
raise their own budget. There is no I/O beyond `print`, no imports, no file
or network access — the only observable effects are printed output and the
return value. `while` loops always terminate: either the condition goes
falsy or gas runs out.

## 8. Error protocol (summary)

All errors are `TerseError` subclasses with stable codes and machine-readable
fields (`code`, `phase`, `op`, `pos`, `message`, `expected`, `got`, `hint`);
`to_dict()` serializes them for the repair loop, and `str()` renders
`[E103 parse] op 'add' got 1 argument(s) (hint: ...)`.

| code | name | phase | when |
|------|------|-------|------|
| E100 | unexpected-eof | parse | unclosed `(`/string, empty program |
| E101 | unexpected-token | parse | stray `)`, non-symbol/list head, `()` |
| E103 | arity-mismatch | parse | wrong arg count (incl. odd `dict` args) |
| E104 | bad-literal | parse | unterminated string, bad escape |
| E105 | bad-binding | parse | non-symbol in binding position |
| E200 | unbound-variable | runtime | `("var", name)` with no binding (incl. typo'd op names, with did-you-mean hint) |
| E201 | division-by-zero | runtime | `div`/`mod` by zero |
| E202 | key-or-index-error | runtime | missing key / bad index in `get`/`put` |
| E203 | conversion-error | runtime | failed `int`/`float` conversion |
| E204 | assertion-failed | runtime | `assert` on falsy condition |
| E205 | gas-exhausted | runtime | evaluation budget used up |
| E206 | not-callable | runtime | calling a non-function |
| E207 | call-arity-mismatch | runtime | wrong arg count at a call |
| E208 | type-error | runtime | op applied to unsupported type(s) |
| E300 | transpile-unsupported | transpile | construct with no Python mapping (e.g. `def` in value position, `return` outside fn-body statement position) |

(E102 `unknown-op` was retired in v0.2: unknown heads parse as calls; the
typo hint moved to E200's runtime hint.)

The parser never crashes on malformed input: `parse` raises `ParseError`;
`try_parse` returns `(None, [error_dict])` instead.

## 9. Differences from Python (notes for transpiler/interp workers)

- No operator precedence to preserve — the transpiler emits explicit
  parentheses from the tree; it never re-derives precedence.
- `div` is `/`, never `//`. There is no floor-division op.
- `and`/`or` are n-ary and short-circuit; `not` takes truthiness.
- `if` without else evaluates to `null` (Python `None`), not an error.
- `put`/`push`/`inc` are persistent (return new collections); Python output
  should avoid mutating the input.
- `print` returns `None`; `seq` returns its last value; `for`/`while`/`def`
  evaluate to `null`.
- `return` only valid in fn-body statement position (E300 otherwise);
  `def` only valid in statement position (E300 otherwise).
- String escapes are exactly `\" \\ \n \t`; Python output must re-escape
  accordingly (no `\r`, `\0`, `\x..`, `\u....` in v0.2 source).
- `true`/`false`/`null` map to `True`/`False`/`None`.

## 10. Density results (v0.2)

Measured with `bench/run.py` (tiktoken `cl100k_base`), reference solutions
in Terse vs idiomatic Python, stdout verified identical:

| task | terse | python | ratio |
|---|---|---|---|
| fizzbuzz | 45 | 65 | **0.69** |
| fib | 29 | 36 | **0.81** |
| wordcount | 38 | 55 | **0.69** |
| factorial | 25 | 28 | 0.89 |
| gcd | 26 | 34 | **0.76** |
| palindrome | 13 | 13 | 1.00 |
| primes | 43 | 61 | **0.70** |
| transform | 61 | 62 | 0.98 |

v0.1 ratios were 1.38–3.42×. Six of eight tasks are now decisively under
0.85×. Known walls: Python's `[::-1]` slice is a unique density advantage
(palindrome ties at 1.00); list comprehensions fuse filter+project in a way
prefix syntax can't fully match (transform 0.98); the recursive factorial's
`if`/`def` scaffolding costs 0.89×. See `bench/` and the density notes.
