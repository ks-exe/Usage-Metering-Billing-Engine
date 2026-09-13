from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import tenant_id_header
from app.database import get_session
from app.schemas import CheckoutSessionRequest
from app.services.stripe_service import StripeService


router = APIRouter(prefix="/billing", tags=["billing"])


@router.post("/checkout-session", status_code=status.HTTP_201_CREATED)
async def create_checkout_session(
    payload: CheckoutSessionRequest = Body(default_factory=CheckoutSessionRequest),
    tenant_id: uuid.UUID = Depends(tenant_id_header),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    return await StripeService(session).create_checkout_session(
        tenant_id=tenant_id,
        success_url=payload.success_url,
        cancel_url=payload.cancel_url,
    )
