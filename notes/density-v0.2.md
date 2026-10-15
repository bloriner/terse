# Terse v0.2 density pass — findings

Measured with tiktoken `cl100k_base`. v0.1 ratios: 1.38–3.42×.
v0.2 ratios: 0.69–1.00× (6 of 8 tasks under 0.85×).

## What actually moved the needle

1. **Head-position calls** `(fib 20)` vs `(call fib 20)` — saved 2 tokens per call.
   Combined with `def` (named functions without the `let`+`fn` wrapper),
   fib went 123→29 tokens. The `(op` BPE fusion means the paren before the
   op name is free; the savings come from dropping the `call` token itself.

2. **N-ary `if`** — flat elif chains. Fizzbuzz's nested ifs collapsed;
   fizzbuzz went 90→45 tokens (0.69×). The else-if scaffolding
   `(if c1 e1 (if c2 e2 ...))` was pure overhead.

3. **Juxtaposed parens** — `(f(g x))` with no spaces. The tokenizer never
   needed the spaces; they were aesthetic. This alone saved ~10% across
   the bench. BPE insight: `'('` after `')'` or letters fuses, so the
   whitespace was literally wasted tokens.

4. **Fusion ops for hot patterns:**
   - `fold (acc x) init coll body` — loop-with-accumulator in one form.
     Wordcount's counting loop: `(fold(acc w)(dict)(split s " ")(inc acc w))`.
   - `inc d k [by]` — dict counter, missing key = 0, returns new dict.
     Eliminates the `(put d k (add 1 (get d k 0)))` dance.
   - `keep f coll` — Clojure-style filter+map fusion (drops nulls).
     Transform (filter+project) needs this; plain map+filter composition
     was 1.08×, keep got it to 0.98×.
   - `dec`, `rev`, `map`, `filter` — the obvious missing pieces.

5. **BPE fusion exploitation:** `(op` = 1 token, so op names are FREE.
   This means: don't shorten op names (it backfires — see below).
   Spend tokens on structure, not names. `(get u "age")` — the `get`
   costs nothing; the parens and spaces around it cost everything.

## What backfired

1. **Shortening op names** — `(f x)` vs `(foo x)`: NO DIFFERENCE.
   `(op` fuses to a single token regardless of op length. Shorter names
   would only help if the op appeared WITHOUT a preceding paren, which
   never happens in prefix syntax. Readability loss for zero gain.
   (This is the opposite of what intuition suggests.)

2. **Single-char ops like `+`** — `(add` = 1 token, `(+` = 2 tokens
   (`(` + `+` don't fuse). Symbolic ops are WORSE than word ops in
   head position. The v0.1 English-word ops were accidentally optimal.

   > CORRECTION (2026-10-15, tiktoken 0.14.0): re-measured `(+` as
   > ONE token (`['(+']`) on cl100k_base/o200k_base — the merge table
   > changed (or the v0.2 measurement was wrong). The "symbolic ops are
   > worse" pillar no longer holds. No density reason to prefer words
   > over symbols in head position now, but also no reason to switch:
   > words read better at equal cost. Pin tiktoken versions on every
   > density claim. See notes/dialects.md.

3. **`let` for single-use bindings** — costs 3 tokens (`(let`, name, `)`).
   Inlining single-use bindings is always cheaper. But `let` is still
   needed for double-use (palindrome's `s` used twice).

4. **Explicit `null` else-branches** — `(if c e null)` wastes a token.
   The 2-arg `if` returns null implicitly. Combined with `keep` (which
   drops nulls), filter patterns don't need explicit nulls.

5. **Python's comprehensions are a wall** — `[x for x in l if c]` fuses
   filter+project in 15 tokens; Terse's `(keep(fn(u)...)...)` needs 16+.
   Transform ties at best (0.98×). This is inherent to prefix syntax.

6. **Python's `[::-1]` slice is a wall** — 4 tokens for reverse.
   Terse's `(rev s)` needs a `let` binding for the string (or repeats it).
   Palindrome ties at 1.00×. Inherent.

## BPE surprises (valuable)

- `' x'` (space+letter) fuses to 1 token — spaces before identifiers are FREE.
- `' ('` (space+paren) fuses — spaces before parens are FREE.
- `' "'` (space+quote) fuses — spaces before strings are FREE.
- `' '`+digit does NOT fuse — `(range 1 10)` the spaces before numbers cost.
- `'))'` fuses to 1 — closing paren pairs are cheap.
- `', '` in Python costs 2 tokens; Terse's juxtaposed `)(` costs ~1.
- Strings cost ~equally in both languages (content dominates).
- Python's infix (`' %'`, `'=='`, `' >'`) and `:`/`=` are cheap (1 token each).
  Terse's advantage is structural (no `def`/`return`/`:`/`\n` scaffolding),
  not per-operator.

## The formula

Terse wins where Python has scaffolding: `def f(...):` + `return` + newlines
+ indentation (function defs), `result = []` + `for` + `.append` (loops),
`{}` + `:` + `,` + newlines (dict literals). Terse loses where Python has
fusion: comprehensions, slices, f-strings. The v0.2 op set was chosen to
maximize scaffolding-elimination (def/fold/inc) while conceding the fusion
walls (comprehensions, slices) as ties.

## Open questions

- Could a `comp` (comprehension) op beat Python's? Probably not — the
  `[x for x in l if c]` syntax is already minimal; a prefix version would
  need at least as many tokens for the three clauses.
- F-strings: Terse's `cat` is competitive for simple cases.
- Method chains (`s.strip().split()`) have no Terse equivalent; `->`
  threading macro could help but adds a new construct.
