from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

import stripe
from anyio import to_thread
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models import Plan, ProcessedWebhookEvent, Subscription, Tenant
from app.services.errors import StripeConfigurationError, TenantNotFoundError
from app.services.metering import current_calendar_month_window


def _normalize_plan_id(value: str | None) -> str:
    if not value:
        return "pro"
    normalized = value.strip().lower()
    return {"professional": "pro"}.get(normalized, normalized)


def _normalize_subscription_status(value: str | None) -> str:
    if value in {"active", "trialing"}:
        return "active"
    if value in {"canceled", "incomplete_expired"}:
        return "canceled"
    if value in {"past_due", "unpaid", "incomplete"}:
        return "past_due"
    return value or "past_due"


def _from_unix_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(int(value), tz=timezone.utc)


def _subscription_id_from_object(obj: Mapping[str, Any]) -> str | None:
    subscription = obj.get("subscription")
    if isinstance(subscription, Mapping):
        return subscription.get("id")
    if subscription:
        return str(subscription)
    if obj.get("object") == "subscription" and obj.get("id"):
        return str(obj.get("id"))
    return None


def _customer_id_from_object(obj: Mapping[str, Any]) -> str | None:
    customer = obj.get("customer")
    if isinstance(customer, Mapping):
        return customer.get("id")
    if customer:
        return str(customer)
    return None


def _period_from_object(obj: Mapping[str, Any]) -> tuple[datetime, datetime]:
    start = _from_unix_timestamp(obj.get("current_period_start"))
    end = _from_unix_timestamp(obj.get("current_period_end"))
    if start is not None and end is not None and end > start:
        return start, end

    subscription = obj.get("subscription")
    if isinstance(subscription, Mapping):
        start = _from_unix_timestamp(subscription.get("current_period_start"))
        end = _from_unix_timestamp(subscription.get("current_period_end"))
        if start is not None and end is not None and end > start:
            return start, end

    items = obj.get("items")
    if isinstance(items, Mapping):
        data = items.get("data") or []
        if data:
            start = _from_unix_timestamp(data[0].get("current_period_start"))
            end = _from_unix_timestamp(data[0].get("current_period_end"))
            if start is not None and end is not None and end > start:
                return start, end

    return current_calendar_month_window()


def _price_id_from_subscription(obj: Mapping[str, Any]) -> str | None:
    items = obj.get("items")
    if not isinstance(items, Mapping):
        return None
    data = items.get("data") or []
    if not data:
        return None
    price = data[0].get("price") or {}
    if isinstance(price, Mapping):
        return price.get("id")
    return None


class StripeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_checkout_session(
        self,
        *,
        tenant_id: uuid.UUID,
        success_url: str | None,
        cancel_url: str | None,
    ) -> dict[str, str]:
        if not settings.stripe_secret_key:
            raise StripeConfigurationError("STRIPE_SECRET_KEY is required for checkout sessions.")

        tenant = await self.session.get(Tenant, tenant_id)
        if tenant is None:
            raise TenantNotFoundError(str(tenant_id))

        plan = await self.session.get(Plan, "pro")
        if plan is None:
            raise StripeConfigurationError("The 'pro' plan must be seeded before checkout can run.")
        if not plan.stripe_price_id:
            raise StripeConfigurationError("STRIPE_PRO_PRICE_ID must be configured for the 'pro' plan.")

        stripe.api_key = settings.stripe_secret_key
        session_payload: dict[str, Any] = {
            "mode": "subscription",
            "line_items": [{"price": plan.stripe_price_id, "quantity": 1}],
            "success_url": success_url or settings.checkout_success_url,
            "cancel_url": cancel_url or settings.checkout_cancel_url,
            "client_reference_id": str(tenant_id),
            "metadata": {"tenant_id": str(tenant_id), "target_plan": "pro"},
            "subscription_data": {"metadata": {"tenant_id": str(tenant_id), "plan_id": "pro"}},
        }
        if tenant.stripe_customer_id:
            session_payload["customer"] = tenant.stripe_customer_id

        checkout_session = await to_thread.run_sync(
            lambda: stripe.checkout.Session.create(**session_payload)
        )
        return {
            "checkout_session_id": checkout_session["id"],
            "url": checkout_session["url"],
            "client_reference_id": str(tenant_id),
        }

    async def handle_webhook_event(self, event: Mapping[str, Any]) -> dict[str, str]:
        event_id = str(event.get("id") or "")
        event_type = str(event.get("type") or "")
        if not event_id or not event_type:
            return {"status": "ignored", "reason": "missing_event_id_or_type"}

        try:
            async with self.session.begin():
                existing = await self.session.get(ProcessedWebhookEvent, event_id)
                if existing is not None:
                    return {"status": "already_processed"}

                self.session.add(ProcessedWebhookEvent(event_id=event_id))
                obj = event.get("data", {}).get("object", {})
                if not isinstance(obj, Mapping):
                    return {"status": "ignored", "event_type": event_type}

                handled = await self._apply_event(event_type, obj)
        except IntegrityError:
            await self.session.rollback()
            return {"status": "already_processed"}

        return {"status": "processed" if handled else "ignored", "event_type": event_type}

    async def _apply_event(self, event_type: str, obj: Mapping[str, Any]) -> bool:
        if event_type == "checkout.session.completed":
            await self._handle_checkout_completed(obj)
            return True
        if event_type == "customer.subscription.updated":
            await self._handle_subscription_updated(obj)
            return True
        if event_type == "customer.subscription.deleted":
            await self._handle_subscription_deleted(obj)
            return True
        return False

    async def _handle_checkout_completed(self, obj: Mapping[str, Any]) -> None:
        metadata = obj.get("metadata") or {}
        tenant_id_value = obj.get("client_reference_id") or metadata.get("tenant_id")
        tenant = await self._tenant_from_id_value(tenant_id_value)
        customer_id = _customer_id_from_object(obj)
        if tenant is None and customer_id:
            tenant = await self._tenant_by_customer(customer_id)
        if tenant is None:
            return
        if customer_id:
            tenant.stripe_customer_id = customer_id

        plan = await self.session.get(Plan, _normalize_plan_id(metadata.get("target_plan")))
        if plan is None:
            plan = await self.session.get(Plan, "pro")
        if plan is None:
            return

        period_start, period_end = _period_from_object(obj)
        await self._upsert_tenant_subscription(
            tenant=tenant,
            plan=plan,
            stripe_subscription_id=_subscription_id_from_object(obj),
            status="active",
            period_start=period_start,
            period_end=period_end,
        )

    async def _handle_subscription_updated(self, obj: Mapping[str, Any]) -> None:
        stripe_subscription_id = _subscription_id_from_object(obj)
        subscription = await self._subscription_by_stripe_id(stripe_subscription_id)
        tenant = subscription.tenant if subscription is not None else None

        metadata = obj.get("metadata") or {}
        if tenant is None:
            tenant = await self._tenant_from_id_value(metadata.get("tenant_id"))
        customer_id = _customer_id_from_object(obj)
        if tenant is None and customer_id:
            tenant = await self._tenant_by_customer(customer_id)
        if tenant is None:
            return
        if customer_id:
            tenant.stripe_customer_id = customer_id

        plan = await self._plan_from_subscription_object(obj)
        if plan is None and subscription is not None:
            plan = subscription.plan
        if plan is None:
            plan = await self.session.get(Plan, _normalize_plan_id(metadata.get("plan_id")))
        if plan is None:
            return

        period_start, period_end = _period_from_object(obj)
        await self._upsert_tenant_subscription(
            tenant=tenant,
            plan=plan,
            stripe_subscription_id=stripe_subscription_id,
            status=_normalize_subscription_status(obj.get("status")),
            period_start=period_start,
            period_end=period_end,
        )

    async def _handle_subscription_deleted(self, obj: Mapping[str, Any]) -> None:
        stripe_subscription_id = _subscription_id_from_object(obj)
        subscription = await self._subscription_by_stripe_id(stripe_subscription_id)
        tenant = subscription.tenant if subscription is not None else None

        metadata = obj.get("metadata") or {}
        if tenant is None:
            tenant = await self._tenant_from_id_value(metadata.get("tenant_id"))
        customer_id = _customer_id_from_object(obj)
        if tenant is None and customer_id:
            tenant = await self._tenant_by_customer(customer_id)
        if tenant is None:
            return

        plan = await self.session.get(Plan, "free")
        if plan is None:
            return

        period_start, period_end = current_calendar_month_window()
        await self._upsert_tenant_subscription(
            tenant=tenant,
            plan=plan,
            stripe_subscription_id=None,
            status="active",
            period_start=period_start,
            period_end=period_end,
        )

    async def _upsert_tenant_subscription(
        self,
        *,
        tenant: Tenant,
        plan: Plan,
        stripe_subscription_id: str | None,
        status: str,
        period_start: datetime,
        period_end: datetime,
    ) -> None:
        subscription = await self._subscription_by_tenant(tenant.id)
        if subscription is None and stripe_subscription_id:
            subscription = await self._subscription_by_stripe_id(stripe_subscription_id)

        if subscription is None:
            subscription = Subscription(
                tenant_id=tenant.id,
                plan_id=plan.id,
                stripe_subscription_id=stripe_subscription_id,
                status=status,
                current_period_start=period_start,
                current_period_end=period_end,
            )
            self.session.add(subscription)
            return

        subscription.plan_id = plan.id
        subscription.stripe_subscription_id = stripe_subscription_id
        subscription.status = status
        subscription.current_period_start = period_start
        subscription.current_period_end = period_end

    async def _subscription_by_tenant(self, tenant_id: uuid.UUID) -> Subscription | None:
        result = await self.session.execute(
            select(Subscription)
            .options(selectinload(Subscription.plan), selectinload(Subscription.tenant))
            .where(Subscription.tenant_id == tenant_id)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _subscription_by_stripe_id(self, stripe_subscription_id: str | None) -> Subscription | None:
        if not stripe_subscription_id:
            return None
        result = await self.session.execute(
            select(Subscription)
            .options(selectinload(Subscription.plan), selectinload(Subscription.tenant))
            .where(Subscription.stripe_subscription_id == stripe_subscription_id)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _tenant_by_customer(self, stripe_customer_id: str) -> Tenant | None:
        result = await self.session.execute(
            select(Tenant).where(Tenant.stripe_customer_id == stripe_customer_id).limit(1)
        )
        return result.scalar_one_or_none()

    async def _tenant_from_id_value(self, tenant_id_value: Any) -> Tenant | None:
        if not tenant_id_value:
            return None
        try:
            tenant_id = uuid.UUID(str(tenant_id_value))
        except ValueError:
            return None
        return await self.session.get(Tenant, tenant_id)

    async def _plan_from_subscription_object(self, obj: Mapping[str, Any]) -> Plan | None:
        price_id = _price_id_from_subscription(obj)
        if price_id:
            result = await self.session.execute(
                select(Plan).where(Plan.stripe_price_id == price_id).limit(1)
            )
            plan = result.scalar_one_or_none()
            if plan is not None:
                return plan

        metadata = obj.get("metadata") or {}
        plan_id = metadata.get("plan_id") or metadata.get("target_plan")
        if plan_id:
            return await self.session.get(Plan, _normalize_plan_id(plan_id))
        return None
