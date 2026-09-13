from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ServiceError(Exception):
    status_code = 400

    def __init__(self, message: str) -> None:
        self.message = message
        self.headers: dict[str, str] = {}
        super().__init__(message)

    def payload(self) -> dict[str, Any]:
        return {"error": {"code": "bad_request", "message": self.message}}


class TenantNotFoundError(ServiceError):
    status_code = 404

    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id
        super().__init__(f"Tenant '{tenant_id}' was not found.")

    def payload(self) -> dict[str, Any]:
        return {
            "error": {
                "code": "tenant_not_found",
                "message": self.message,
                "tenant_id": self.tenant_id,
            }
        }


class PaymentRequiredError(ServiceError):
    status_code = 402

    def __init__(self, message: str = "Tenant has no active subscription for the current period.") -> None:
        super().__init__(message)

    def payload(self) -> dict[str, Any]:
        return {"error": {"code": "payment_required", "message": self.message}}


class IdempotencyConflictError(ServiceError):
    status_code = 409

    def payload(self) -> dict[str, Any]:
        return {"error": {"code": "idempotency_conflict", "message": self.message}}


class StripeConfigurationError(ServiceError):
    status_code = 503

    def payload(self) -> dict[str, Any]:
        return {"error": {"code": "stripe_configuration_error", "message": self.message}}


@dataclass
class QuotaExceededError(ServiceError):
    metric: str
    current_usage: int
    requested: int
    limit: int
    retry_after_seconds: int
    quota: dict[str, Any] = field(default_factory=dict)

    status_code = 429

    def __post_init__(self) -> None:
        ServiceError.__init__(
            self,
            f"{self.metric} quota would be exceeded for the current billing period.",
        )
        self.headers = {"Retry-After": str(self.retry_after_seconds)}

    def payload(self) -> dict[str, Any]:
        return {
            "error": {
                "code": "quota_exceeded",
                "message": self.message,
                "metric": self.metric,
                "current_usage": self.current_usage,
                "requested": self.requested,
                "would_be": self.current_usage + self.requested,
                "limit": self.limit,
                "retry_after_seconds": self.retry_after_seconds,
            },
            "quota": self.quota,
        }
