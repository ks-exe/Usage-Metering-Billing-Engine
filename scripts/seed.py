from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Plan, Subscription, Tenant
from app.services.metering import current_calendar_month_window


FREE_TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
PRO_TENANT_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
FREE_SUBSCRIPTION_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
PRO_SUBSCRIPTION_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")


async def _upsert_plan(
    *,
    plan_id: str,
    name: str,
    api_calls_quota: int,
    token_quota: int,
    stripe_price_id: str | None,
) -> None:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            plan = await session.get(Plan, plan_id)
            if plan is None:
                session.add(
                    Plan(
                        id=plan_id,
                        name=name,
                        api_calls_quota=api_calls_quota,
                        token_quota=token_quota,
                        stripe_price_id=stripe_price_id,
                    )
                )
                return

            plan.name = name
            plan.api_calls_quota = api_calls_quota
            plan.token_quota = token_quota
            plan.stripe_price_id = stripe_price_id


async def _upsert_tenant(
    *,
    tenant_id: uuid.UUID,
    name: str,
    subscription_id: uuid.UUID,
    plan_id: str,
    stripe_customer_id: str | None,
    stripe_subscription_id: str | None,
    period_start: datetime,
    period_end: datetime,
) -> None:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            tenant = await session.get(Tenant, tenant_id)
            if tenant is None:
                tenant = Tenant(id=tenant_id, name=name, stripe_customer_id=stripe_customer_id)
                session.add(tenant)
            else:
                tenant.name = name
                tenant.stripe_customer_id = stripe_customer_id

            result = await session.execute(
                select(Subscription).where(Subscription.tenant_id == tenant_id).limit(1)
            )
            subscription = result.scalar_one_or_none()
            if subscription is None:
                session.add(
                    Subscription(
                        id=subscription_id,
                        tenant_id=tenant_id,
                        plan_id=plan_id,
                        stripe_subscription_id=stripe_subscription_id,
                        status="active",
                        current_period_start=period_start,
                        current_period_end=period_end,
                    )
                )
                return

            subscription.plan_id = plan_id
            subscription.stripe_subscription_id = stripe_subscription_id
            subscription.status = "active"
            subscription.current_period_start = period_start
            subscription.current_period_end = period_end


async def seed() -> None:
    period_start, period_end = current_calendar_month_window(datetime.now(timezone.utc))

    await _upsert_plan(
        plan_id="free",
        name="Free",
        api_calls_quota=1_000,
        token_quota=100_000,
        stripe_price_id=None,
    )
    await _upsert_plan(
        plan_id="pro",
        name="Pro",
        api_calls_quota=50_000,
        token_quota=5_000_000,
        stripe_price_id=settings.stripe_pro_price_id or None,
    )
    await _upsert_tenant(
        tenant_id=FREE_TENANT_ID,
        name="Demo Free Tenant",
        subscription_id=FREE_SUBSCRIPTION_ID,
        plan_id="free",
        stripe_customer_id=None,
        stripe_subscription_id=None,
        period_start=period_start,
        period_end=period_end,
    )
    await _upsert_tenant(
        tenant_id=PRO_TENANT_ID,
        name="Demo Pro Tenant",
        subscription_id=PRO_SUBSCRIPTION_ID,
        plan_id="pro",
        stripe_customer_id="cus_test_demo_pro",
        stripe_subscription_id="sub_test_demo_pro",
        period_start=period_start,
        period_end=period_end,
    )

    print("Seeded plans and demo tenants.")
    print(f"Free tenant: {FREE_TENANT_ID}")
    print(f"Pro tenant:  {PRO_TENANT_ID}")


if __name__ == "__main__":
    asyncio.run(seed())
