# Terse Think — compact reasoning-trace format for AI coding agents

v0.1 (2026-10-15). Status: format draft + measured examples. **Not yet tested
with real agents** — all effectiveness claims are hypotheses (see §7).

## 1. Why

Agents spend 10–50× more tokens reasoning (chain-of-thought) than on final
code. Terse compresses code to 0.69–1.00× Python; Terse Think applies the
same measurement-driven philosophy to the reasoning trace itself.

The win is structural, not lexical. English content words cost ~1 token each
and can't be compressed without losing meaning. What *can* go is the
discourse scaffolding agents emit constantly: "Let me think about this",
"First, I should consider", "It's worth noting that", "On the other hand",
"Now let me verify". That scaffolding is 5–10 tokens per instance, repeated
dozens of times per trace — pure overhead, like the `(call ...)` wrapper
was for code.

## 2. Syntax

A trace is a sequence of **lines**. Each line is one reasoning move:

```
MARKER: content
```

- `MARKER` is one of the 10 markers in §3, followed by a colon.
- Content is a **compressed noun phrase**, not a sentence. No subject-verb
  scaffolding ("I will", "let me", "we should"). Write the thought, not the
  meta-thought.
- One move per line. Order is significant (plan steps run top to bottom;
  debug lines run in time order).
- Blank lines between phases are allowed and free-ish (1 token); use them
  sparingly.
- There is no parser and no validation. Terse Think is a *convention* an LLM
  follows from a prompt template, not a grammar. (Mechanical enough to emit
  reliably; no tooling required to write it.)

## 3. Marker catalog

Measured with tiktoken `cl100k_base`. Cost = tokens for the marker text.

| marker | tokens | name | when to use |
|--------|--------|------|-------------|
| `G:` | 2 | goal | restate the task in one compressed line |
| `P:` | 2 | plan | one plan step per line; order = execution order |
| `A:` | 2 | approach | one candidate approach per line |
| `T:` | 2 | tradeoff | `+pro` / `-con` items, ending in `-> decision` |
| `V:` | 2 | verify | a check: edge case, test value, invariant |
| `H:` | 2 | hypothesis | one candidate explanation per line (debugging) |
| `O:` | 2 | observe | evidence: test output, error text, grep result |
| `F:` | 2 | fix | action taken: the change + rerun |
| `?:` | 1 | question | uncertainty, or evaluating a hypothesis |
| `!:` | 1 | decide | conclusion / verdict; ends the trace or a phase |

Numbering (`P1:`, `H2:`) costs 3 tokens vs 2 — number a marker only when a
later line references it by number. Otherwise repeat the bare marker; order
carries the meaning.

## 4. Content style rules

1. **Noun phrases, not sentences.** `recursive fib, base (le n 1)->n`
   not "I'll implement the Fibonacci function recursively with n <= 1 as
   the base case".
2. **Reference code in Terse (or the target language) inline.** A Terse
   fragment is denser than its English description — same BPE wins as the
   language itself.
3. **Tradeoffs on one line when possible.** `T: +iter fast / -more code ->
   iter` beats a paragraph.
4. **Observations quote the signal.** `O: [E200] unbound 'fib'` — the error
   code and name, not "the test failed with an error about something".
5. **Decisions are short.** `!: go recursive + guard`. The `!:` line is the
   trace's return value.
6. **Banned scaffolding** (never emit these): "let me think", "first,",
   "it's worth noting", "on the other hand", "in other words", "to
   summarize", "I'm confident", "as an AI". If a sentence starts with
   throat-clearing, delete the throat-clearing.

## 5. Canonical patterns

**Plan + tradeoffs + verification** (design tasks):
```
G: <goal in one line>
P: <step 1>
P: <step 2>
A: <option 1> vs <option 2>
T: +<pro> / -<con> -> <choice>
V: <edge case> -> <guard>
V: <known value> check after write
!: <verdict>; <next action>
```

**Debug loop** (failing test / error):
```
O: <failure signal + key detail>
H: <hypothesis 1>
H: <hypothesis 2>
?: <why H1 is unlikely, or what would discriminate>
O: <discriminating evidence> -> <Hn> confirmed
F: <fix>; rerun
O: <result>
!: done
```

## 6. Measured results

Two worked pairs in `examples/` (verbose plain-English CoT vs Terse Think,
same content, tiktoken `cl100k_base`):

| example | English | Think | ratio | saved |
|---------|---------|-------|-------|-------|
| fib-plan (design + edge cases) | 333 | 148 | 0.44 | 56% |
| debug-test (hypothesis + check loop) | 281 | 110 | 0.39 | 61% |

Method: the English versions were written to match realistic agent
chain-of-thought (hedging, scaffolding, self-narration included — not a
strawman). The Think versions preserve every substantive point; only
scaffolding and sentence-grammar were removed.

## 7. Honest limitations (hypotheses, not findings)

- **Untested with real agents.** Nothing here has been A/B tested on live
  reasoning. Compressed traces *might* reduce reasoning quality: the
  scaffolding we delete ("let me think", "on the other hand") may serve as
  compute — extra tokens that let the model think longer. This is the
  single biggest open question.
- **Content words don't compress.** A trace about an unfamiliar domain is
  still long; Think only removes structure, not substance.
- **Ambiguity risk.** Noun phrases are denser but less explicit than
  sentences. For genuinely tricky reasoning, full sentences may reason
  better. Suggested rule: Think by default, expand to sentences at points
  of real confusion (and mark them — the format doesn't forbid prose, it
  just prices it).
- **No validation.** Unlike Terse code (GBNF-constrained), nothing checks
  a Think trace. Malformed traces fail silently into vagueness.
- **Model-dependent.** Measured on `cl100k_base`; other tokenizers will
  differ slightly. Markers are 1–2 tokens everywhere, so the structure
  holds, but exact ratios will vary.
