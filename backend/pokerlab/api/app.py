"""FastAPI application exposing heads-up Hold'em sessions."""

from __future__ import annotations

import os
import threading

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware

from .. import __version__
from ..engine import Action, IllegalActionError
from ..session import HeadsUpSession, SessionConfig, SessionStateError
from .schemas import ActionRequest, CreateSessionRequest, PlayResponse
from .store import SessionStore


def create_app(store: SessionStore | None = None) -> FastAPI:
    app = FastAPI(title="PokerLab API", version=__version__)
    sessions = store or SessionStore()
    # One lock per process is plenty here; it serialises moves within a session.
    play_lock = threading.Lock()

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
        return {"status": "ok", "version": __version__}

    @app.post("/api/sessions", status_code=201, response_model=PlayResponse)
    def create_session(req: CreateSessionRequest) -> PlayResponse:
        config = SessionConfig(
            starting_stack=req.starting_stack,
            small_blind=req.small_blind,
            big_blind=req.big_blind,
            player_name=req.player_name.strip() or "You",
        )
        session = HeadsUpSession(config=config, seed=req.seed)
        sessions.add(session)
        return PlayResponse(state=session.view(), steps=[])

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str) -> dict:
        return _get(session_id).view()

    @app.delete("/api/sessions/{session_id}", status_code=204)
    def delete_session(session_id: str) -> Response:
        if not sessions.remove(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return Response(status_code=204)

    @app.post("/api/sessions/{session_id}/hands", response_model=PlayResponse)
    def start_hand(session_id: str) -> PlayResponse:
        session = _get(session_id)
        with play_lock:
            try:
                steps = session.start_hand()
            except SessionStateError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            return PlayResponse(state=session.view(), steps=[s.__dict__ for s in steps])

    @app.post("/api/sessions/{session_id}/actions", response_model=PlayResponse)
    def act(session_id: str, req: ActionRequest) -> PlayResponse:
        session = _get(session_id)
        with play_lock:
            try:
                steps = session.act(Action(req.action, req.amount))
            except SessionStateError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except IllegalActionError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return PlayResponse(state=session.view(), steps=[s.__dict__ for s in steps])

    return app


app = create_app()
