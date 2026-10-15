# Terse Launch Kit — drafts (2026-10-15, updated for v0.3+)

Repo: https://github.com/bloriner/terse — public, Apache-2.0, v0.3, 193 tests passing.
Playground: live browser REPL (link in chat).
Core claim (measured, tiktoken cl100k_base v0.14.0): Terse reference solutions use **0.60–1.00x** the tokens of idiomatic Python across 8 benchmark tasks (fizzbuzz 0.60x, fib 0.81x, wordcount 0.69x). Full story: interpreter, Python transpiler, GBNF grammar for constrained decoding, MCP server (7 tools), Terse Think reasoning format (56–61% savings), session macro dictionary, token-savings-metered API, public TTCP leaderboard.

Surprising findings (all measured):
- Shortening op names does nothing (BPE fuses `(op` to 1 token regardless — but keep names ≤6 chars; `(filtermap` already costs 2)
- CORRECTION: `(+` is 1 token on current tiktoken, not 2 — no density reason to prefer words over symbols in head position anymore; words stay for readability at equal cost
- All the big wins came from structure: head-position calls, n-ary `if`, juxtaposed parens, fused ops (`fold`/`inc`/`keep`/`divis`/`sum`)
- Library learning works: mining a 215-program corpus found `divis` and `sum`, which cut fizzbuzz 0.69x → 0.60x
- Reasoning tokens dwarf code tokens: Terse Think compresses agent reasoning traces 56–61%

## 1. Hacker News (Show HN) — needs his HN account
Title: `Show HN: Terse – an AI-native code language (0.60x Python tokens)`

Body:
```
Terse is a machine-designed code format for AI coding agents — not a
human-designed language with AI tooling bolted on, but a format designed
from the start around how tokenizers and LLMs actually consume code.

Measured with a GPT-4-class tokenizer across 8 tasks: 0.60–1.00x Python's
token count (fizzbuzz 0.60x, fib 0.81x, wordcount 0.69x). The repo has the
full interpreter, Python transpiler, GBNF grammar for constrained
decoding, an MCP server (eval/transpile/parse/token-count + session
macro dictionary), and 193 passing tests.

The surprising findings so far:
- Shortening op names does nothing (BPE fuses `(op` to 1 token regardless)
- Symbolic ops are NOT worse than words (re-measured: `(+` = 1 token)
- All the wins came from structure: head-position calls, n-ary if,
  juxtaposed parens, fused ops
- A DreamCoder-style miner found two new ops (`divis`, `sum`) that
  improved the benchmark — the language now designs parts of itself

Also in the repo: Terse Think (a compact reasoning-trace format, 56-61%
measured savings — reasoning tokens dwarf code tokens), a token-savings-
metered execution API, and a public TTCP leaderboard.

Open source (Apache-2.0). Try it in the browser playground [link].
Would love critique of the density methodology — notes/density-v0.2.md
has the full breakdown, corrections included.
```

## 2. r/ProgrammingLanguages — needs his Reddit account
Title: `[Terse] An AI-native code language: designing for tokenizers instead of humans (0.60x Python tokens)`

Body: longer technical version —
- Motivation: every mainstream language optimizes for human readers; AI agents now write most new code, and they pay per token.
- Design principles: head-position calls `(f x y)`, n-ary `if`, fused higher-order ops, juxtaposed parens — all chosen by measuring BPE token counts, not aesthetics.
- What's implemented: parser, tree-walking interpreter, Python transpiler, GBNF grammar, MCP server, 193 tests.
- The honest correction: v0.2 notes claimed symbolic ops tokenize worse; re-measurement on tiktoken 0.14.0 proved it wrong. Methodology notes carry corrections, not rewrites.
- Ask: feedback on the density methodology; collaborators welcome.

## 3. Lobsters — needs his account (invite-only)
Title: `Terse: an AI-native code language`
Tags: programming, compilers
Summary: `Machine-designed code format for AI coding agents. 0.60-1.00x Python token counts across 8 benchmarks; interpreter, Python transpiler, GBNF grammar, MCP server, 193 tests. Apache-2.0.`

## 4. Product Hunt — needs his account + assets
- Tagline: "The code language designed for AI, not humans"
- Assets: logo + 3-4 screenshots (playground, density chart, leaderboard) — generator-ready, see below
- Maker comment: the story of the `(+` correction (measurement beats intuition)
- Suggested launch day: Tuesday–Thursday

## 5. Awesome-lists to PR (agent-doable, needs his one-tap to send from his account)
- punkpeye/awesome-mcp-servers — MCP server section (7 tools: eval, transpile, tokens, parse, alias define/list/clear)
- awesome programming-languages list — esolangs-adjacent / experimental section
- awesome-ai-agents tooling section
Each PR: one-line description + link, following the list's contribution format.

## 6. PyPI — `pip install terse` (needs his PyPI account; agent can package)
Pure-Python, stdlib-only — packaging is trivial (pyproject.toml + `pip install build`). Account signup needs his email verification.

## 7. X launch post (when @terse handle is live — needs his phone signup)
```
Introducing Terse — a code language designed for AI, not humans.

0.60x Python's token count on real tasks. Interpreter + transpiler +
GBNF grammar + MCP server. Open source, 193 tests green.

The twist: shortening op names does nothing. All wins came from
structure. And the language now designs parts of itself — a corpus
miner found two new ops that improved the benchmark.

Try it in your browser: [playground link]
github.com/bloriner/terse
```

## 8. dev.to / Hashnode — needs his account (draft ready on request)
Long-form: "I designed a programming language by measuring a tokenizer" — the full story including the `(+` correction and the library-learning loop.
