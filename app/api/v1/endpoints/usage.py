from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import tenant_id_header
from app.database import get_session
from app.services.metering import MeteringService


router = APIRouter(tags=["usage"])


@router.get("/usage")
async def usage(
    tenant_id: uuid.UUID = Depends(tenant_id_header),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await MeteringService(session).current_usage(tenant_id)
