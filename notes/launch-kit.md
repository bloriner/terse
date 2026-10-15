# Terse Launch Kit — drafts (2026-10-15)
Repo: https://github.com/bloriner/terse — public, Apache-2.0, v0.2, 158 tests passing.
Core claim (measured): Terse v0.2 uses 0.69–1.00x the GPT-4-class tokens of equivalent Python across 8 benchmark tasks (fizzbuzz 0.69x, fib 0.81x, wordcount 0.69x).

## 1. Hacker News (Show HN)
Title: `Show HN: Terse – an AI-native code language (0.69x Python tokens)`

Body:
```
Terse is a machine-designed code format for AI coding agents — not a
human-designed language with AI tooling bolted on, but a format designed
from the start around how tokenizers and LLMs actually consume code.

v0.2 results (measured with a GPT-4-class tokenizer, 8 tasks):
fizzbuzz 0.69x, fib 0.81x, wordcount 0.69x Python tokens. The full
interpreter, Python transpiler, and GBNF grammar are in the repo, with
158 tests passing.

The surprising findings so far:
- Shortening op names does nothing (BPE fuses them to 1 token regardless)
- Symbolic ops are WORSE than English words: (+ = 2 tokens vs (add = 1)
- All the wins came from structure: head-position calls, n-ary if,
  juxtaposed parens, fused ops (fold/inc/keep/def)

Open source (Apache-2.0). Next up: paired training corpora and an MCP
server so agents can actually execute it. Would love critique of the
density methodology — notes/density-v0.2.md has the full breakdown.
```

## 2. r/ProgrammingLanguages
Title: `[Terse] An AI-native code language: designing for tokenizers instead of humans (0.69x Python tokens)`

Body: longer technical version —
- Motivation: every mainstream language optimizes for human readers; AI
  agents now write most new code, and they pay per token.
- Design principles: head-position calls `(f x y)`, n-ary `if`, fused
  higher-order ops, juxtaposed parens — all chosen by measuring BPE
  token counts, not aesthetics.
- What's implemented: parser, tree-walking interpreter, Python
  transpiler, GBNF grammar for constrained decoding, 158 tests.
- Honest limits: v0.2, no type system yet, benchmark is 8 small tasks.
- Ask: feedback on the density methodology; collaborators welcome.

## 3. Lobsters
Title: `Terse: an AI-native code language`
Tags: programming, compilers
Summary: `Machine-designed code format for AI coding agents. v0.2 hits 0.69-1.00x Python token counts across 8 benchmarks; includes interpreter, Python transpiler, and GBNF grammar. Apache-2.0.`

## 4. Product Hunt (needs his account + assets)
- Tagline: "The code language designed for AI, not humans"
- Needs: logo/icon, 3-4 screenshots (REPL session, density chart), maker comment.
- Suggested launch day: Tuesday-Thursday.

## 5. awesome-lists to PR
- awesome-programming-languages (esolangs-adjacent section if any)
- awesome-llm / awesome-ai-agents tooling sections
Each PR: one-line description + link, following the list's contribution format.

## 6. X launch post (when @terse is live)
```
Introducing Terse — a code language designed for AI, not humans.

v0.2: 0.69x Python's token count on real tasks.
Interpreter + transpiler + GBNF grammar. Open source.

The twist: shortening op names does nothing. Symbolic ops are worse
than words. All wins came from structure.

github.com/bloriner/terse
```
