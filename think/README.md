# Terse Think

A compact reasoning-trace format for AI coding agents. Terse compresses code
to 0.69–1.00× Python tokens; Terse Think applies the same measurement-driven
philosophy to chain-of-thought, where agents actually spend 10–50× more
tokens than on final code.

## The idea in 30 seconds

Agents pad their reasoning with discourse scaffolding — "let me think",
"first, I should", "on the other hand", "it's worth noting" — 5–10 tokens
per instance, dozens of times per trace. Terse Think replaces scaffolding
with 10 one-or-two-token markers (`G:` goal, `P:` plan, `A:` approach,
`T:` tradeoff, `V:` verify, `H:` hypothesis, `O:` observe, `F:` fix,
`?:` question, `!:` decide) and compressed noun phrases instead of
sentences. No parser, no tooling: an LLM writes it from a prompt template.

## Measured savings

Two worked pairs in `examples/` — same reasoning, verbose English vs Terse
Think, counted with tiktoken `cl100k_base`:

| example | English | Think | saved |
|---------|---------|-------|-------|
| fib-plan (design, tradeoffs, edge cases) | 333 tok | 148 tok | **56%** |
| debug-test (hypothesis + check loop) | 281 tok | 110 tok | **61%** |

The English baselines were written to match realistic agent CoT (hedging
and self-narration included, not strawmen). Every substantive point is
preserved; only scaffolding and sentence-grammar were removed.

## Files

- `SPEC-think.md` — the format specification: markers, style rules,
  canonical patterns, measurements
- `think-prompt.md` — system-prompt template (463 tokens) that teaches an
  agent the format, with few-shot micro-examples
- `examples/` — the two measured pairs (`.en.txt` verbose, `.think.txt`
  compressed)

## Honest limitations

- **Untested with real agents.** The biggest open question: scaffolding
  may be *compute* — deleting "let me think" might delete thinking, not
  just tokens. Needs A/B testing on live reasoning before any quality
  claim.
- **Content doesn't compress.** Unfamiliar domains stay long; Think
  removes structure, not substance.
- **Noun phrases can under-specify.** Suggested rule: dense by default,
  expand to sentences at genuine confusion points.
- **No validation.** Nothing checks a Think trace; malformed traces fail
  silently into vagueness.
- **Tokenizer-dependent.** Measured on `cl100k_base`; ratios will vary by
  model, though 1–2-token markers hold everywhere.
