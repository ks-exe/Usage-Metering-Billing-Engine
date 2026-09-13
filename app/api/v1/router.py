from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import billing, generate, usage, webhooks


router = APIRouter()
router.include_router(generate.router)
router.include_router(usage.router)
router.include_router(billing.router)
router.include_router(webhooks.router)
