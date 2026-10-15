#!/usr/bin/env python3
"""Report Terse metered usage to Stripe (billing meter events).

Pipeline:

    POST /meter (api/server.py)
        -> signed receipt (HMAC-SHA256, TERSE_METER_SECRET)
        -> this module: verify -> aggregate per customer per calendar month
        -> usage events
        -> report(): deliver to Stripe, or hold in a durable JSONL outbox

``report(events)`` is the clean interface. Delivery to Stripe requires the
operation ``POST /v1/billing/meter_events`` — "Create a billing meter event"
(https://docs.stripe.com/api/billing/meter-event/create). As of 2026-10-15
that operation is NOT available to the assistant's reviewed MCP toolset
(only event-listing reads like GET /v1/events exist), so live delivery goes
through ``StripeRestDeliverer`` below, which is gated behind the explicit
opt-in ``TERSE_STRIPE_REPORT_ENABLED=1``. Default behavior: events are held
in the outbox as pending, nothing is sent anywhere.

TEST-MODE THINKING ONLY: there are no customers yet and no real events are
reported. ``report()`` with no deliverer never touches the network.

Idempotency:
  * Every usage event gets a deterministic idempotency key
    (``terse-usage:<customer>:<YYYY-MM>:<short hash of receipt ids>``).
  * The outbox folds its append-only log so an event already marked
    "reported" is never delivered again, even across restarts.
  * The same idempotency key is sent as Stripe's ``identifier`` parameter,
    which additionally dedupes server-side (rolling 24h window).

Stdlib only. Config comes from the environment:

  STRIPE_CUSTOMER_ID      Stripe customer (cus_...) to attribute usage to.
  TERSE_METER_SECRET      HMAC secret used to sign/verify receipts.
  TERSE_METER_EVENT_NAME  Billing meter event name (default: "tokens_saved").
  TERSE_OUTBOX_PATH       JSONL outbox path (default: billing/outbox.jsonl).
  TERSE_STRIPE_SECRET_KEY Secret key for direct REST delivery (only used when
                          TERSE_STRIPE_REPORT_ENABLED=1).
  TERSE_STRIPE_REPORT_ENABLED  Set to "1" to allow real Stripe delivery.

No account IDs or secrets are hardcoded anywhere in this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field

from .receipts import (partition_verified, period_bounds, period_label,
                       receipts_from_jsonl, receipts_from_ledger,
                       receipts_from_ledger_sqlite)

DEFAULT_EVENT_NAME = "tokens_saved"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class BillingConfig:
    customer_id: str = ""
    meter_secret: str = ""
    event_name: str = DEFAULT_EVENT_NAME
    outbox_path: str = ""
    ledger_base: str = "http://127.0.0.1:8080"
    stripe_secret_key: str = ""
    stripe_report_enabled: bool = False


def load_config() -> BillingConfig:
    """Read billing config from the environment (never hardcoded)."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return BillingConfig(
        customer_id=os.environ.get("STRIPE_CUSTOMER_ID", ""),
        meter_secret=os.environ.get("TERSE_METER_SECRET", ""),
        event_name=os.environ.get("TERSE_METER_EVENT_NAME", DEFAULT_EVENT_NAME),
        outbox_path=os.environ.get(
            "TERSE_OUTBOX_PATH",
            os.path.join(repo_root, "billing", "outbox.jsonl")),
        ledger_base=os.environ.get("TERSE_LEDGER_BASE", "http://127.0.0.1:8080"),
        stripe_secret_key=os.environ.get("TERSE_STRIPE_SECRET_KEY", ""),
        stripe_report_enabled=os.environ.get("TERSE_STRIPE_REPORT_ENABLED") == "1",
    )


# ---------------------------------------------------------------------------
# Usage events
# ---------------------------------------------------------------------------

@dataclass
class UsageEvent:
    """Billable usage: total tokens saved for one customer in one period."""
    customer_id: str
    event_name: str
    period: str                    # "YYYY-MM" (UTC calendar month)
    period_start: str              # ISO
    period_end: str                # ISO
    tokens_saved: int              # sum aggregation over the period
    receipt_ids: list[str] = field(default_factory=list)
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        if not self.idempotency_key:
            self.idempotency_key = make_idempotency_key(
                self.customer_id, self.period, self.receipt_ids)

    def stripe_params(self) -> dict:
        """Parameters for POST /v1/billing/meter_events."""
        ts = int(time.time())
        return {
            "event_name": self.event_name,
            "payload[value]": str(self.tokens_saved),
            "payload[stripe_customer_id]": self.customer_id,
            "identifier": self.idempotency_key,
            "timestamp": str(ts),
        }


