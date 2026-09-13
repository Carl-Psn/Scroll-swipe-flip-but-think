"""FastAPI entrypoint for Verto (plan A). Streamlit remains on app.py (plan B)."""

from __future__ import annotations

import os
import uuid
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from verto.service import VertoSession

APP_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(APP_DIR, "flipboard_component")
SESSION_COOKIE = "verto_session"
_sessions: dict[str, VertoSession] = {}


def get_or_create_session(session_id: str | None) -> tuple[str, VertoSession]:
    if session_id and session_id in _sessions:
        return session_id, _sessions[session_id]
    new_id = session_id or uuid.uuid4().hex
    _sessions[new_id] = VertoSession()
    return new_id, _sessions[new_id]


def session_from_request(request: Request) -> tuple[str, VertoSession]:
    return get_or_create_session(request.cookies.get(SESSION_COOKIE))


def attach_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_id,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )


class SettingsBody(BaseModel):
    gemini_api_key: str = ""


class FeedsBody(BaseModel):
    feeds: list[dict[str, str]] = Field(default_factory=list)
    disabled_feeds: list[str] = Field(default_factory=list)
    active_packages: list[str] = Field(default_factory=list)


class SummaryBody(BaseModel):
    summary_points: int = 5
    gemini_api_key: str = ""


app = FastAPI(title="Verto", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/bootstrap")
def api_bootstrap(request: Request, response: Response) -> dict[str, Any]:
    session_id, session = session_from_request(request)
    attach_session_cookie(response, session_id)
    return session.bootstrap_payload()


@app.put("/api/settings")
def api_settings(body: SettingsBody, request: Request, response: Response) -> dict[str, bool]:
    session_id, session = session_from_request(request)
    session.update_settings(body.gemini_api_key)
    attach_session_cookie(response, session_id)
    return {"ok": True}


@app.put("/api/feeds")
def api_feeds(body: FeedsBody, request: Request, response: Response) -> dict[str, Any]:
    session_id, session = session_from_request(request)
    session.update_feeds(body.feeds, body.disabled_feeds, body.active_packages)
    attach_session_cookie(response, session_id)
    return session.bootstrap_payload()


@app.post("/api/articles/load-more")
def api_load_more(request: Request, response: Response) -> dict[str, Any]:
    session_id, session = session_from_request(request)
    session.load_more()
    attach_session_cookie(response, session_id)
    return session.bootstrap_payload()


@app.post("/api/articles/{article_id}/text")
def api_article_text(article_id: str, request: Request, response: Response) -> dict[str, Any]:
    session_id, session = session_from_request(request)
    article = session.article_index().get(article_id)
    if not article:
        return {"error": "Article introuvable"}
    paragraphs = session.enrich_text_for(article_id, article)
    attach_session_cookie(response, session_id)
    return {
        "id": article_id,
        "paragraphs": paragraphs,
        "enrichments": session.build_enrichments(),
    }


@app.post("/api/articles/{article_id}/summary")
def api_article_summary(
    article_id: str,
    body: SummaryBody,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    session_id, session = session_from_request(request)
    article = session.article_index().get(article_id)
    if not article:
        return {"error": "Article introuvable"}
    n_points = max(1, min(5, int(body.summary_points or 5)))
    summary = session.enrich_summary_for(
        article_id,
        article,
        n_points=n_points,
        api_key=body.gemini_api_key,
    )
    attach_session_cookie(response, session_id)
    return {
        "id": article_id,
        "summary": summary,
        "enrichments": session.build_enrichments(),
    }


app.mount(
    "/",
    StaticFiles(directory=FRONTEND_DIR, html=True),
    name="frontend",
)
