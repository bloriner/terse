"""Terse billing pipeline: signed meter receipts -> usage events -> Stripe.

Modules:
  billing.receipts     Receipt loading, HMAC-SHA256 signing/verification.
  billing.report_usage Usage aggregation, JSONL outbox, and reporting to
                       Stripe's billing meter events API.

Everything here is stdlib-only. Customer identity and secrets come from
environment variables; nothing account-specific is hardcoded.
"""