def make_idempotency_key(customer_id: str, period: str,
                         receipt_ids: list[str]) -> str:
    """Deterministic idempotency key for (customer, period, receipt set).

    Same inputs -> same key, so re-running aggregation over the same
    receipts never creates a second billable event. A different receipt set
    (later receipts in the same month) yields a different key, which is
    correct: Stripe sums both into the period's total.
    """
    digest = hashlib.sha256(
        "\n".join(sorted(receipt_ids)).encode("utf-8")).hexdigest()[:16]
    return f"terse-usage:{customer_id}:{period}:{digest}"


def build_usage_events(receipts: list[dict], customer_id: str,
                       event_name: str = DEFAULT_EVENT_NAME
                       ) -> tuple[list[UsageEvent], list[dict]]:
    """Aggregate verified receipts into per-customer-per-period usage events.

    Returns ``(events, skipped)`` where *skipped* lists receipts excluded
    from billing with reasons (e.g. non-positive savings — Terse saved
    nothing, so the customer is billed nothing, which is the whole pitch).

    Only verified receipts should be passed in; use
    ``billing.receipts.partition_verified`` first.
    """
    groups: dict[str, list[dict]] = {}
    skipped: list[dict] = []
    for rec in receipts:
        saved = rec.get("tokens_saved", 0)
        if not isinstance(saved, (int, float)) or saved <= 0:
            skipped.append({"receipt_id": rec.get("receipt_id"),
                            "reason": f"non-positive tokens_saved ({saved})"})
            continue
        groups.setdefault(period_label(rec["ts"]), []).append(rec)

    events: list[UsageEvent] = []
    for period in sorted(groups):
        batch = groups[period]
        start, end = period_bounds(batch[0]["ts"])
        events.append(UsageEvent(
            customer_id=customer_id,
            event_name=event_name,
            period=period,
            period_start=start,
            period_end=end,
            tokens_saved=int(sum(r["tokens_saved"] for r in batch)),
            receipt_ids=sorted(r["receipt_id"] for r in batch),
        ))
    return events, skipped


# ---------------------------------------------------------------------------
# JSONL outbox (append-only, retryable)
# ---------------------------------------------------------------------------
#
# Every state change is appended as one JSON line:
#   {"t": "enqueued", "key": ..., "event": {...}, "at": ...}
#   {"t": "failed",   "key": ..., "error": ..., "at": ...}
#   {"t": "reported", "key": ..., "detail": ..., "at": ...}
# The latest entry per key wins. A key marked "reported" is never delivered
# again. Pending keys (enqueued/failed) are retried by retry_pending().
# Append-only means crash-safe: a half-written line is the only risk, and it
# is ignored on load.

