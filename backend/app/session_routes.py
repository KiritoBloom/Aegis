"""Household PIN session routes."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import auth

log = logging.getLogger("aegis.session")
router = APIRouter()


class PinIn(BaseModel):
    pin: str


@router.post("/api/session")
def create_session(body: PinIn, request: Request):
    if not auth.required():
        return {"ok": True, "auth": "disabled"}
    token, exp = auth.check_pin(body.pin, auth.client_of(request))
    response = JSONResponse({"ok": True, "expires_at": exp})
    response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_S,
                        httponly=True, samesite="lax",
                        secure=os.environ.get("COOKIE_SECURE", "") == "1")
    log.info("session opened from %s", auth.client_of(request))
    return response


@router.get("/api/session")
def session_state(request: Request):
    return {
        "auth_required": auth.required(),
        "signed_in": (not auth.required())
        or auth.session_ok(request.cookies.get(auth.COOKIE)),
    }


@router.delete("/api/session")
def end_session():
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE)
    return response
