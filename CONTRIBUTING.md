# Contributing to Terse

## Running the tests

```bash
cd /path/to/terse
python -m pytest
```

The suite must be fully green before any change lands. It covers the parser
(`tests/test_parser.py`), the interpreter (`tests/test_interp.py`), the
transpiler (`tests/test_transpile.py`), the error protocol
(`tests/test_errors.py`), every program in `examples/` run both ways
(`tests/test_examples.py`), and the benchmark harness
(`tests/test_bench.py`).

## Code style

- **Stdlib only.** Terse is pure Python 3.10+ with no third-party dependencies.
  Do not add a new dependency without discussing it first — the zero-dependency
  property is a feature (easy sandboxing, easy vendoring).
- Keep the shared AST contract stable: nodes are `("op", arg1, ...)` tuples,
  literals are raw Python values, variable references are `("var", name)`,
  and binding positions stay plain strings. If you must change it, update
  `SPEC.md`, the parser, the interpreter, the transpiler, the grammar, and
  the tests together.
- Errors are never bare strings: raise the right `TerseError` subclass with
  a stable code from `terse/errors.py` (`E100`–`E105` parse, `E200`–`E208`
  runtime, `E300` transpile), plus `op`/`pos`/`expected`/`got`/`hint` where
  they apply.

## Adding an op

An op touches five places — all five, or it doesn't land:

1. **`terse/parser.py`** — add the op and its arity to the `ARITY` catalog.
   Binding positions (`let`/`for`/`fn`-style) need parser support, not just
   a catalog entry.
2. **`terse/interp.py`** — implement the semantics in `_Interpreter`,
   following the documented coercion and truthiness rules.
3. **`terse/to_python.py`** — implement codegen so the transpiled program
   behaves identically (same value, same printed output).
4. **`SPEC.md`** — document the op: arity, arg types, semantics, example.
5. **`tests/`** — parser tests (AST shape, arity errors), interpreter tests
   (semantics + error cases), and transpiler round-trip tests.

Also update `terse/grammar.gbnf` if the op changes the surface syntax.

## PR guidance

- One change per PR; keep diffs small and reviewable.
- Add or update tests for every behavior change — no test, no merge.
- Update `SPEC.md` alongside any semantic change; the spec is the contract
  the tests enforce.
- Don't commit generated artifacts, caches, or local scratch files.