class Outbox:
    def __init__(self, path: str):
        self.path = path
        self._index: dict[str, dict] = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # half-written line from a crash: ignore
                    key = rec.get("key")
                    if key:
                        self._index[key] = rec

    def _append(self, rec: dict) -> None:
        rec = dict(rec)
        rec.setdefault("at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        d = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(d, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        self._index[rec["key"]] = rec

    def enqueue(self, event: UsageEvent) -> str:
        """Queue an event. Returns its latest status ("enqueued"/"reported")."""
        key = event.idempotency_key
        cur = self._index.get(key)
        if cur and cur.get("t") in ("enqueued", "failed", "reported"):
            return cur["t"]
        self._append({"t": "enqueued", "key": key, "event": asdict(event)})
        return "enqueued"

    def mark_reported(self, key: str, detail: str = "") -> None:
        self._append({"t": "reported", "key": key, "detail": detail})

    def mark_failed(self, key: str, error: str) -> None:
        self._append({"t": "failed", "key": key, "error": error})

    def status(self, key: str) -> str:
        """Latest status for a key: "enqueued", "failed", "reported", or "unknown"."""
        cur = self._index.get(key)
        return cur["t"] if cur else "unknown"

    def pending(self) -> list[UsageEvent]:
        """Events not yet successfully reported (enqueued or failed)."""
        out: list[UsageEvent] = []
        for rec in self._index.values():
            if rec.get("t") in ("enqueued", "failed"):
                ev = rec.get("event") or {}
                out.append(UsageEvent(
                    customer_id=ev.get("customer_id", ""),
                    event_name=ev.get("event_name", DEFAULT_EVENT_NAME),
                    period=ev.get("period", ""),
                    period_start=ev.get("period_start", ""),
                    period_end=ev.get("period_end", ""),
                    tokens_saved=int(ev.get("tokens_saved", 0)),
                    receipt_ids=list(ev.get("receipt_ids", [])),
                    idempotency_key=rec["key"],
                ))
        return out

    def retry_pending(self, deliver) -> dict:
        """Re-attempt delivery of every pending event. Returns a summary."""
        summary = {"attempted": 0, "reported": 0, "still_pending": 0}
        for event in self.pending():
            summary["attempted"] += 1
            try:
                detail = deliver(event)
            except Exception as exc:  # noqa: BLE001 - recorded, not raised
                self.mark_failed(event.idempotency_key, f"{type(exc).__name__}: {exc}")
                summary["still_pending"] += 1
                continue
            self.mark_reported(event.idempotency_key, detail)
            summary["reported"] += 1
        return summary


# ---------------------------------------------------------------------------
# Deliverers
# ---------------------------------------------------------------------------

class NullDeliverer:
    """Default deliverer: records intent, sends nothing.

    Used when the Stripe meter-event operation is unavailable or live
    delivery is not enabled. ``report()`` keeps events pending in the
    outbox so a later run (with a real deliverer) can pick them up.
    """

    def __init__(self, reason: str = "stripe meter-event operation unavailable"):
        self.reason = reason

    def __call__(self, event: UsageEvent) -> str:
        raise _DeliveryUnavailable(self.reason)


class _DeliveryUnavailable(Exception):
    """Raised when there is no live path to Stripe right now."""


class StripeRestDeliverer:
    """Deliver usage events via direct REST: POST /v1/billing/meter_events.

    This is the operation missing from the assistant's reviewed MCP toolset
    (see module docstring). Constructing this class does NOT send anything;
    it only posts when called. Requires TERSE_STRIPE_REPORT_ENABLED=1 at
    call time — otherwise it refuses, so test runs can never move real data.
    """

    API_URL = "https://api.stripe.com/v1/billing/meter_events"

    def __init__(self, secret_key: str, event_name: str = DEFAULT_EVENT_NAME):
        if not secret_key:
            raise ValueError("StripeRestDeliverer needs a secret key")
        self.secret_key = secret_key
        self.event_name = event_name

    def __call__(self, event: UsageEvent) -> str:
        if os.environ.get("TERSE_STRIPE_REPORT_ENABLED") != "1":
            raise _DeliveryUnavailable(
                "live Stripe reporting is disabled "
                "(TERSE_STRIPE_REPORT_ENABLED != 1); event held in outbox")
        params = event.stripe_params()
        body = urllib.parse.urlencode(params).encode("utf-8")
        req = urllib.request.Request(
            self.API_URL, data=body, method="POST",
            headers={"Authorization": f"Bearer {self.secret_key}",
                     "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"stripe meter_events POST failed: {exc}") from exc
        return (f"stripe meter_event {payload.get('id', '?')} "
                f"(identifier={payload.get('identifier', '?')})")


# ---------------------------------------------------------------------------
# report() — the clean interface
# ---------------------------------------------------------------------------

def report(events: list[UsageEvent], outbox: Outbox | None = None,
           deliver=None, config: BillingConfig | None = None) -> dict:
    """Deliver usage events to Stripe, or hold them in the outbox.

    - Events already marked "reported" in the outbox are skipped: no
      double-reporting, even across restarts.
    - New events are enqueued, then delivered immediately if *deliver* is
      given and succeeds; failures stay pending for a later retry.
    - With no *deliver* (default), events are queued as pending and the
      returned summary says exactly why nothing was sent.

    Returns a summary dict: {"total", "skipped_already_reported",
    "reported", "pending", "errors"}.
    """
    cfg = config or load_config()
    box = outbox or Outbox(cfg.outbox_path)
    deliver = deliver if deliver is not None else NullDeliverer(
        "stripe meter-event op not in reviewed toolset "
        "(need POST /v1/billing/meter_events); holding in outbox")

    summary = {"total": len(events), "skipped_already_reported": 0,
               "reported": 0, "pending": 0, "errors": []}
    for event in events:
        if box.status(event.idempotency_key) == "reported":
            summary["skipped_already_reported"] += 1
            continue
        box.enqueue(event)
        try:
            detail = deliver(event)
        except _DeliveryUnavailable as exc:
            box.mark_failed(event.idempotency_key, str(exc))
            summary["pending"] += 1
        except Exception as exc:  # noqa: BLE001 - recorded, not raised
            box.mark_failed(event.idempotency_key,
                            f"{type(exc).__name__}: {exc}")
            summary["errors"].append(
                {"key": event.idempotency_key, "error": str(exc)})
            summary["pending"] += 1
        else:
            box.mark_reported(event.idempotency_key, detail)
            summary["reported"] += 1
    return summary


# ---------------------------------------------------------------------------
# End-to-end helper + CLI
# ---------------------------------------------------------------------------

def collect_and_report(cfg: BillingConfig, deliver=None,
                       receipt_source: str = "ledger") -> dict:
    """Full pipeline: load receipts -> verify -> aggregate -> report().

    *receipt_source*: "ledger" (HTTP), "sqlite" (api/ledger.sqlite), or a
    path to a JSONL file of receipts.
    """
    if not cfg.customer_id:
        return {"ok": False, "error": "STRIPE_CUSTOMER_ID is not set"}
    if not cfg.meter_secret:
        return {"ok": False, "error": "TERSE_METER_SECRET is not set"}

    if receipt_source == "ledger":
        receipts = receipts_from_ledger(cfg.ledger_base)
    elif receipt_source == "sqlite":
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        receipts = receipts_from_ledger_sqlite(
            os.path.join(repo_root, "api", "ledger.sqlite"))
    else:
        receipts = receipts_from_jsonl(receipt_source)

    verified, rejected = partition_verified(receipts, cfg.meter_secret)
    events, skipped = build_usage_events(verified, cfg.customer_id,
                                         cfg.event_name)
    box = Outbox(cfg.outbox_path)
    summary = report(events, outbox=box, deliver=deliver, config=cfg)
    return {
        "ok": True,
        "receipts_seen": len(receipts),
        "receipts_verified": len(verified),
        "receipts_rejected": [
            {"receipt_id": r.get("receipt_id"), "reason": reason}
            for r, reason in rejected],
        "events": len(events),
        "skipped_non_billable": skipped,
        "report": summary,
        "outbox": cfg.outbox_path,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Verify Terse meter receipts and report usage to Stripe "
                    "(outbox by default; no live reporting without "
                    "TERSE_STRIPE_REPORT_ENABLED=1).")
    ap.add_argument("--customer-id", default=os.environ.get("STRIPE_CUSTOMER_ID", ""),
                    help="Stripe customer id (or STRIPE_CUSTOMER_ID)")
    ap.add_argument("--source", default="ledger",
                    help="'ledger', 'sqlite', or path to receipts JSONL")
    ap.add_argument("--base", default=os.environ.get("TERSE_LEDGER_BASE",
                                                     "http://127.0.0.1:8080"),
                    help="meter API base URL (or TERSE_LEDGER_BASE)")
    ap.add_argument("--outbox", default="",
                    help="outbox path (or TERSE_OUTBOX_PATH)")
    ap.add_argument("--event-name", default=os.environ.get(
        "TERSE_METER_EVENT_NAME", DEFAULT_EVENT_NAME))
    args = ap.parse_args(argv)

    cfg = load_config()
    if args.customer_id:
        cfg.customer_id = args.customer_id
    if args.base:
        cfg.ledger_base = args.base
    if args.outbox:
        cfg.outbox_path = args.outbox
    cfg.event_name = args.event_name

    result = collect_and_report(cfg, receipt_source=args.source)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
