from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from app.config import Settings, settings


MICRO_USD_PER_USD = Decimal("1000000")
CENTS_PER_USD = Decimal("100")


@dataclass(frozen=True)
class CostBreakdown:
    api_call_micro_usd: int
    fresh_input_micro_usd: int
    cached_input_micro_usd: int
    output_micro_usd: int
    reasoning_micro_usd: int

    @property
    def token_micro_usd(self) -> int:
        return (
            self.fresh_input_micro_usd
            + self.cached_input_micro_usd
            + self.output_micro_usd
            + self.reasoning_micro_usd
        )

    @property
    def total_micro_usd(self) -> int:
        return self.api_call_micro_usd + self.token_micro_usd

    @property
    def total_cents(self) -> int:
        return micro_usd_to_cents(self.total_micro_usd)

    def as_dict(self) -> dict[str, int | str]:
        return {
            "currency": "usd",
            "api_call_micro_usd": self.api_call_micro_usd,
            "fresh_input_micro_usd": self.fresh_input_micro_usd,
            "cached_input_micro_usd": self.cached_input_micro_usd,
            "output_micro_usd": self.output_micro_usd,
            "reasoning_micro_usd": self.reasoning_micro_usd,
            "token_micro_usd": self.token_micro_usd,
            "total_micro_usd": self.total_micro_usd,
            "total_cents": self.total_cents,
        }


def _to_micro_usd(dollars: Decimal) -> int:
    return int((dollars * MICRO_USD_PER_USD).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _token_cost_micro_usd(tokens: int, price_per_million_usd: Decimal) -> int:
    if tokens <= 0:
        return 0
    dollars = Decimal(tokens) * price_per_million_usd / Decimal("1000000")
    return _to_micro_usd(dollars)


def micro_usd_to_cents(micro_usd: int) -> int:
    cents = Decimal(micro_usd) * CENTS_PER_USD / MICRO_USD_PER_USD
    return int(cents.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def calculate_usage_cost(
    *,
    api_calls: int = 0,
    input_tokens: int = 0,
    cached_input_tokens: int = 0,
    output_tokens: int = 0,
    reasoning_tokens: int = 0,
    config: Settings = settings,
) -> CostBreakdown:
    return CostBreakdown(
        api_call_micro_usd=_to_micro_usd(Decimal(api_calls) * config.api_call_price_usd),
        fresh_input_micro_usd=_token_cost_micro_usd(
            input_tokens,
            config.fresh_input_price_per_million_usd,
        ),
        cached_input_micro_usd=_token_cost_micro_usd(
            cached_input_tokens,
            config.cached_input_price_per_million_usd,
        ),
        output_micro_usd=_token_cost_micro_usd(
            output_tokens,
            config.output_price_per_million_usd,
        ),
        reasoning_micro_usd=_token_cost_micro_usd(
            reasoning_tokens,
            config.reasoning_price_per_million_usd,
        ),
    )


def pricing_table(config: Settings = settings) -> dict[str, str]:
    return {
        "fresh_input_per_million_usd": str(config.fresh_input_price_per_million_usd),
        "cached_input_per_million_usd": str(config.cached_input_price_per_million_usd),
        "output_per_million_usd": str(config.output_price_per_million_usd),
        "reasoning_per_million_usd": str(config.reasoning_price_per_million_usd),
        "api_call_usd": str(config.api_call_price_usd),
    }
