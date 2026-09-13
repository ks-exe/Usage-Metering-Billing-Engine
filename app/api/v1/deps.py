from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Header


async def tenant_id_header(
    x_tenant_id: Annotated[uuid.UUID, Header(alias="X-Tenant-ID")],
) -> uuid.UUID:
    return x_tenant_id


async def idempotency_key_header(
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
) -> str:
    return idempotency_key
