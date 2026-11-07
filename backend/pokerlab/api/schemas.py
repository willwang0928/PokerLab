"""Request/response models for the HTTP API."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field, StrictInt, model_validator

from ..ai.difficulty import DEFAULT_DIFFICULTY, Difficulty
from ..engine import ActionType


class CreateSessionRequest(BaseModel):
    starting_stack: int = Field(1000, ge=20, le=1_000_000)
    small_blind: int = Field(5, ge=1, le=100_000)
    big_blind: int = Field(10, ge=1, le=200_000)
    player_name: str = Field("You", min_length=1, max_length=24)
    difficulty: Difficulty = DEFAULT_DIFFICULTY
    # Only honoured when the server allows seeded games (tests and debugging); a known
    # seed would let a client work out the opponent's cards.
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
    # Strict, so JSON like true or "30" is rejected instead of coerced.
    amount: Annotated[StrictInt, Field(ge=0)] | None = None


class StepModel(BaseModel):
    event: dict[str, Any]
    state: dict[str, Any]


class PlayResponse(BaseModel):
    state: dict[str, Any]
    steps: list[StepModel]


MAX_EQUITY_PLAYERS = 6
MAX_EQUITY_EVALUATIONS = 300_000  # iterations x players; keeps one request around a second


class EquityRequest(BaseModel):
    """All-in equity query. Each player is a hand (``"AhKh"``) or a range (``"QQ+,AKs"``)."""

    players: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        min_length=2, max_length=MAX_EQUITY_PLAYERS
    )
    board: str = Field("", max_length=20)
    dead: str = Field("", max_length=60)
    iterations: StrictInt = Field(20_000, ge=100, le=100_000)
    seed: StrictInt | None = Field(None, ge=0, lt=2**63)

    @model_validator(mode="after")
    def _check_work(self) -> EquityRequest:
        if self.iterations * len(self.players) > MAX_EQUITY_EVALUATIONS:
            limit = MAX_EQUITY_EVALUATIONS // len(self.players)
            raise ValueError(f"iterations must be at most {limit} for {len(self.players)} players")
        return self


class PlayerEquityModel(BaseModel):
    equity: float
    win: float
    tie: float
    std_error: float


class EquityResponse(BaseModel):
    method: str
    trials: int
    seed: int | None
    players: list[PlayerEquityModel]
