"""Authentication routes."""

from fastapi import APIRouter, HTTPException, Request, Response

import call_center_db
from rate_limit import enforce_rate_limit
from schemas import LoginBody
from security import (
    client_ip_from_request,
    get_staff_user_from_request,
    session_cookie_settings,
)
from settings import SESSION_COOKIE_NAME

router = APIRouter()


@router.post("/api/auth/login")
async def api_auth_login(body: LoginBody, request: Request):
    enforce_rate_limit(
        f"auth-login:{client_ip_from_request(request)}",
        limit=20,
        window_seconds=300,
    )
    with call_center_db.db_session() as conn:
        user = call_center_db.get_staff_user_by_username(conn, body.username.strip())
        if not user or int(user.get("is_active", 0)) != 1:
            raise HTTPException(
                status_code=401,
                detail="Usuario o contrasena invalidos.",
            )
        if not call_center_db.verify_password(
            body.password,
            str(user["password_hash"]),
            str(user["password_salt"]),
        ):
            raise HTTPException(
                status_code=401,
                detail="Usuario o contrasena invalidos.",
            )
        session_id = call_center_db.create_staff_session(conn, str(user["id"]))
    response = Response(content='{"ok":true}', media_type="application/json")
    response.set_cookie(SESSION_COOKIE_NAME, session_id, **session_cookie_settings())
    return response


@router.get("/api/auth/me")
async def api_auth_me(request: Request):
    user = get_staff_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado.")
    return {
        "username": user["username"],
        "display_name": user["display_name"],
        "role": user["role"],
    }


@router.post("/api/auth/logout")
async def api_auth_logout(request: Request):
    session_id = request.cookies.get(SESSION_COOKIE_NAME, "").strip()
    if session_id:
        with call_center_db.db_session() as conn:
            call_center_db.delete_staff_session(conn, session_id)
    response = Response(content='{"ok":true}', media_type="application/json")
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response
