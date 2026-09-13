from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import IdempotencyRecord, Plan, Subscription, Tenant, UsageEvent, utc_now
from app.schemas import GenerateRequest, SimulatedTokens
from app.services.errors import (
    IdempotencyConflictError,
    PaymentRequiredError,
    QuotaExceededError,
    TenantNotFoundError,
)
from app.services.pricing import calculate_usage_cost, pricing_table


GENERATE_ENDPOINT = "POST /api/v1/generate"
ACTIVE_STATUSES = {"active", "trialing"}


@dataclass(frozen=True)
class MeteringResult:
    status_code: int
    body: dict[str, Any]
    replayed: bool = False


@dataclass(frozen=True)
class UsageRollup:
    api_calls: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_billable_tokens: int = 0


def current_calendar_month_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    current = _aware_utc(now or utc_now())
    start = datetime(current.year, current.month, 1, tzinfo=timezone.utc)
    if current.month == 12:
        end = datetime(current.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(current.year, current.month + 1, 1, tzinfo=timezone.utc)
    return start, end


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _seconds_until(value: datetime, now: datetime | None = None) -> int:
    delta = _aware_utc(value) - _aware_utc(now or utc_now())
    return max(0, int(delta.total_seconds()))


def _request_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _metadata_int(metadata: dict[str, Any], key: str) -> int:
    value = metadata.get(key, 0)
    if value is None:
        return 0
    return int(value)


class MeteringService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def generate(
        self,
        *,
        tenant_id: uuid.UUID,
        idempotency_key: str,
        request: GenerateRequest,
    ) -> MeteringResult:
        request_payload = request.model_dump(mode="json")
        digest = _request_hash(request_payload)

        try:
            async with self.session.begin():
                return await self._generate_in_transaction(
                    tenant_id=tenant_id,
                    idempotency_key=idempotency_key,
                    request=request,
                    request_hash=digest,
                )
        except IntegrityError:
            await self.session.rollback()
            existing = await self._get_idempotency_record(tenant_id, idempotency_key)
            if existing is not None:
                return self._cached_result(existing, request_hash=digest)
            raise IdempotencyConflictError("Idempotency key was claimed by another in-flight request.")

    async def current_usage(self, tenant_id: uuid.UUID) -> dict[str, Any]:
        tenant = await self.session.get(Tenant, tenant_id)
        if tenant is None:
            raise TenantNotFoundError(str(tenant_id))

        subscription = await self._active_subscription(tenant_id)
        rollup = await self._usage_rollup(
            tenant_id=tenant_id,
            start=subscription.current_period_start,
            end=subscription.current_period_end,
        )
        cost = calculate_usage_cost(
            api_calls=rollup.api_calls,
            input_tokens=rollup.input_tokens,
            cached_input_tokens=rollup.cached_input_tokens,
            output_tokens=rollup.output_tokens,
            reasoning_tokens=rollup.reasoning_tokens,
        )

        return {
            "tenant_id": str(tenant.id),
            "plan": {
                "id": subscription.plan.id,
                "name": subscription.plan.name,
                "status": subscription.status,
                "current_period_start": _aware_utc(subscription.current_period_start).isoformat(),
                "current_period_end": _aware_utc(subscription.current_period_end).isoformat(),
                "reset_at": _aware_utc(subscription.current_period_end).isoformat(),
            },
            "usage": {
                "api_calls": {
                    "used": rollup.api_calls,
                    "limit": subscription.plan.api_calls_quota,
                    "remaining": max(subscription.plan.api_calls_quota - rollup.api_calls, 0),
                },
                "tokens": {
                    "input_tokens": rollup.input_tokens,
                    "cached_input_tokens": rollup.cached_input_tokens,
                    "output_tokens": rollup.output_tokens,
                    "reasoning_tokens": rollup.reasoning_tokens,
                    "total_billable_tokens": rollup.total_billable_tokens,
                    "limit": subscription.plan.token_quota,
                    "remaining": max(subscription.plan.token_quota - rollup.total_billable_tokens, 0),
                },
            },
            "cost": cost.as_dict(),
            "pricing": pricing_table(),
        }

    async def _generate_in_transaction(
        self,
        *,
        tenant_id: uuid.UUID,
        idempotency_key: str,
        request: GenerateRequest,
        request_hash: str,
    ) -> MeteringResult:
        existing = await self._get_idempotency_record(tenant_id, idempotency_key, for_update=True)
        if existing is not None:
            return self._cached_result(existing, request_hash=request_hash)

        tenant = await self._tenant_for_update(tenant_id)
        if tenant is None:
            raise TenantNotFoundError(str(tenant_id))

        subscription = await self._active_subscription(tenant_id, for_update=True)
        rollup = await self._usage_rollup(
            tenant_id=tenant_id,
            start=subscription.current_period_start,
            end=subscription.current_period_end,
        )

        tokens = request.simulated_tokens
        self._assert_quota(
            rollup=rollup,
            plan=subscription.plan,
            requested_api_calls=1,
            requested_tokens=tokens.total_billable_tokens,
            period_end=subscription.current_period_end,
        )

        idempotency_record = IdempotencyRecord(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            endpoint=GENERATE_ENDPOINT,
            request_hash=request_hash,
        )
        self.session.add(idempotency_record)
        await self.session.flush()

        cost = calculate_usage_cost(
            api_calls=1,
            input_tokens=tokens.input_tokens,
            cached_input_tokens=tokens.cached_input_tokens,
            output_tokens=tokens.output_tokens,
            reasoning_tokens=tokens.reasoning_tokens,
        )
        api_event_id = uuid.uuid4()
        token_event_id = uuid.uuid4()

        self.session.add_all(
            [
                UsageEvent(
                    id=api_event_id,
                    tenant_id=tenant_id,
                    event_type="api_call",
                    quantity=1,
                    idempotency_key=idempotency_key,
                    event_metadata={"cost_micro_usd": cost.api_call_micro_usd},
                ),
                UsageEvent(
                    id=token_event_id,
                    tenant_id=tenant_id,
                    event_type="ai_tokens",
                    quantity=tokens.total_billable_tokens,
                    idempotency_key=idempotency_key,
                    event_metadata={
                        "input_tokens": tokens.input_tokens,
                        "cached_input_tokens": tokens.cached_input_tokens,
                        "output_tokens": tokens.output_tokens,
                        "reasoning_tokens": tokens.reasoning_tokens,
                        "cost_micro_usd": cost.token_micro_usd,
                    },
                ),
            ]
        )

        response_body = self._generate_response_body(
            tenant_id=tenant_id,
            request=request,
            subscription=subscription,
            rollup=rollup,
            tokens=tokens,
            api_event_id=api_event_id,
            token_event_id=token_event_id,
            cost=cost.as_dict(),
        )
        idempotency_record.response_status = 200
        idempotency_record.response_body = response_body

        return MeteringResult(status_code=200, body=response_body, replayed=False)

    async def _tenant_for_update(self, tenant_id: uuid.UUID) -> Tenant | None:
        result = await self.session.execute(
            select(Tenant).where(Tenant.id == tenant_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def _active_subscription(
        self,
        tenant_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> Subscription:
        now = utc_now()
        statement = (
            select(Subscription)
            .options(selectinload(Subscription.plan))
            .where(
                Subscription.tenant_id == tenant_id,
                Subscription.status.in_(ACTIVE_STATUSES),
                Subscription.current_period_start <= now,
                Subscription.current_period_end > now,
            )
            .limit(1)
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        subscription = result.scalar_one_or_none()
        if subscription is None:
            raise PaymentRequiredError()
        return subscription

    async def _usage_rollup(self, tenant_id: uuid.UUID, start: datetime, end: datetime) -> UsageRollup:
        result = await self.session.execute(
            select(UsageEvent).where(
                UsageEvent.tenant_id == tenant_id,
                UsageEvent.created_at >= start,
                UsageEvent.created_at < end,
            )
        )

        api_calls = 0
        input_tokens = 0
        cached_input_tokens = 0
        output_tokens = 0
        reasoning_tokens = 0
        total_billable_tokens = 0

        for event in result.scalars():
            if event.event_type == "api_call":
                api_calls += event.quantity
                continue
            if event.event_type != "ai_tokens":
                continue

            total_billable_tokens += event.quantity
            metadata = event.event_metadata or {}
            input_tokens += _metadata_int(metadata, "input_tokens")
            cached_input_tokens += _metadata_int(metadata, "cached_input_tokens")
            output_tokens += _metadata_int(metadata, "output_tokens")
            reasoning_tokens += _metadata_int(metadata, "reasoning_tokens")

        return UsageRollup(
            api_calls=api_calls,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            total_billable_tokens=total_billable_tokens,
        )

    async def _get_idempotency_record(
        self,
        tenant_id: uuid.UUID,
        idempotency_key: str,
        *,
        for_update: bool = False,
    ) -> IdempotencyRecord | None:
        statement = select(IdempotencyRecord).where(
            IdempotencyRecord.tenant_id == tenant_id,
            IdempotencyRecord.idempotency_key == idempotency_key,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    def _cached_result(self, record: IdempotencyRecord, *, request_hash: str) -> MeteringResult:
        if record.endpoint != GENERATE_ENDPOINT:
            raise IdempotencyConflictError("Idempotency key was used for a different endpoint.")
        if record.request_hash != request_hash:
            raise IdempotencyConflictError("Idempotency key was reused with a different request body.")
        if record.response_status is None or record.response_body is None:
            raise IdempotencyConflictError("Original request is still being processed; retry shortly.")
        return MeteringResult(
            status_code=record.response_status,
            body=record.response_body,
            replayed=True,
        )

    def _assert_quota(
        self,
        *,
        rollup: UsageRollup,
        plan: Plan,
        requested_api_calls: int,
        requested_tokens: int,
        period_end: datetime,
    ) -> None:
        quota = {
            "api_calls": {
                "used": rollup.api_calls,
                "requested": requested_api_calls,
                "limit": plan.api_calls_quota,
                "would_be": rollup.api_calls + requested_api_calls,
            },
            "ai_tokens": {
                "used": rollup.total_billable_tokens,
                "requested": requested_tokens,
                "limit": plan.token_quota,
                "would_be": rollup.total_billable_tokens + requested_tokens,
            },
        }
        retry_after = _seconds_until(period_end)
        if rollup.api_calls + requested_api_calls > plan.api_calls_quota:
            raise QuotaExceededError(
                metric="api_calls",
                current_usage=rollup.api_calls,
                requested=requested_api_calls,
                limit=plan.api_calls_quota,
                retry_after_seconds=retry_after,
                quota=quota,
            )
        if rollup.total_billable_tokens + requested_tokens > plan.token_quota:
            raise QuotaExceededError(
                metric="ai_tokens",
                current_usage=rollup.total_billable_tokens,
                requested=requested_tokens,
                limit=plan.token_quota,
                retry_after_seconds=retry_after,
                quota=quota,
            )

    def _generate_response_body(
        self,
        *,
        tenant_id: uuid.UUID,
        request: GenerateRequest,
        subscription: Subscription,
        rollup: UsageRollup,
        tokens: SimulatedTokens,
        api_event_id: uuid.UUID,
        token_event_id: uuid.UUID,
        cost: dict[str, int | str],
    ) -> dict[str, Any]:
        return {
            "tenant_id": str(tenant_id),
            "result": {
                "text": "Dummy generation completed.",
                "prompt_echo": request.prompt,
            },
            "usage_events": {
                "api_call_event_id": str(api_event_id),
                "ai_tokens_event_id": str(token_event_id),
            },
            "usage": {
                "api_calls": 1,
                "tokens": {
                    "input_tokens": tokens.input_tokens,
                    "cached_input_tokens": tokens.cached_input_tokens,
                    "output_tokens": tokens.output_tokens,
                    "reasoning_tokens": tokens.reasoning_tokens,
                    "total_billable_tokens": tokens.total_billable_tokens,
                },
            },
            "quota": {
                "api_calls": {
                    "used": rollup.api_calls + 1,
                    "limit": subscription.plan.api_calls_quota,
                    "remaining": max(subscription.plan.api_calls_quota - rollup.api_calls - 1, 0),
                },
                "ai_tokens": {
                    "used": rollup.total_billable_tokens + tokens.total_billable_tokens,
                    "limit": subscription.plan.token_quota,
                    "remaining": max(
                        subscription.plan.token_quota
                        - rollup.total_billable_tokens
                        - tokens.total_billable_tokens,
                        0,
                    ),
                },
            },
            "cost": cost,
            "plan": {
                "id": subscription.plan.id,
                "name": subscription.plan.name,
                "status": subscription.status,
                "reset_at": _aware_utc(subscription.current_period_end).isoformat(),
            },
        }
