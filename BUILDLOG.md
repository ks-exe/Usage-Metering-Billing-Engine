# Build Log

## Implementation Summary

- Built a FastAPI application with `app/api/v1` route modules, service-layer business logic, Pydantic schemas, and SQLAlchemy 2.0 async persistence.
- Added PostgreSQL schema and Alembic migration for `tenants`, `plans`, `subscriptions`, `usage_events`, `processed_webhook_events`, plus `idempotency_records` for persisted response replay.
- Implemented `POST /api/v1/generate` with tenant UUID headers, idempotency keys, active subscription enforcement, quota boundary checks, `Retry-After` on 429 responses, and atomic two-event metering.
- Implemented `GET /api/v1/usage` with current-period API call totals, categorized token rollups, plan status, reset date, and Decimal-backed integer micro-USD/cents cost output.
- Implemented `POST /api/v1/billing/checkout-session` using Stripe Test Mode checkout sessions with `client_reference_id` and tenant metadata.
- Implemented `POST /api/v1/webhooks/stripe` with raw-body signature verification, event ID deduplication, checkout completion upgrades, subscription updates, and subscription deletion fallback to the Free plan.
- Added Dockerfile and Docker Compose orchestration for local Postgres plus API startup, migration, and seed flow.
- Added pytest coverage for idempotency, duplicate suppression, quota rejection, usage rollups, pricing arithmetic, and Stripe webhook validation.

## Design Notes

- Money is never represented with floats. Pricing uses `Decimal`, and API responses expose integer micro-USD and cents.
- `usage_events.metadata` stores the token category breakdown so the required table remains compact while `GET /api/v1/usage` can itemize input, cached input, output, and reasoning tokens.
- `usage_events` uses a uniqueness constraint on `(tenant_id, idempotency_key, event_type)` so each successful generation can store the required two rows while still preventing duplicate rows for either event type.
- Stripe webhook deduplication is independent from business logic through `processed_webhook_events`, keyed by the Stripe event ID.
