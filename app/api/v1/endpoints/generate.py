from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import idempotency_key_header, tenant_id_header
from app.database import get_session
from app.schemas import GenerateRequest
from app.services.metering import MeteringService


router = APIRouter(tags=["metering"])


@router.post("/generate")
async def generate(
    payload: GenerateRequest,
    tenant_id: uuid.UUID = Depends(tenant_id_header),
    idempotency_key: str = Depends(idempotency_key_header),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    result = await MeteringService(session).generate(
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        request=payload,
    )
    return JSONResponse(
        status_code=result.status_code,
        content=result.body,
        headers={"Idempotency-Replayed": "true" if result.replayed else "false"},
    )
