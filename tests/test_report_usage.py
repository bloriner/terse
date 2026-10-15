"""Tests for billing.report_usage (Stripe metered-usage reporting).

Real tests: receipt signature verification (valid + tampered), aggregation
math, outbox retry semantics (no double-report), and idempotency keys.
Everything is in-process; no network calls are ever made by these tests.
"""

import json
import os

import pytest

from billing.receipts import (canonical_bytes, partition_verified,
                              period_bounds, sign_receipt, verify_receipt)
from billing.report_usage import (NullDeliverer, Outbox, StripeRestDeliverer,
                                  UsageEvent, build_usage_events,
                                  make_idempotency_key, report)

SECRET = "test-meter-secret-do-not-use-in-prod"
CUSTOMER = "cus_TEST123"


def _receipt(rid="abc123", ts="2026-10-15T12:00:00+00:00", saved=1000):
    return {
        "receipt_id": rid,
        "ts": ts,
        "terse_tokens": 100,
        "python_tokens": 1100,
        "tokens_saved": saved,
        "savings_pct": 90.91,
        "price_usd": round(saved * 0.30 / 1_000_000, 6),
        "stdout_match": True,
        "method": "tiktoken-cl100k",
        "flags": [],
    }


# ---------------------------------------------------------------------------
# Receipt signing / verification
# ---------------------------------------------------------------------------

def test_sign_verify_roundtrip():
    rec = sign_receipt(_receipt(), SECRET)
    assert rec["sig"].startswith("hmac-sha256:")
    ok, reason = verify_receipt(rec, SECRET)
    assert ok, reason


def test_canonical_is_key_order_independent():
    a = _receipt()
    b = {k: a[k] for k in reversed(list(a))}
    assert canonical_bytes(a) == canonical_bytes(b)


def test_verify_tampered_tokens_fails():
    rec = sign_receipt(_receipt(saved=1000), SECRET)
    rec["tokens_saved"] = 1_000_000  # tamper after signing
    ok, reason = verify_receipt(rec, SECRET)
    assert not ok and "mismatch" in reason


def test_verify_tampered_flag_fails():
    rec = sign_receipt(_receipt(), SECRET)
    rec["flags"] = ["anomaly: savings_pct > 90 — review baseline"]
    ok, _ = verify_receipt(rec, SECRET)
    assert not ok


def test_verify_wrong_secret_fails():
    rec = sign_receipt(_receipt(), SECRET)
    ok, reason = verify_receipt(rec, "some-other-secret")
    assert not ok and "mismatch" in reason


def test_verify_missing_sig_fails():
    ok, reason = verify_receipt(_receipt(), SECRET)  # never signed
    assert not ok and "missing signature" in reason


def test_verify_sig_stripped_fails():
    rec = sign_receipt(_receipt(), SECRET)
    del rec["sig"]
    ok, _ = verify_receipt(rec, SECRET)
    assert not ok


def test_verify_missing_field_fails():
    rec = sign_receipt(_receipt(), SECRET)
    del rec["method"]
    ok, reason = verify_receipt(rec, SECRET)
    assert not ok and "missing required fields" in reason


def test_partition_verified_separates_good_and_bad():
    good = sign_receipt(_receipt("r1"), SECRET)
    tampered = sign_receipt(_receipt("r2"), SECRET)
    tampered["tokens_saved"] += 1
    unsigned = _receipt("r3")
    ok_list, bad_list = partition_verified([good, tampered, unsigned], SECRET)
    assert [r["receipt_id"] for r in ok_list] == ["r1"]
    assert {r["receipt_id"] for r, _ in bad_list} == {"r2", "r3"}


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _signed_set():
    return [
        sign_receipt(_receipt("r1", "2026-10-05T10:00:00+00:00", 1000), SECRET),
        sign_receipt(_receipt("r2", "2026-10-20T10:00:00+00:00", 2500), SECRET),
        sign_receipt(_receipt("r3", "2026-11-02T10:00:00+00:00", 400), SECRET),
    ]


