"""Request/response models for the HTTP API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from ..engine import ActionType


class CreateSessionRequest(BaseModel):
    starting_stack: int = Field(1000, ge=20, le=1_000_000)
    small_blind: int = Field(5, ge=1, le=100_000)
    big_blind: int = Field(10, ge=1, le=200_000)
    player_name: str = Field("You", min_length=1, max_length=24)
    seed: int | None = None

    @model_validator(mode="after")
    def _check_blinds(self) -> CreateSessionRequest:
        if self.small_blind > self.big_blind:
            raise ValueError("small_blind cannot exceed big_blind")
        if self.starting_stack < 2 * self.big_blind:
            raise ValueError("starting_stack must be at least two big blinds")
        return self


class ActionRequest(BaseModel):
    action: ActionType
    amount: int | None = Field(None, ge=0)


class StepModel(BaseModel):
    event: dict[str, Any]
    state: dict[str, Any]


class PlayResponse(BaseModel):
    state: dict[str, Any]
    steps: list[StepModel]
