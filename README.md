# Usage Metering & Billing Engine

Production-ready FastAPI capstone for multi-tenant usage metering, quota enforcement, idempotent billable actions, current-period usage rollups, and Stripe Test Mode subscription webhooks.

## Stack

- Python 3.11, FastAPI, Pydantic v2
- PostgreSQL 16 via Docker Compose
- SQLAlchemy 2.0 async ORM and Alembic migrations
- Stripe Python SDK in test mode
- Monetary math uses `decimal.Decimal`; persisted cost values are integer micro-USD/cents only

## Project Layout

```text
.
├── app/
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── models/
│   ├── schemas/
│   ├── api/v1/endpoints/
│   └── services/
├── migrations/
├── scripts/seed.py
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── capstone.yaml
├── EVIDENCE.md
└── BUILDLOG.md
```

## Run Locally

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -r requirements.txt
copy .env.example .env
docker compose up -d postgres
alembic upgrade head
python -m scripts.seed
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Or run the API and database together:

```bash
docker compose up --build
```

Seeded tenants:

- Free: `11111111-1111-1111-1111-111111111111`
- Pro: `22222222-2222-2222-2222-222222222222`

## Generate

```bash
curl -i -X POST http://localhost:8000/api/v1/generate \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  -H "Idempotency-Key: demo-001" \
  -d '{"prompt":"Generate a summary","simulated_tokens":{"input_tokens":500,"cached_input_tokens":200,"output_tokens":300,"reasoning_tokens":150}}'
```

Successful requests atomically write one `api_call` event and one `ai_tokens` event. Reusing the same tenant/idempotency key with the same payload returns the cached response and does not duplicate usage.

## Usage

```bash
curl -i http://localhost:8000/api/v1/usage \
  -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111"
```

The response includes plan status, reset date, API call usage, categorized token totals, and itemized cost in integer micro-USD and cents.

## Stripe Test Mode

Set these values in `.env` when testing Stripe:

```text
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRO_PRICE_ID=price_...
```

Create an upgrade checkout session:

```bash
curl -i -X POST http://localhost:8000/api/v1/billing/checkout-session \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  -d '{"success_url":"http://localhost:8000/billing/success","cancel_url":"http://localhost:8000/billing/cancel"}'
```

Forward webhooks locally:

```bash
stripe listen --forward-to localhost:8000/api/v1/webhooks/stripe
```

Handled events:

- `checkout.session.completed`
- `customer.subscription.updated`
- `customer.subscription.deleted`

Webhook signatures are verified from the raw body. Stripe event IDs are deduplicated in `processed_webhook_events`.

## Tests

```bash
pytest
```
