from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from sqlalchemy import func, select

from app.models import Plan, ProcessedWebhookEvent, Subscription, UsageEvent
from app.services.pricing import calculate_usage_cost
from conftest import TEST_TENANT_ID, TestingSessionLocal


def _headers(idempotency_key: str = "idem-test") -> dict[str, str]:
    return {
        "X-Tenant-ID": str(TEST_TENANT_ID),
        "Idempotency-Key": idempotency_key,
    }


def _generate_payload(total_input: int = 500) -> dict:
    return {
        "prompt": "Generate a summary",
        "simulated_tokens": {
            "input_tokens": total_input,
            "cached_input_tokens": 200,
            "output_tokens": 300,
            "reasoning_tokens": 150,
        },
    }


async def _usage_event_count() -> int:
    async with TestingSessionLocal() as session:
        result = await session.execute(select(func.count(UsageEvent.id)))
        return int(result.scalar_one())


@pytest.mark.asyncio
async def test_generate_creates_two_usage_events(client):
    response = await client.post("/api/v1/generate", json=_generate_payload(), headers=_headers("idem-two"))

    assert response.status_code == 200
    body = response.json()
    assert body["usage"]["api_calls"] == 1
    assert body["usage"]["tokens"]["total_billable_tokens"] == 1_150
    assert body["cost"]["api_call_micro_usd"] == 1_000
    assert body["cost"]["fresh_input_micro_usd"] == 750
    assert body["cost"]["cached_input_micro_usd"] == 75
    assert body["cost"]["output_micro_usd"] == 1_800
    assert body["cost"]["reasoning_micro_usd"] == 900
    assert body["cost"]["total_micro_usd"] == 4_525
    assert await _usage_event_count() == 2


@pytest.mark.asyncio
async def test_idempotency_replay_returns_cached_result_without_duplicate_rows(client):
    payload = _generate_payload()

    first = await client.post("/api/v1/generate", json=payload, headers=_headers("idem-replay"))
    second = await client.post("/api/v1/generate", json=payload, headers=_headers("idem-replay"))

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert second.headers["Idempotency-Replayed"] == "true"
    assert await _usage_event_count() == 2


@pytest.mark.asyncio
async def test_idempotency_key_reuse_with_different_body_returns_409(client):
    first = await client.post("/api/v1/generate", json=_generate_payload(500), headers=_headers("idem-conflict"))
    second = await client.post("/api/v1/generate", json=_generate_payload(501), headers=_headers("idem-conflict"))

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "idempotency_conflict"
    assert await _usage_event_count() == 2


@pytest.mark.asyncio
async def test_crossing_token_quota_returns_429_with_retry_after(client):
    within_quota = {
        "prompt": "use the full free quota",
        "simulated_tokens": {
            "input_tokens": 100_000,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
        },
    }
    over_quota = {
        "prompt": "one token too many",
        "simulated_tokens": {
            "input_tokens": 1,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
        },
    }

    ok_response = await client.post("/api/v1/generate", json=within_quota, headers=_headers("idem-quota-1"))
    quota_response = await client.post("/api/v1/generate", json=over_quota, headers=_headers("idem-quota-2"))

    assert ok_response.status_code == 200
    assert quota_response.status_code == 429
    assert "Retry-After" in quota_response.headers
    payload = quota_response.json()
    assert payload["error"]["metric"] == "ai_tokens"
    assert payload["error"]["current_usage"] == 100_000
    assert payload["error"]["requested"] == 1
    assert payload["error"]["limit"] == 100_000
    assert await _usage_event_count() == 2


@pytest.mark.asyncio
async def test_usage_rollup_returns_current_period_categories_and_cost(client):
    await client.post("/api/v1/generate", json=_generate_payload(), headers=_headers("idem-usage"))

    response = await client.get("/api/v1/usage", headers={"X-Tenant-ID": str(TEST_TENANT_ID)})

    assert response.status_code == 200
    body = response.json()
    assert body["plan"]["id"] == "free"
    assert body["usage"]["api_calls"]["used"] == 1
    assert body["usage"]["api_calls"]["limit"] == 1_000
    assert body["usage"]["tokens"]["input_tokens"] == 500
    assert body["usage"]["tokens"]["cached_input_tokens"] == 200
    assert body["usage"]["tokens"]["output_tokens"] == 300
    assert body["usage"]["tokens"]["reasoning_tokens"] == 150
    assert body["usage"]["tokens"]["total_billable_tokens"] == 1_150
    assert body["cost"]["total_micro_usd"] == 4_525


def _stripe_signature(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    ts = timestamp or int(time.time())
    signed_payload = f"{ts}.{payload.decode('utf-8')}".encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={signature}"


@pytest.mark.asyncio
async def test_stripe_signature_validation_dedup_and_checkout_completion(client):
    event = {
        "id": "evt_checkout_completed",
        "object": "event",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_123",
                "object": "checkout.session",
                "customer": "cus_test_new",
                "subscription": "sub_test_123",
                "client_reference_id": str(TEST_TENANT_ID),
                "metadata": {
                    "tenant_id": str(TEST_TENANT_ID),
                    "target_plan": "pro",
                },
            }
        },
    }
    payload = json.dumps(event, separators=(",", ":")).encode("utf-8")
    signature = _stripe_signature(payload, "whsec_test_secret")

    valid = await client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers={"Stripe-Signature": signature},
    )
    duplicate = await client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers={"Stripe-Signature": signature},
    )
    tampered = await client.post(
        "/api/v1/webhooks/stripe",
        content=payload.replace(b"evt_checkout_completed", b"evt_tampered_event"),
        headers={"Stripe-Signature": signature},
    )

    assert valid.status_code == 200
    assert valid.json() == {"status": "processed", "event_type": "checkout.session.completed"}
    assert duplicate.status_code == 200
    assert duplicate.json() == {"status": "already_processed"}
    assert tampered.status_code == 400

    async with TestingSessionLocal() as session:
        subscription = (
            await session.execute(select(Subscription).where(Subscription.tenant_id == TEST_TENANT_ID))
        ).scalar_one()
        processed = await session.get(ProcessedWebhookEvent, "evt_checkout_completed")
        assert subscription.plan_id == "pro"
        assert subscription.stripe_subscription_id == "sub_test_123"
        assert processed is not None


def test_token_arithmetic_uses_decimal_micro_units():
    cost = calculate_usage_cost(
        api_calls=1,
        input_tokens=1_000,
        cached_input_tokens=1_000,
        output_tokens=1_000,
        reasoning_tokens=500,
    )

    assert cost.api_call_micro_usd == 1_000
    assert cost.fresh_input_micro_usd == 1_500
    assert cost.cached_input_micro_usd == 375
    assert cost.output_micro_usd == 6_000
    assert cost.reasoning_micro_usd == 3_000
    assert cost.total_micro_usd == 11_875
