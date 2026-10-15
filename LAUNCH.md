# Terse launch target list

Compiled 2026-10-15. Sequenced so one day's effort doesn't cannibalize the next:
**GitHub repo first → HN day → other channels 48h later → directories anytime.**

## Pre-launch (his hand only)

- [ ] Publish `bloriner/terse` as a public repo — needs his one-tap approval per GitHub connector write
- [ ] Add GitHub topics to the repo page: `programming-language`, `ai-agents`, `constrained-decoding`, `code-generation`, `open-source`
- [ ] Tag a `v0.1` release once the density pass lands and tests pass (133 tests currently green; license: Apache-2.0)
- [ ] X/Twitter handle — he asked for one; X almost always demands email + phone verification, so this is his tap-through. Handle candidates: `@tryterse`, `@terse_lang`, `@tersecode` (availability uncheckable without an account — first-come on signup)
- [ ] Open question for him: which email should the X account live on (his Gmail vs. project-specific)?

## Tier A — the two that matter

**1. Show HN** (`news.ycombinator.com/submit`, free)
- Title ≤ 80 chars, factual, no superlatives, starts with `Show HN:`. Candidate: "Show HN: Terse — a code format for AI agents that's denser than Python"
- URL must land on something tryable — the public repo works
- Post the explanatory first comment *immediately* after submitting: what it is, the density numbers from the benchmark harness, honest tradeoffs, what feedback he wants
- Best window: Tue–Thu morning US Eastern; he stays present for the first 4–8 hours of replies
- Never solicit upvotes. If it stalls (< ~5 points in 2h), let it die — a respectful re-submit weeks later is allowed

**2. r/ProgrammingLanguages**
- Natural fit (language-design sub), but it wants a substantive design writeup, not an ad. Angle: the constrained-decoding grammar + BPE-density problem
- Check current subreddit rules before posting; disclose authorship

## Tier B — strong signal, slower burn

**3. r/LocalLLaMA** — strictest technical bar of the Reddit options. Verified rules (April 2026): 1-in-10 self-promotion ratio, affiliation disclosure, account gates (~10 karma to comment, subreddit-earned karma to post), no primarily-LLM-generated copy, no sensationalized titles. This crowd asks "what's local?" — the angle that works: the constrained-decoding grammar for local models (Ollama, llama.cpp) + measured token ratios. Lead with numbers, be honest about what failed.
**4. Lobsters** (`lobste.rs`) — invite-only; *only if he already has an account or can get an invite*. Brand-new accounts face a ~70-day wait before they can use the `show` tag. Strong anti-drive-by-self-promo norm: must tag `authored by me`, needs a substantive engineering writeup (not a bare release link), don't cross-post to HN within 48h, don't bump if it lands flat. If no invite exists, skip — one bad-fit submission burns the account.
**5. Changelog News** (`changelog.com/news/submit`) — submission form, own-project submissions explicitly welcomed
**6. Console.dev** (`hello@console.dev`) — curates dev tools and reviews pre-1.0 tools; email pitch

## Companion content (feeds the above)

**7. dev.to** (free, account needed) — long-form "why/how I built it" piece; this becomes the canonical link the HN/Lobsters/Reddit posts can reference. Publish the same day as Show HN.
**8. daily.dev** — submit the dev.to article to relevant communities/squads once live (repo link alone performs worse there)

## Secondary directories (low effort, anytime)

- **Product Hunt** — needs his personal maker account; best days Tue–Thu, launch at 00:01 PT; needs tagline (≤60 chars), description (260 chars), 3–5 gallery images (1270×760), maker-story first comment. Realistic window: Mon–Wed next week (10/19–10/21), not today — assets aren't ready
- **LibHunt** — make sure the repo is listed before the HN post goes live (it captures the mention wave)
- **Uneed** (`uneed.best`), **Fazier** (`fazier.com`), **Microlaunch** (`microlaunch.net`) — free launch directories, submit anytime
- **awesome-lists** — identify 2–3 fitting lists (programming-languages / LLM / agents) and open PRs; each has its own contribution rules
- **Lambda the Ultimate** — academic PL forum, very low traffic, account needed. Optional.

## Notes

- Don't cross-post Tier A/B channels on the same day — give HN 48h to breathe before Lobsters/Reddit
- One person's realistic bandwidth: he can credibly engage with replies for ~72h. Don't add channels he won't reply in — dead threads hurt more than silence
- The X handle and the public GitHub repo are the two gating items; everything else drafts in parallel
