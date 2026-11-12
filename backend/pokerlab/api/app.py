"""FastAPI application exposing heads-up Hold'em sessions."""

from __future__ import annotations

import logging
import os
import random
import sqlite3
import threading

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import TypeAdapter, ValidationError

from .. import __version__
from ..coach import training
from ..coach.patterns import find_weaknesses
from ..coach.spot import Spot, SpotError
from ..engine import Action, IllegalActionError
from ..equity import calculate_equity
from ..history import HistoryError, replay, summarize, validate_record
from ..session import HeadsUpSession, SessionConfig, SessionStateError
from ..storage import (
    Profile,
    ProfileNotFound,
    ProgressStore,
    QuotaExceeded,
    StorageUnavailable,
    default_path,
)
from .schemas import (
    ActionRequest,
    AnswerRecord,
    AnswerRequest,
    CreateSessionRequest,
    DecisionRecord,
    EquityRequest,
    EquityResponse,
    HandUpload,
    PlayResponse,
    PracticeRequest,
    ProfileCreate,
    ProfileUpdate,
    RecordUpload,
    TrainingConcept,
    TrainingDifficulty,
)
from .store import SessionStore

log = logging.getLogger(__name__)

_VALIDATORS = {"answers": TypeAdapter(AnswerRecord), "decisions": TypeAdapter(DecisionRecord)}


