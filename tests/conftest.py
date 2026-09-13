from __future__ import annotations

import os
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test_secret"
os.environ["STRIPE_PRO_PRICE_ID"] = "price_test_pro"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.database import Base, get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Plan, Subscription, Tenant  # noqa: E402
from app.services.metering import current_calendar_month_window  # noqa: E402


TEST_TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


test_engine = create_async_engine(
    os.environ["DATABASE_URL"],
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = async_sessionmaker(bind=test_engine, expire_on_commit=False, autoflush=False)


async def override_get_session() -> AsyncIterator[AsyncSession]:
    async with TestingSessionLocal() as session:
        yield session


app.dependency_overrides[get_session] = override_get_session


@pytest_asyncio.fixture(autouse=True)
async def reset_database() -> AsyncIterator[None]:
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    period_start, period_end = current_calendar_month_window()
    async with TestingSessionLocal() as session:
        async with session.begin():
            session.add_all(
                [
                    Plan(
                        id="free",
                        name="Free",
                        api_calls_quota=1_000,
                        token_quota=100_000,
                        stripe_price_id=None,
                    ),
                    Plan(
                        id="pro",
                        name="Pro",
                        api_calls_quota=50_000,
                        token_quota=5_000_000,
                        stripe_price_id="price_test_pro",
                    ),
                    Tenant(
                        id=TEST_TENANT_ID,
                        name="Test Tenant",
                        stripe_customer_id="cus_test_123",
                    ),
                    Subscription(
                        tenant_id=TEST_TENANT_ID,
                        plan_id="free",
                        stripe_subscription_id=None,
                        status="active",
                        current_period_start=period_start,
                        current_period_end=period_end,
                    ),
                ]
            )

    yield


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client
