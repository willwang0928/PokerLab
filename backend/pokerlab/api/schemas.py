"""Request/response models for the HTTP API."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StrictInt, model_validator

from ..ai.difficulty import DEFAULT_DIFFICULTY, Difficulty
from ..engine import ActionType

TrainingDifficulty = Literal["beginner", "intermediate", "advanced"]
TrainingConcept = Literal[
    "pot_odds", "draws", "value_betting", "bluffing", "bet_sizing", "preflop", "reads"
]


class CreateSessionRequest(BaseModel):
    starting_stack: int = Field(1000, ge=20, le=1_000_000)
    small_blind: int = Field(5, ge=1, le=100_000)
    big_blind: int = Field(10, ge=1, le=200_000)
    player_name: str = Field("You", min_length=1, max_length=24)
    difficulty: Difficulty = DEFAULT_DIFFICULTY
    adaptive: bool = True
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


class PracticeRequest(BaseModel):
    """A decision point to practise: public betting plus the player's own cards."""

    spot: dict[str, Any]
    difficulty: TrainingDifficulty = "intermediate"
    # The option the player chose at the table, so practice includes that exact size.
    chosen: str | None = Field(None, max_length=40)


class AnswerRequest(BaseModel):
    id: str = Field(min_length=1, max_length=4000)
    choice: str = Field(min_length=1, max_length=40)


# ------------------------------------------------------------------ progress storage

GradeName = Literal["good", "close", "questionable", "mistake", "ungraded"]
TagName = Literal[
    "called_without_odds",
    "folded_with_odds",
    "missed_value",
    "overaggressive",
    "bet_sizing",
    "preflop_loose",
    "preflop_tight",
]
# Scenario ids, practice-spot ids (base64url) and "<session>#<hand>:<n>" decision ids.
RecordId = Annotated[str, Field(min_length=1, max_length=1000, pattern=r"^[A-Za-z0-9._:#|=-]+$")]
# Milliseconds since the epoch: 2001-09-09 to 2096-10-02. Rejects seconds and garbage.
Timestamp = Annotated[float, Field(ge=1e12, le=4e12, allow_inf_nan=False)]
Unit = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class _Record(BaseModel):
    model_config = {"extra": "forbid"}


class AnswerRecord(_Record):
    """One first answer to a training scenario."""

    id: RecordId
    t: Timestamp
    concept: TrainingConcept
    difficulty: TrainingDifficulty
    grade: GradeName
    review: bool | None = None


class DecisionRecord(_Record):
    """One graded decision from a post-hand review."""

    id: RecordId
    t: Timestamp
    hand: Annotated[str, Field(min_length=1, max_length=200)]
    grade: GradeName
    concept: TrainingConcept
    tags: Annotated[list[TagName], Field(max_length=10)]
    evLossBb: Annotated[float, Field(ge=0, le=10_000, allow_inf_nan=False)]  # noqa: N815
    confidence: Literal["low", "medium", "high"] | None = None
    equity: Unit | None = None
    required: Unit | None = None


MAX_UPLOAD = 2000  # records of each kind per request


class RecordUpload(BaseModel):
    """Records to add. Each is validated on its own, so one bad record cannot block the rest."""

    answers: Annotated[list[Any], Field(max_length=MAX_UPLOAD)] = []
    decisions: Annotated[list[Any], Field(max_length=MAX_UPLOAD)] = []


class HandUpload(BaseModel):
    """Hand-history records to add (v0.4.3); each is validated and replayed on its own."""

    hands: Annotated[list[Any], Field(max_length=200)] = []


class ProfileCreate(BaseModel):
    name: Annotated[str, Field(max_length=40)] = "Player"


class ProfileUpdate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=40)]