def test_aggregation_sums_per_customer_per_period():
    events, skipped = build_usage_events(_signed_set(), CUSTOMER)
    assert skipped == []
    by_period = {e.period: e for e in events}
    assert set(by_period) == {"2026-10", "2026-11"}
    assert by_period["2026-10"].tokens_saved == 3500
    assert by_period["2026-11"].tokens_saved == 400
    assert by_period["2026-10"].receipt_ids == ["r1", "r2"]
    assert by_period["2026-10"].customer_id == CUSTOMER
    start, end = period_bounds("2026-10-05T10:00:00+00:00")
    assert by_period["2026-10"].period_start == start
    assert by_period["2026-10"].period_end == end


def test_aggregation_skips_non_billable_receipts():
    recs = [
        sign_receipt(_receipt("pos", saved=100), SECRET),
        sign_receipt(_receipt("zero", saved=0), SECRET),
        sign_receipt(_receipt("neg", saved=-50), SECRET),
    ]
    events, skipped = build_usage_events(recs, CUSTOMER)
    assert len(events) == 1 and events[0].tokens_saved == 100
    assert {s["receipt_id"] for s in skipped} == {"zero", "neg"}


def test_aggregation_empty_input():
    events, skipped = build_usage_events([], CUSTOMER)
    assert events == [] and skipped == []


def test_period_bounds_calendar_month():
    start, end = period_bounds("2026-10-15T12:34:56+00:00")
    assert start == "2026-10-01T00:00:00+00:00"
    assert end == "2026-11-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Idempotency keys
# ---------------------------------------------------------------------------

def test_idempotency_key_deterministic():
    k1 = make_idempotency_key(CUSTOMER, "2026-10", ["r1", "r2"])
    k2 = make_idempotency_key(CUSTOMER, "2026-10", ["r2", "r1"])  # order-free
    assert k1 == k2
    assert k1.startswith("terse-usage:cus_TEST123:2026-10:")


def test_idempotency_key_changes_with_inputs():
    base = make_idempotency_key(CUSTOMER, "2026-10", ["r1"])
    assert make_idempotency_key(CUSTOMER, "2026-11", ["r1"]) != base
    assert make_idempotency_key(CUSTOMER, "2026-10", ["r1", "r2"]) != base
    assert make_idempotency_key("cus_OTHER", "2026-10", ["r1"]) != base


# ---------------------------------------------------------------------------
# Outbox + report() retry semantics
# ---------------------------------------------------------------------------

class FlakyDeliverer:
    """Fails the first `fail_times` calls, then succeeds (records calls)."""

    def __init__(self, fail_times=0):
        self.fail_times = fail_times
        self.calls: list[str] = []

    def __call__(self, event: UsageEvent) -> str:
        self.calls.append(event.idempotency_key)
        if len(self.calls) <= self.fail_times:
            raise RuntimeError("boom")
        return f"reported:{event.idempotency_key}"


def _event(key_suffix="k1", saved=1000):
    return UsageEvent(
        customer_id=CUSTOMER,
        event_name="tokens_saved",
        period="2026-10",
        period_start="2026-10-01T00:00:00+00:00",
        period_end="2026-11-01T00:00:00+00:00",
        tokens_saved=saved,
        receipt_ids=["r1"],
        idempotency_key=f"terse-usage:{CUSTOMER}:2026-10:{key_suffix}",
    )


def test_outbox_enqueue_then_pending(tmp_path):
    box = Outbox(str(tmp_path / "outbox.jsonl"))
    ev = _event()
    assert box.enqueue(ev) == "enqueued"
    assert box.status(ev.idempotency_key) == "enqueued"
    pending = box.pending()
    assert len(pending) == 1 and pending[0].tokens_saved == 1000


