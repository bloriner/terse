# Terse billing: metered usage → Stripe

How tokens-saved turns into a Stripe invoice, end to end.

## Flow

```
POST /meter  (api/server.py)
   └─ runs the (Terse, Python) pair, verifies identical stdout, meters tokens
   └─ builds a receipt and HMAC-SHA256-signs it (needs TERSE_METER_SECRET)
   └─ stores it in api/ledger.sqlite, returns it

billing.report_usage (this package)
   └─ loads receipts (HTTP /ledger, api/ledger.sqlite, or a receipts JSONL)
   └─ verifies every signature (billing.receipts); rejects unsigned/tampered
   └─ aggregates verified tokens_saved per customer per calendar month (UTC)
   └─ report(events): sends each usage event to Stripe, or holds it pending
                      in billing/outbox.jsonl when Stripe is unreachable
```

## Receipt format

Fields: `receipt_id`, `ts` (UTC ISO), `terse_tokens`, `python_tokens`,
`tokens_saved`, `savings_pct`, `price_usd`, `stdout_match`, `method`
(`tiktoken-cl100k` or `char-estimate`), `flags`, plus `sig`.

`sig` is `hmac-sha256:<hex>` over a canonical JSON encoding of every other
field (sorted keys, compact separators). Receipts minted before signing was
added (2026-10-15), or while `TERSE_METER_SECRET` was unset, carry
`"sig": null` and are **rejected by the billing pipeline** — we never bill
from an unverifiable receipt.

## Reporting a usage event to Stripe

A usage event is one billable line: `(customer_id, event_name, period,
tokens_saved)` with a deterministic idempotency key
`terse-usage:<customer>:<YYYY-MM>:<hash of receipt ids>`. It maps 1:1 onto
Stripe's "Create a billing meter event" call:

```
POST https://api.stripe.com/v1/billing/meter_events
  event_name=tokens_saved
  payload[value]=<tokens_saved>            (meters use sum aggregation)
  payload[stripe_customer_id]=<cus_...>    (the meter's customer_mapping key)
  identifier=<idempotency_key>             (server-side dedupe, rolling 24h)
  timestamp=<unix epoch>
```

The same key lives in the outbox, which provides the long-term dedupe:
once an event is marked `reported`, `report()` never delivers it again,
even across restarts. Non-positive savings are skipped entirely — Terse
saved nothing, the customer pays nothing.

## Honest status (2026-10-15)

- **Receipt signing: DONE.** Added today to `api/server.py` (receipts were
  previously unsigned); `billing/receipts.py` signs and verifies. 27 tests
  green in `tests/test_report_usage.py`, existing `tests/test_api.py`
  (10 tests) still green.
- **Aggregation + outbox: DONE.** Verified receipts aggregate per customer
  per calendar month; the append-only JSONL outbox is crash-safe and
  retryable (`Outbox.retry_pending`).
- **Stripe delivery: CODE-READY, NOT LIVE.** `report(events)` delivers via a
  `deliver` callable. `StripeRestDeliverer` implements the exact call above,
  but it refuses to run unless `TERSE_STRIPE_REPORT_ENABLED=1` — and there
  are **no customers yet, so nothing has ever been reported**. The
  assistant's reviewed MCP toolset does not include any meter-event write
  operation (only event-listing reads such as `GET /v1/events`), so live
  delivery from the assistant goes through this REST path once enabled, or
  through a future reviewed MCP op.
- **Missing operation the parent should request:** `POST
  /v1/billing/meter_events` ("Create a billing meter event",
  https://docs.stripe.com/api/billing/meter-event/create) in the reviewed
  toolset. (Meter *creation* was already known-missing; meter *events* are
  missing too — confirmed via `stripe_api_search` on 2026-10-15.)

## Configuration

Everything comes from the environment; nothing account-specific is
hardcoded:

| Variable | Purpose |
|---|---|
| `STRIPE_CUSTOMER_ID` | customer to attribute usage to (required) |
| `TERSE_METER_SECRET` | HMAC secret for receipt signing/verification (required) |
| `TERSE_METER_EVENT_NAME` | meter event name, default `tokens_saved` |
| `TERSE_OUTBOX_PATH` | JSONL outbox, default `billing/outbox.jsonl` |
| `TERSE_LEDGER_BASE` | meter API base URL, default `http://127.0.0.1:8080` |
| `TERSE_STRIPE_SECRET_KEY` | Stripe secret key (REST delivery only) |
| `TERSE_STRIPE_REPORT_ENABLED` | must be `1` to allow any real Stripe call |

Run the pipeline:

```bash
export TERSE_METER_SECRET=... STRIPE_CUSTOMER_ID=cus_...
python -m billing.report_usage --source ledger     # from the live API
python -m billing.report_usage --source sqlite     # from api/ledger.sqlite
python -m billing.report_usage --source receipts.jsonl
```

Without `TERSE_STRIPE_REPORT_ENABLED=1` this verifies, aggregates, and
queues — it never touches Stripe.
