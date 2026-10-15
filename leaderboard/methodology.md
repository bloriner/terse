# TTCP Leaderboard — methodology

**TTCP** = *Time-To-Correct-Program*: wall-clock time from task prompt to a
program that passes the task's tests, including all repair iterations. The
leaderboard's v1 metric is the part we can measure deterministically today:
**token ratio** — how many tokens a correct Terse solution costs versus an
idiomatic Python solution for the same task. Values under 1.0 mean Terse is
token-cheaper for an agent to emit.

## The task set (fixed)

Eight tasks in `bench/tasks/`, each a `<name>.terse` + `<name>.py` pair:

| task | what it does |
|------|--------------|
| factorial | recursive factorial |
| fib | fibonacci |
| fizzbuzz | fizzbuzz to 20 |
| gcd | greatest common divisor |
| palindrome | palindrome check |
| primes | sieve / prime generation |
| transform | list transform pipeline |
| wordcount | word frequency count |

The task set is versioned with the leaderboard (`version` in `results.json`).
New tasks are added only in new versions; old versions stay frozen so
history stays comparable.

## What counts

1. **Correctness is binary and non-negotiable.** A submission's Terse
   solution must produce byte-identical stdout to the task's Python
   reference when run with the same inputs. Any mismatch = the task scores
   as failed and the entry is rejected outright. There is no partial credit
   and no "savings" claimed for wrong programs.
2. **Gas-capped execution.** Terse solutions run under the interpreter's
   default gas budget (currently 1,000,000). Exhaustion = failure.
3. **Token counting.** Both sources are counted with tiktoken
   `cl100k_base` (tiktoken 0.14.0 at seed time; the version is pinned in
   `results.json`'s `tokenizer` field and re-pinned on every update).
   Ratios are `terse_tokens / python_tokens`, rounded to two decimals.

## Metrics

- **Primary: average token ratio** across the 8 tasks (unweighted mean).
  This is what the table ranks by. Lower is better.
- **Secondary (defined, not yet run): first-try validity.** The percentage
  of tasks where the model's first emitted program passes without repair.
  This requires live agent runs with repair loops driven by the error
  protocol; the harness for it does not exist yet. When it does, it becomes
  a second ranked column. We name it now so submissions can't claim it
  retroactively.

## Submission spec

A submission is a directory:

```
submission/
  meta.json            # {"model": "name", "date": "YYYY-MM-DD", "notes": "..."}
  solutions/
    factorial.terse
    fib.terse
    ... (one per task)
```

Run `python tools/score_submission.py submission/` from the repo root. It
re-runs every solution, verifies stdout against the Python references,
counts tokens, and prints the `results.json` entry to stdout. We re-run all
submissions ourselves before publishing — never trust a submitted score.

## Anti-gaming rules

- **No lookup tables.** Solutions must compute the answer from the task's
  inputs. Precomputed outputs keyed to the task's exact inputs
  (`(if (eq n 20) "55" ...)` style) are disqualified on sight.
- **Natural solutions only.** The reference Python baselines are idiomatic,
  not golfed; submissions are held to the same standard. Minified or
  deliberately obfuscated Terse is rejected.
- **We re-run everything.** Submitted token counts are informational only;
  the published number is what our harness measures.
- **Anomaly flags.** Entries with `avg_ratio < 0.5` or any single-task
  ratio `< 0.4` are held for manual review before publishing.

## What this doesn't measure

- **Reasoning tokens.** The leaderboard counts solution tokens only. A
  model that "saves" 20 solution tokens while burning 2,000 extra thinking
  tokens is not actually cheaper — see `think/` (Terse Think) for the
  reasoning-trace side of the problem.
- **Repair-loop cost.** v1 has no live agent runs; first-try validity is
  defined but unmeasured. A dense solution that fails three times before
  passing may cost more end-to-end than a verbose one that passes first try.
- **Human readability, debuggability, maintainability.** Terse optimizes
  for machine emission, explicitly not for human eyes. `to_python` exists
  for the humans.
- **Tokenizer coverage.** Counts use `cl100k_base`/`o200k_base`-equivalent
  BPE tokenizers. Models on very different tokenizers (SentencePiece
  families, etc.) may see different ratios — see `notes/dialects.md`.
- **Task breadth.** Eight small algorithmic tasks are a start, not a
  benchmark suite. They were chosen to be unambiguous and verifiable, not
  representative of all coding work.
