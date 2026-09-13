from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("stripe_customer_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
    )
    op.create_index(op.f("ix_tenants_stripe_customer_id"), "tenants", ["stripe_customer_id"], unique=False)

    op.create_table(
        "plans",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("api_calls_quota", sa.Integer(), nullable=False),
        sa.Column("token_quota", sa.Integer(), nullable=False),
        sa.Column("stripe_price_id", sa.String(length=255), nullable=True),
        sa.CheckConstraint("api_calls_quota >= 0", name=op.f("ck_plans_plans_api_calls_quota_nonnegative")),
        sa.CheckConstraint("token_quota >= 0", name=op.f("ck_plans_plans_token_quota_nonnegative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plans")),
    )
    op.create_index(op.f("ix_plans_stripe_price_id"), "plans", ["stripe_price_id"], unique=False)

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("stripe_subscription_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("current_period_end > current_period_start", name=op.f("ck_subscriptions_subscriptions_period_order")),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], name=op.f("fk_subscriptions_plan_id_plans"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_subscriptions_tenant_id_tenants"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscriptions")),
    )
    op.create_index(op.f("ix_subscriptions_plan_id"), "subscriptions", ["plan_id"], unique=False)
    op.create_index(op.f("ix_subscriptions_status"), "subscriptions", ["status"], unique=False)
    op.create_index(
        op.f("ix_subscriptions_stripe_subscription_id"),
        "subscriptions",
        ["stripe_subscription_id"],
        unique=True,
    )
    op.create_index(op.f("ix_subscriptions_tenant_id"), "subscriptions", ["tenant_id"], unique=True)

    op.create_table(
        "usage_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint("event_type IN ('api_call', 'ai_tokens')", name=op.f("ck_usage_events_usage_events_event_type")),
        sa.CheckConstraint("quantity >= 0", name=op.f("ck_usage_events_usage_events_quantity_nonnegative")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_usage_events_tenant_id_tenants"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_events")),
        sa.UniqueConstraint("tenant_id", "idempotency_key", "event_type", name="uq_usage_events_tenant_idempotency_event_type"),
    )
    op.create_index(op.f("ix_usage_events_created_at"), "usage_events", ["created_at"], unique=False)
    op.create_index(op.f("ix_usage_events_tenant_id"), "usage_events", ["tenant_id"], unique=False)
    op.create_index("ix_usage_events_tenant_created_at", "usage_events", ["tenant_id", "created_at"], unique=False)
    op.create_index("ix_usage_events_tenant_idempotency_key", "usage_events", ["tenant_id", "idempotency_key"], unique=False)

    op.create_table(
        "processed_webhook_events",
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_processed_webhook_events")),
    )

    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("endpoint", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_idempotency_records_tenant_id_tenants"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_records")),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_idempotency_records_tenant_key"),
    )
    op.create_index(op.f("ix_idempotency_records_tenant_id"), "idempotency_records", ["tenant_id"], unique=False)
    op.create_index("ix_idempotency_records_tenant_endpoint", "idempotency_records", ["tenant_id", "endpoint"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_idempotency_records_tenant_endpoint", table_name="idempotency_records")
    op.drop_index(op.f("ix_idempotency_records_tenant_id"), table_name="idempotency_records")
    op.drop_table("idempotency_records")
    op.drop_table("processed_webhook_events")
    op.drop_index("ix_usage_events_tenant_idempotency_key", table_name="usage_events")
    op.drop_index("ix_usage_events_tenant_created_at", table_name="usage_events")
    op.drop_index(op.f("ix_usage_events_tenant_id"), table_name="usage_events")
    op.drop_index(op.f("ix_usage_events_created_at"), table_name="usage_events")
    op.drop_table("usage_events")
    op.drop_index(op.f("ix_subscriptions_tenant_id"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_stripe_subscription_id"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_status"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_plan_id"), table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_index(op.f("ix_plans_stripe_price_id"), table_name="plans")
    op.drop_table("plans")
    op.drop_index(op.f("ix_tenants_stripe_customer_id"), table_name="tenants")
    op.drop_table("tenants")