def create_app(
    store: SessionStore | None = None,
    allow_seed: bool | None = None,
    progress: ProgressStore | None = None,
) -> FastAPI:
    """Build the API.

    ``allow_seed`` (default: ``POKERLAB_ALLOW_SEED``) permits seeded games. ``progress`` is
    the progress database; by default it opens ``POKERLAB_DB`` (or
    ``POKERLAB_DATA_DIR/pokerlab.sqlite3``) the first time it is needed.
    """
    app = FastAPI(title="PokerLab API", version=__version__)
    # Not ``store or ...``: an empty store is falsy because it defines __len__.
    sessions = store if store is not None else SessionStore()
    if allow_seed is None:
        allow_seed = os.getenv("POKERLAB_ALLOW_SEED", "").lower() in ("1", "true", "yes")
    app.state.sessions = sessions
    progress_lock = threading.Lock()

    def _progress() -> ProgressStore:
        nonlocal progress
        with progress_lock:
            if progress is None:
                progress = ProgressStore(default_path())
            app.state.progress = progress
            return progress

    @app.exception_handler(sqlite3.Error)
    @app.exception_handler(StorageUnavailable)
    async def _storage_failed(_: Request, exc: Exception) -> JSONResponse:
        # A database that fails after startup (disk full, I/O error, locked for too long)
        # is reported like storage that never opened: 503, which clients retry later.
        log.error("Progress storage error: %s: %s", type(exc).__name__, exc)
        return JSONResponse(status_code=503, content={"detail": "Progress storage is unavailable"})

    origins = os.getenv("POKERLAB_CORS_ORIGINS", "http://localhost:5173").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in origins if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _get(session_id: str) -> HeadsUpSession:
        session = sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return session

    @app.get("/api/health")
    def health() -> dict[str, str]:
        storage = "ok" if _progress().available else "unavailable"
        return {"status": "ok", "version": __version__, "storage": storage}

    # ------------------------------------------------------------------ progress

    def _authed(authorization: str | None) -> tuple[ProgressStore, Profile]:
        db = _progress()
        if not db.available:
            raise HTTPException(status_code=503, detail="Progress storage is unavailable")
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise HTTPException(status_code=401, detail="A profile key is required")
        try:
            return db, db.profile(token.strip())
        except ProfileNotFound as exc:
            raise HTTPException(status_code=401, detail="Unknown profile key") from exc
        except StorageUnavailable as exc:
            raise HTTPException(status_code=503, detail="Progress storage is unavailable") from exc

    @app.post("/api/profiles", status_code=201)
    def create_profile(req: ProfileCreate) -> dict:
        db = _progress()
        if not db.available:
            raise HTTPException(status_code=503, detail="Progress storage is unavailable")
        key, profile = db.create_profile(req.name.strip() or "Player")
        return {"key": key, "profile": profile.view({"answers": 0, "decisions": 0})}

    @app.get("/api/profile")
    def get_profile(authorization: str | None = Header(None)) -> dict:
        db, profile = _authed(authorization)
        return profile.view(db.counts(profile))

    @app.patch("/api/profile")
    def update_profile(req: ProfileUpdate, authorization: str | None = Header(None)) -> dict:
        db, profile = _authed(authorization)
        return db.rename(profile, req.name.strip() or profile.name).view(db.counts(profile))

    @app.delete("/api/profile", status_code=204)
    def delete_profile(authorization: str | None = Header(None)) -> Response:
        db, profile = _authed(authorization)
        db.delete_profile(profile)
        return Response(status_code=204)

    @app.get("/api/profile/records")
    def get_records(
        limit: int = Query(20_000, ge=1, le=50_000),
        authorization: str | None = Header(None),
    ) -> dict:
        db, profile = _authed(authorization)
        records = db.records(profile, limit)
        counts = db.counts(profile)
        return {**records, "truncated": any(counts[k] > len(records[k]) for k in records)}

    @app.post("/api/profile/records")
    def add_records(req: RecordUpload, authorization: str | None = Header(None)) -> dict:
        db, profile = _authed(authorization)
        valid: dict[str, list[dict]] = {}
        rejected: list[dict] = []
        for kind, items in (("answers", req.answers), ("decisions", req.decisions)):
            valid[kind] = []
            for item in items:
                try:
                    rec = _VALIDATORS[kind].validate_python(item)
                except ValidationError as exc:
                    rid = item.get("id") if isinstance(item, dict) else None
                    first = exc.errors()[0]
                    where = ".".join(str(x) for x in first["loc"]) or "record"
                    rejected.append(
                        {
                            "kind": kind,
                            "id": rid if isinstance(rid, str) else None,
                            "error": f"{where}: {first['msg']}",
                        }  # fmt: skip
                    )
                    continue
                valid[kind].append(rec.model_dump(exclude_none=True))
        try:
            added = db.add(profile, valid)
        except QuotaExceeded as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        return {
            "added": added,
            "accepted": {k: len(v) for k, v in valid.items()},
            "rejected": rejected,
        }

    # ------------------------------------------------------------------ hand histories

    @app.post("/api/profile/hands")
    def add_hands(req: HandUpload, authorization: str | None = Header(None)) -> dict:
        """Store completed hands. Each record is validated and replayed first; a record
        that does not add up, or that shows opponent cards that were never revealed, is
        refused with the reason, without blocking the others."""
        db, profile = _authed(authorization)
        valid: list[tuple[dict, dict]] = []
        rejected: list[dict] = []
        for item in req.hands:
            rid = item.get("id") if isinstance(item, dict) else None
            try:
                rec = validate_record(item)
                valid.append((rec, summarize(rec)))
            except (HistoryError, KeyError, TypeError, ValueError) as exc:
                rejected.append({"id": rid if isinstance(rid, str) else None, "error": str(exc)})
        try:
            added = db.add_hands(profile, valid)
        except QuotaExceeded as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        return {"added": added, "accepted": len(valid), "rejected": rejected}

    @app.get("/api/profile/hands")
    def list_hands(
        limit: int = Query(500, ge=1, le=5000),
        authorization: str | None = Header(None),
    ) -> dict:
        """Summaries of saved hands (newest first) and the recurring weaknesses in them."""
        db, profile = _authed(authorization)
        recent = db.hand_summaries(profile, max(limit, 2000))
        total = db.counts(profile)["hands"]
        return {
            "hands": recent[:limit],
            "total": total,
            "weaknesses": find_weaknesses(recent[:2000]),
        }

    @app.get("/api/profile/hands/{hand_id}")
    def get_hand(hand_id: str, authorization: str | None = Header(None)) -> dict:
        """One saved hand with its replay: the table after every event."""
        db, profile = _authed(authorization)
        try:
            record = db.hand(profile, hand_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="This saved hand is unreadable.") from exc
        if record is None:
            raise HTTPException(status_code=404, detail="No such hand")
        try:
            frames = replay(record)
        except (HistoryError, KeyError, TypeError) as exc:
            raise HTTPException(
                status_code=422, detail=f"This saved hand is damaged and cannot be replayed: {exc}"
            ) from exc
        return {"record": record, "frames": frames}

    @app.post("/api/sessions", status_code=201, response_model=PlayResponse)
    def create_session(req: CreateSessionRequest) -> PlayResponse:
        if req.seed is not None and not allow_seed:
            raise HTTPException(status_code=400, detail="Seeded games are disabled on this server")
        config = SessionConfig(
            starting_stack=req.starting_stack,
            small_blind=req.small_blind,
            big_blind=req.big_blind,
            player_name=req.player_name.strip() or "You",
            difficulty=req.difficulty,
            adaptive=req.adaptive,
        )
        session = HeadsUpSession(config=config, seed=req.seed)
        sessions.add(session)
        return PlayResponse(state=session.view(), steps=[])

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str) -> dict:
        session = _get(session_id)
        # Under the session lock, so a read never sees a move half-applied.
        with session.lock:
            return session.view()

    @app.delete("/api/sessions/{session_id}", status_code=204)
    def delete_session(session_id: str) -> Response:
        if not sessions.remove(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return Response(status_code=204)

    @app.post("/api/sessions/{session_id}/hands", response_model=PlayResponse)
    def start_hand(session_id: str) -> PlayResponse:
        session = _get(session_id)
        with session.lock:
            try:
                steps = session.start_hand()
            except SessionStateError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            return PlayResponse(state=session.view(), steps=[s.__dict__ for s in steps])

    @app.post("/api/sessions/{session_id}/actions", response_model=PlayResponse)
    def act(session_id: str, req: ActionRequest) -> PlayResponse:
        session = _get(session_id)
        with session.lock:
            try:
                steps = session.act(Action(req.action, req.amount))
            except SessionStateError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except IllegalActionError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return PlayResponse(state=session.view(), steps=[s.__dict__ for s in steps])

    @app.get("/api/sessions/{session_id}/hands/{hand_number}/review")
    def review_hand(session_id: str, hand_number: int) -> dict:
        """Coaching review of the latest finished hand (only once it is over)."""
        session = _get(session_id)
        with session.lock:
            hand = session.coach.pending(hand_number)
        if hand is None:
            raise HTTPException(status_code=404, detail="No finished hand to review")
        # The analysis is slow; it runs outside the lock so play is never blocked.
        return session.coach.review_hand(hand)

    @app.get("/api/sessions/{session_id}/hands/{hand_number}/record")
    def hand_record(session_id: str, hand_number: int) -> dict:
        """The hand-history record of a recently finished hand, for saving to a profile.

        Only public information: the opponent's cards appear only if they were shown."""
        session = _get(session_id)
        record = session.hand_record(hand_number)  # slow the first time: outside the lock
        if record is None:
            raise HTTPException(status_code=404, detail="No recently finished hand")
        return record

    # Training scenarios contain no hidden cards, so seeds are always allowed here.
    @app.get("/api/training/scenario")
    def training_scenario(
        difficulty: TrainingDifficulty = "beginner",
        concept: TrainingConcept = "pot_odds",
        seed: int | None = Query(None, ge=0, lt=2**31),
    ) -> dict:
        seed = random.SystemRandom().randrange(2**31) if seed is None else seed
        try:
            return training.scenario_view(training.generate(difficulty, concept, seed))
        except training.ScenarioError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/training/practice")
    def training_practice(req: PracticeRequest) -> dict:
        try:
            spot = Spot.from_dict(req.spot)
            return training.scenario_view(training.from_spot(spot, req.difficulty, req.chosen))
        except (SpotError, training.ScenarioError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/training/answer")
    def training_answer(req: AnswerRequest) -> dict:
        try:
            return training.answer(req.id, req.choice)
        except (SpotError, training.ScenarioError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/equity", response_model=EquityResponse)
    def equity(req: EquityRequest) -> EquityResponse:
        try:
            r = calculate_equity(
                req.players,
                board=req.board or None,
                dead=req.dead or None,
                iterations=req.iterations,
                seed=req.seed,
            )
        except ValueError as exc:  # includes RangeError and bad card codes
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return EquityResponse(
            method=r.method,
            trials=r.trials,
            seed=r.seed,
            players=[
                {"equity": p.equity, "win": p.win, "tie": p.tie, "std_error": p.std_error}
                for p in r.players
            ],
        )

    return app


app = create_app()
