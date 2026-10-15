# Terse

<p align="center"><img src="assets/logo.png" width="160" alt="Terse logo"></p>

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-193%20passing-brightgreen.svg)](tests/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](terse/)
[![Token ratio](https://img.shields.io/badge/tokens-0.60%E2%80%931.00x%20vs%20Python-orange.svg)](bench/)

**An AI-native code language: a machine-designed prefix HIR for AI coding agents.**

AI agents write human-designed languages — verbose, infix, and syntax-error-prone. Terse is a canonical prefix intermediate representation designed for machines: fixed-arity ops, one spelling per construct, structured errors built for repair loops, and a surface syntax measured to be token-cheaper than Python.

## Why

Every mainstream language was designed for human eyes: infix operators with precedence tables, multiple spellings for the same construct (`if` vs ternary vs statement), significant whitespace, and error messages written for people. When an AI coding agent emits code, each of those is a failure mode — a misplaced paren or a precedence guess costs a repair iteration.

Terse removes the decisions a model shouldn't have to make:

- **Prefix, always.** `(add a (mul b c))` — the op comes first, so the structure of every form is known after one token. No precedence tables, no associativity, exactly one way to parenthesize anything.
- **Canonical.** One spelling per construct, enforced by the parser. `if` is always prefix; `true`/`false`/`null` are always lowercase; bare symbols are always variable references. Canonicality shrinks the output distribution a model must learn and makes diffs, caches, and dedup exact.
- **Dense.** The v0.2 syntax was shaped by `cl100k_base` measurements: juxtaposed parens, head-position calls, n-ary `if`, and fusion ops for hot patterns. Reference solutions measure **0.60–1.00× Python tokens** across 8 bench tasks (see `bench/`).
- **Fixed arities, checked at parse time.** `(add 1)` fails with `E103 arity-mismatch` before anything runs.
- **Structured errors for repair loops.** Every failure is a `TerseError` with a stable code (`E100`–`E300`), phase, op, source position, `expected`/`got`, and a concrete `hint`. Feed `err.to_dict()` back to the model and the fix is usually one shot.
- **Constrained-decoding grammar.** `terse/grammar.gbnf` is a GBNF grammar for the full language — under constrained decoding, syntax errors become impossible, and the only remaining failures are semantic ones the error protocol already covers.

## Quickstart

```python
import terse

# Parse: surface syntax -> AST tuples
ast = terse.parse("(let x 40 (add x 2))")   # ("let", "x", 40, ("add", ("var", "x"), 2))

# Run: (value, printed_output)
value, output = terse.run(ast)               # (42, "")

# Transpile: AST -> readable Python, verified by exec
src = terse.to_python(ast)
ns = {}
exec(compile(src, "<terse>", "exec"), ns)
ns["_terse_result"]                          # 42
```

CLI:

```bash
python -m terse run examples/fib.terse          # run; prints program output, "=> <value>" to stderr
python -m terse run prog.terse --gas 1000000   # with a custom gas budget
python -m terse transpile prog.terse           # print the generated Python
python -m terse check prog.terse               # parse only; prints OK or error JSON (exit 0/1)
```

Errors are machine-readable JSON on stderr:

```bash
$ python -m terse check broken.terse
{"code": "E103", "phase": "parse", "op": "add", "pos": {"line": 1, "col": 1, "index": 1}, ...}
```

A taste of the language:

```lisp
; recursive fibonacci via def
(def fib(n)(if(lt n 2)n(add(fib(dec n))(fib(dec(dec n))))))
(print(fib 10))                               ; => 55

; fizzbuzz: n-ary if, divisibility op, juxtaposed parens
(for i(range 1 21)(print(if(divis i 15)"fizzbuzz"(divis i 3)"fizz"(divis i 5)"buzz"i)))

; wordcount: fold + inc, no pre-seeding needed
(print(fold(acc w)(dict)(list "a" "b" "a")(inc acc w)))  ; => {'a': 2, 'b': 1}
```

## Repo layout

```
terse/               the language package (pure Python 3.10+, stdlib only)
  parser.py          .terse surface syntax -> AST (tokenizer + recursive descent)
  errors.py          TerseError hierarchy + the E100–E300 code registry
  interp.py          tree-walking interpreter: run/run_source, gas sandbox
  to_python.py       AST -> readable Python transpiler (emits _terse_result)
  grammar.gbnf       GBNF grammar for constrained decoding
  __main__.py        CLI: run / transpile / check
SPEC.md              the language specification (syntax, ops, semantics, errors)
examples/            8 sample programs + expected.json (exact expected stdout)
bench/               TTCP benchmark: `python -m bench.run` (8 tasks, token ratios)
tests/               pytest suite (160 tests)
mcp/                 MCP server: eval/transpile/tokens/parse as agent tools (stdio)
api/                 metered execution API: POST /meter -> HMAC-signed receipts (see notes/metered-api.md)
billing/             Stripe usage reporting: verify receipts, aggregate tokens-saved
                     per customer per month, report to billing meter events
                     (see billing/README.md for the honest status)
LICENSE              Apache-2.0
```

Key semantics: `div` is true division; `and`/`or` are n-ary and short-circuit; `let` is letrec for closures and `def` defines named (recursive) functions; `put`/`push`/`inc` are functional; `print` returns `null`; evaluation is gas-metered (`E205` on exhaustion). See `SPEC.md` for the full contract.

## Status

**v0.3** — 160 tests green. New: `fmt` string interpolation (`(fmt "hi {}" name)` — ties Python f-strings, beats `cat` on sentences; see `notes/density-v0.3.md` for the measured decision rule). Rejected after measurement: `->` threading macro (13 tokens vs 9 nested — BPE doesn't fuse `(->`). v0.2 density ratios hold: 0.60–1.00× Python tokens across the 8-task benchmark, all stdout MATCH. MCP server (`mcp/`) exposes eval/transpile/token-count/parse as agent tools over stdio.

## Roadmap

- **TTCP leaderboard** — live in [`leaderboard/`](leaderboard/) (methodology, scoring tool, static page): models compete on token-efficiency vs Python across the 8 fixed bench tasks, stdout-verified. First-try validity with live repair loops is the defined-but-unmeasured second metric.
- **Paired training corpora**: Terse↔Python parallel programs for fine-tuning.
- **Fine-tuning open models** on Terse for direct generation.
- **More backends**: additional transpile targets beyond Python.

## License

Apache-2.0 — see [LICENSE](LICENSE).
