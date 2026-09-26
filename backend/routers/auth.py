"""Passcode login for public DevLoop deployments."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from config import settings


router = APIRouter(prefix="/api/auth", tags=["auth"])
_COOKIE_NAME = "devloop_access"


class LoginRequest(BaseModel):
    token: str


def _is_authenticated(request: Request) -> bool:
    if not settings.access_token:
        return True
    supplied = request.cookies.get(_COOKIE_NAME, "")
    return hmac.compare_digest(supplied, settings.access_token)


@router.get("/status")
def auth_status(request: Request) -> dict[str, bool]:
    return {
        "required": bool(settings.access_token),
        "authenticated": _is_authenticated(request),
    }


@router.post("/login")
def login(request: Request, body: LoginRequest, response: Response) -> dict[str, bool]:
    if not settings.access_token:
        return {"required": False, "authenticated": True}
    if not hmac.compare_digest(body.token, settings.access_token):
        raise HTTPException(status_code=401, detail="Incorrect access code.")

    response.set_cookie(
        _COOKIE_NAME,
        body.token,
        httponly=True,
        secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https",
        samesite="strict",
        max_age=43200,
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"required": True, "authenticated": True}