def test_report_no_double_report(tmp_path):
    box = Outbox(str(tmp_path / "outbox.jsonl"))
    d = FlakyDeliverer()
    s1 = report([_event()], outbox=box, deliver=d)
    assert s1["reported"] == 1 and len(d.calls) == 1
    # Second run over the same event: already reported -> skipped, no delivery.
    s2 = report([_event()], outbox=box, deliver=d)
    assert s2["skipped_already_reported"] == 1
    assert s2["reported"] == 0 and len(d.calls) == 1


def test_retry_semantics_flaky_then_success(tmp_path):
    box = Outbox(str(tmp_path / "outbox.jsonl"))
    d = FlakyDeliverer(fail_times=1)
    s1 = report([_event()], outbox=box, deliver=d)
    assert s1["reported"] == 0 and s1["pending"] == 1
    assert box.status(_event().idempotency_key) in ("enqueued", "failed")

    retry = box.retry_pending(d)
    assert retry == {"attempted": 1, "reported": 1, "still_pending": 0}
    assert box.pending() == []
    assert len(d.calls) == 2  # one failed attempt + one successful retry


def test_retry_does_not_redeliver_reported(tmp_path):
    box = Outbox(str(tmp_path / "outbox.jsonl"))
    d = FlakyDeliverer()
    report([_event()], outbox=box, deliver=d)
    retry = box.retry_pending(d)
    assert retry["attempted"] == 0 and len(d.calls) == 1


def test_outbox_survives_restart_no_double_report(tmp_path):
    path = str(tmp_path / "outbox.jsonl")
    d = FlakyDeliverer()
    report([_event()], outbox=Outbox(path), deliver=d)
    # New Outbox instance = process restart; index rebuilt from the log.
    box2 = Outbox(path)
    assert box2.status(_event().idempotency_key) == "reported"
    s = report([_event()], outbox=box2, deliver=d)
    assert s["skipped_already_reported"] == 1 and len(d.calls) == 1


def test_report_default_deliverer_queues_without_network(tmp_path):
    box = Outbox(str(tmp_path / "outbox.jsonl"))
    s = report([_event()], outbox=box)  # no deliverer -> NullDeliverer
    assert s["reported"] == 0 and s["pending"] == 1 and s["errors"] == []
    assert box.pending()  # still queued for a later run


def test_report_continues_past_errors(tmp_path):
    box = Outbox(str(tmp_path / "outbox.jsonl"))

    def boom(event):
        raise RuntimeError("network down")

    s = report([_event("k1"), _event("k2")], outbox=box, deliver=boom)
    assert s["reported"] == 0 and s["pending"] == 2
    assert len(s["errors"]) == 2
    assert len(box.pending()) == 2


def test_outbox_ignores_corrupt_lines(tmp_path):
    path = tmp_path / "outbox.jsonl"
    path.write_text("not json at all\n", encoding="utf-8")
    box = Outbox(str(path))  # must not raise
    box.enqueue(_event())
    assert box.status(_event().idempotency_key) == "enqueued"


# ---------------------------------------------------------------------------
# Stripe REST deliverer safety gates
# ---------------------------------------------------------------------------

def test_stripe_rest_deliverer_requires_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv("TERSE_STRIPE_REPORT_ENABLED", raising=False)
    d = StripeRestDeliverer("sk_test_fake")
    with pytest.raises(Exception, match="disabled"):
        d(_event())


def test_stripe_rest_deliverer_needs_key():
    with pytest.raises(ValueError):
        StripeRestDeliverer("")


def test_stripe_params_shape():
    ev = _event()
    params = ev.stripe_params()
    assert params["event_name"] == "tokens_saved"
    assert params["payload[value]"] == "1000"
    assert params["payload[stripe_customer_id]"] == CUSTOMER
    assert params["identifier"] == ev.idempotency_key
    assert params["timestamp"].isdigit()


def test_null_deliverer_raises_unavailable():
    from billing.report_usage import _DeliveryUnavailable
    with pytest.raises(_DeliveryUnavailable):
        NullDeliverer("unit test")( _event())
