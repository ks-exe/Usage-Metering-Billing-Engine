# Evidence

Use this file to paste final command output during acceptance.

## 1. Startup

```bash
docker compose up --build
```

Expected:

```text
postgres healthy
alembic upgrade head completed
Seeded plans and demo tenants.
Uvicorn running on http://0.0.0.0:8000
```

## 2. Idempotency Replay

```bash
curl -i -X POST http://localhost:8000/api/v1/generate \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  -H "Idempotency-Key: evidence-replay-001" \
  -d '{"prompt":"replay check","simulated_tokens":{"input_tokens":500,"cached_input_tokens":200,"output_tokens":300,"reasoning_tokens":150}}'
```

Run the same command twice. The second response should include:

```text
Idempotency-Replayed: true
```

The database should still contain exactly two usage rows for that idempotency key: one `api_call` and one `ai_tokens`.

## 3. Quota Exceeded

```bash
curl -i -X POST http://localhost:8000/api/v1/generate \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111" \
  -H "Idempotency-Key: evidence-quota-001" \
  -d '{"prompt":"quota","simulated_tokens":{"input_tokens":100001,"cached_input_tokens":0,"output_tokens":0,"reasoning_tokens":0}}'
```

Expected:

```text
HTTP/1.1 429 Too Many Requests
Retry-After: <seconds-until-period-reset>
```

## 4. Usage Rollup

```bash
curl -i http://localhost:8000/api/v1/usage \
  -H "X-Tenant-ID: 11111111-1111-1111-1111-111111111111"
```

Expected response includes current plan, status, reset date, API calls used/limit, categorized token totals, and integer micro-USD/cents cost fields.

## 5. Stripe Webhook Validation

```bash
stripe listen --forward-to localhost:8000/api/v1/webhooks/stripe
```

Expected:

```text
Invalid signatures return HTTP 400.
Duplicate event IDs return {"status":"already_processed"}.
checkout.session.completed upgrades the tenant subscription to pro.
customer.subscription.deleted reverts the tenant subscription to free.
```

## 6. Automated Tests

```bash
pytest
```
