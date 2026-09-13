from __future__ import annotations

import json
from typing import Annotated

import stripe
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.services.stripe_service import StripeService


router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: Annotated[str | None, Header(alias="stripe-signature")] = None,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    if not settings.stripe_webhook_secret:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "stripe_configuration_error",
                    "message": "STRIPE_WEBHOOK_SECRET is required.",
                }
            },
        )
    if not stripe_signature:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "invalid_stripe_signature", "message": "Invalid Stripe signature."}},
        )

    payload = await request.body()
    try:
        stripe.Webhook.construct_event(
            payload=payload,
            sig_header=stripe_signature,
            secret=settings.stripe_webhook_secret,
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "invalid_stripe_signature", "message": "Invalid Stripe signature."}},
        )

    event = json.loads(payload.decode("utf-8"))
    result = await StripeService(session).handle_webhook_event(event)
    return JSONResponse(status_code=200, content=result)
