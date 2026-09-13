from __future__ import annotations

from pydantic import BaseModel, Field


class SimulatedTokens(BaseModel):
    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)

    @property
    def total_billable_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cached_input_tokens
            + self.output_tokens
            + self.reasoning_tokens
        )


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=16_000)
    simulated_tokens: SimulatedTokens


class CheckoutSessionRequest(BaseModel):
    success_url: str | None = Field(default=None, min_length=1)
    cancel_url: str | None = Field(default=None, min_length=1)
