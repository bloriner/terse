# Terse library learning (DreamCoder-style), round 1

Date: 2026-10-15. tiktoken 0.14.0, `cl100k_base`.
Tool: `tools/mine_corpus.py` (deterministic; `--holes` for 1-hole anti-unification).

## Method

Instead of hand-guessing fused ops (`fold`, `inc`, `keep`, `def` were all
human-designed), mine a corpus of Terse programs for repeated AST subtrees
and promote the winners to real ops. Two passes:

1. **Exact-match**: count identical subtrees, one vote per program
   (presence, not raw count — stops one long program dominating).
2. **1-hole anti-unification**: for each subtree, replace each child
   (including literal/var leaves — the most valuable hole positions) with
   `?`; keep patterns with ≥2 distinct fillers across ≥2 programs.

Bar for implementation: **≥5% corpus-wide token savings, or ≥2 tokens
saved per use-site with 3+ use-sites.** Every op added is grammar a model
must learn, so the bar is real — marginal candidates are rejected below.

## Corpus

| source | programs | note |
|---|---|---|
| `bench/tasks/*.terse` | 8 | reference solutions |
| `examples/*.terse` | 8 | documented examples |
| `tools/corpus/*.terse` | 40 | **new**: realistic workload programs written for this round (list processing, dict/JSON transforms, strings, math, aggregation) — all parse and run |
| `tests/test_*.py` string literals | 159 | parsed Terse snippets from tests |

**215 snippets total.** Honest caveat: the test-string slice dominates by
count and its top patterns are test fixtures (`('add', 1, 2)`,
`('div', 1, 0)`, `('le', ('var','n'), 1)` — the canonical parser/error-test
shapes). Mining the 56-program realistic slice alone gives the actionable
signal; both are reported by `tools/mine_corpus.py`.

## Top 1-hole patterns (realistic 56-program slice)

| # | pattern | programs | fillers (sample) | verdict |
|---|---|---|---|---|
| 1 | `('print', ?)` | 7 | various | trivial — print exists |
| 2 | `('range', 1, ?)` | 6 | 11, 21 | usage, not fusible |
| 3 | `('get', ('var','u'), ?)` | 5 | 'age', 'email', 'n' | field access — already minimal |
| 4 | `('len', ?)` | 5 | vars | trivial |
| 5 | `('call', ?, ('dec', ('var','n')))` | 4 | fact, fib | recursion scaffolding — not an op |
| 6 | `('eq', ('mod', X, N), 0)` | 7 uses / 3 progs | (mod i 15/3/5), (mod x 2) | **→ `divis`** |
| 7 | `('gt'/'lt', ('var','x'), ?)` | 4 | acc, 0, 100, 40 | already minimal |
| 8 | `('add', ('var','acc'), ?)` | 4 | s, t, 1 | subsumed by sum-fold below |
| 9 | `('inc', ('var','acc'), ?)` | 3 | v, w | `inc` already exists (prior art) |
| 10 | `('split', ?, ' ')` | 3 | s, (lower …) | → `words`: **rejected** (marginal, see below) |
| 11 | `('fold', [a,x], 0, C, ('add', a, x))` | 3 | various colls | **→ `sum`** |

Also probed and rejected: `(map (fn (u) (get u K)) C)` pluck (1 use-site),
`(if (lt x 0) (neg x) x)` abs (1 use-site).

## Implemented

### `divis` — `(divis x n)` ≡ `(eq (mod x n) 0)`

- 7 use-sites across 3 programs (fizzbuzz ×6, evens ×1).
- Measured: 8→6 tokens per use (**saves 2**).
- Semantics: exact fusion — `a % b == 0` with Python `%`; E201 on zero
  divisor, E208 on non-numbers (same as `mod`).
- Transpiles to `((x) % (n) == 0)`.
- **Closed the loop**: `bench/tasks/fizzbuzz.terse` rewritten with `divis`;
  bench now reads **39 tokens, 0.60× Python** (was 45 / 0.69), stdout MATCH.
  SPEC §10 table updated.

### `sum` — `(sum coll)` ≡ `(fold (acc x) 0 coll (add acc x))`

- 3 use-sites across 3 programs.
- Measured: 20–23→11–14 tokens per use (**saves 8–9**).
- Semantics: E208 on non-list or non-number elements; `[]` → `0`;
  int+float → float per the language's coercion rules.
- Transpiles to `sum(coll)`.
- Equivalence verified against the fold expansion (42 divis cases, 4 sum
  cases, plus transpile agreement).

Corpus-wide rewrite (5 files): 138→104 tokens on affected files;
**1.8% of the full 1856-token realistic corpus** — below the 5% bar, which
is why the per-use-site criterion governs here. A larger corpus (or a
hotter pattern) would be needed for a 5% corpus-wide win from one op.

## Rejected

- **`words` ≡ `(split s " ")`** — 3 use-sites, but saves only **1 token**
  in the common case (`(split s " ")` is already 4 tokens). Doesn't clear
  the ≥2/use bar. Reconsider if the corpus grows or text workloads dominate.
- **`pluck` ≡ `(map (fn (u) (get u K)) C)`** — the classic DreamCoder-style
  find, but only 1 use-site in this corpus. No evidence of heat.
- **`abs` ≡ `(if (lt x 0) (neg x) x)`** — 1 use-site. Same verdict.
- `('get', u, ?)` field access, `(len ?)`, `(print ?)` — already minimal;
  nothing to fuse.

## What this round proves

The loop works end to end: mine → measure → implement → re-solve →
benchmark improves (fizzbuzz 0.69→0.60). The two discovered ops are both
short dictionary words (`divis`, `sum`), satisfying the dialects.md rule
"short names are free". Next rounds: grow the workload corpus (agentic
coding tasks, not just snippets), try 2-hole patterns, and re-run after
every op addition — each new op changes what the miner can find.
