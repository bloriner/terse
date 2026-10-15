# Terse v0.3 density notes — measured candidates

Method: same as v0.2 — implement, measure with tiktoken `cl100k_base`,
keep only what wins. Bench unchanged: 0.69–1.00x across 8 tasks, all MATCH.

## Rejected: `->` threading macro

Hypothesis: `(-> s (split ",") (sort) (join ", "))` would beat nested
`(join ", " (sort (split s ",")))` for pipelines.

Measured: threading 13 tokens vs nested 9. **Dead on arrival.**

Why: `(->` is 2 tokens — `(` + `->` do NOT fuse the way `(op` does
(the `-` breaks BPE fusion). Each pipeline step still needs its own
parens, so threading adds the `->` token while removing nothing.
In prefix syntax, nesting is already the minimal pipeline form.
Python's method chains (`s.strip().split()`) remain a wall with no
Terse equivalent — conceded, documented, not chased.

## Shipped: `fmt` string interpolation

`(fmt template arg...)` — `"{}"` placeholders filled left-to-right
with `str(arg)`; E207 if placeholders outnumber args.

Measured (tokens):
| case | Python | fmt | cat |
|---|---|---|---|
| sentence, 2 values | 12 (f-string) | 12 | 14 |
| `n={}` single value | 7 (f-string) | 8 | — |
| 4 values, `-` seps | 11 (f-string) | 15 | 10 |

Decision rule (documented in SPEC):
- **Sentences with embedded values** → `fmt` (ties f-strings, beats `cat`)
- **Joining with separators** → `cat` (still wins; `"-"` fuses cheap)
- `{}` is 1 token inside strings; `%s`-style would be 2 — `{}` chosen.

`fmt` doesn't beat Python anywhere, but it closes the string-building
gap where Terse was losing to f-strings, and it beats `cat` in the
common sentence case. Shipped for completeness with an honest rule.

## Unchanged walls (from v0.2)

- Comprehensions: Python's `[x for x in l if c]` still minimal; conceded.
- Slices: `[::-1]` still 4 tokens; conceded.
- Method chains: no Terse equivalent (`->` disproven above); conceded.
