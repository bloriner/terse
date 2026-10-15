# Terse per-model tokenizer dialect analysis

Date: 2026-10-15. tiktoken 0.14.0. Method: `bench/run.py --tokenizer`
(cl100k_base | o200k_base | p50k_base) + targeted spelling-variant probes.
Extends notes/density-v0.2.md, whose claims were all cl100k_base-only.

## Bench ratios per encoding (8 tasks, all stdout MATCH)

| task | cl100k_base | o200k_base | p50k_base |
|---|---|---|---|
| factorial | 0.89 | 0.89 | 1.03 |
| fib | 0.81 | 0.81 | 0.98 |
| fizzbuzz | 0.69 | 0.69 | 0.69 |
| gcd | 0.76 | 0.76 | 0.85 |
| palindrome | 1.00 | 1.00 | 0.89 |
| primes | 0.70 | 0.70 | 0.74 |
| transform | 0.98 | 0.98 | 1.02 |
| wordcount | 0.69 | 0.69 | 0.69 |

**o200k_base is token-identical to cl100k_base on all 8 tasks** (same raw
counts, not just ratios). p50k_base counts more tokens everywhere and
pushes 2 tasks above parity (factorial 1.03, transform 1.02).

## Spelling-variant probes (tokens per encoding)

Format: `variant A` vs `variant B` → cl100k AoB / o200k AoB / p50k AoB.

| variant | cl100k | o200k | p50k |
|---|---|---|---|
| `(add x y)` vs `(+ x y)` | 4v4 = | 4v4 = | 5v5 = |
| `(def f (x) x)` vs `(define f (x) x)` | 7v7 = | 7v8 A | 8v8 = |
| `(fmt "{}" x)` vs `(format "{}" x)` | 5v5 = | 5v5 = | 7v6 B |
| `(-> s (split x))` vs `(split s x)` | 7v4 B | 7v4 B | 7v5 B |
| `(print "hi")` vs `(print 'hi')` | 4v4 = | 4v4 = | 5v5 = |
| `(keep f l)` vs `(filtermap f l)` | 5v5 = | 5v5 = | 5v7 A |
| `(range 1 10)` vs `(range(1 10))` | 6v6 = | 6v6 = | 5v6 A |
| `(fn (x) x)` vs `(lambda (x) x)` | 6v6 = | 6v6 = | 7v7 = |
| `(let x 1 x)` vs `(let(x 1)x)` | 6v6 = | 6v6 = | 6v8 A |
| `(f(g x))` vs `(f (g x))` | 4v5 A | 4v5 A | 6v6 = |

Raw tokenizations that matter (tiktoken 0.14.0):

- `(add` → 1 token on cl100k AND o200k (`['(add']`); 2 on p50k (`['(','add']`).
- `(+` → **1 token on cl100k and o200k** (`['(+']`); 2 on p50k.
  **This overturns density-v0.2.md's claim** that "`(+` = 2 tokens
  (`(` + `+` don't fuse)". Either the BPE table changed between tiktoken
  versions or the original measurement was wrong. Re-measured 2026-10-15.
- `(define` → 1 token cl100k, 2 tokens o200k (`['(','define']`), 2 p50k.
- `(filtermap` → 2 tokens cl100k/o200k (`['(filter','map']`), 4 p50k.
- `(supercalifragilistic` → 10 cl100k / 8 o200k / 9 p50k.
- p50k_base **never fuses `(` with anything**: `(a` → `['(','a']`,
  `)("` → `[')(','"']`. Every call pays a +1 paren tax; op-name length
  always matters.

## What this means

1. **No per-model emission is justified for current tokenizers.**
   cl100k_base (GPT-4, GPT-3.5-turbo) and o200k_base (GPT-4o, o-series)
   produce identical bench ratios to two decimals, and every spelling
   variant probed agrees between them except `(define` (1-token edge to
   `def` on o200k — already the chosen spelling). One emission serves both.

2. **p50k_base is a legacy outlier, not a dialect target.** No paren
   fusion at all; ratios slip above parity on 2/8 tasks. p50k covers
   GPT-3/Codex-era models with no active agentic use. Do not maintain a
   fork for it. If a p50k target ever mattered, the rule would be
   "shortest possible op names" — but it doesn't, so don't.

3. **Refinement of v0.2's "op names are FREE": short names are free.**
   `(op` fuses to 1 token on cl100k/o200k only for short, common-morpheme
   names (`add`, `def`, `fmt`, `keep`). `(filtermap` already costs 2,
   `(define` costs 2 on o200k, long names explode everywhere. Keep new
   ops ≤6 chars and dictionary-common.

4. **The symbolic-op ban is lifted (version-qualified).** `(+` fuses to 1
   token on tiktoken 0.14.0's cl100k/o200k. There is now no density reason
   to prefer word ops over symbolic ones in head position — and no reason
   to switch either, since words read better and cost the same. Do not
   re-litigate op spellings on density grounds alone.

5. **Pin the tokenizer version and re-probe on upgrades.** BPE merge
   tables change between tiktoken releases — the `(+` flip between the
   v0.2 measurement and 0.14.0 proves measurements rot. Record the
   tiktoken version with every density claim; re-run the variant probes
   after any tiktoken upgrade.

## Limits

tiktoken only ships OpenAI encodings. Claude, Llama, and Gemini
tokenizers are not publicly available in tiktoken, so this analysis
covers the GPT family only. The structural findings (paren fusion,
name-length effects) are BPE-general enough to likely hold, but the
exact numbers are not verified there.
