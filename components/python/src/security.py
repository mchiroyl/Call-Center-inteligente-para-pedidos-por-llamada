"""Authentication, authorization and Twilio webhook validation."""

import secrets
from typing import Any

from fastapi import HTTPException, Request, WebSocket, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from twilio.request_validator import RequestValidator

import call_center_db
from settings import PHONE_RE, SESSION_COOKIE_NAME

basic_security = HTTPBasic(auto_error=False)


def mask_phone(raw: str) -> str:
    digits = "".join(char for char in raw if char.isdigit())
    if len(digits) < 4:
        return "***"
    return f"***{digits[-2:]}"


def sanitize_text(text: str) -> str:
    return PHONE_RE.sub(lambda match: mask_phone(match.group(1)), text)


def client_ip_from_request(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    client = request.client.host if request.client else ""
    return client or "unknown"


def client_ip_from_websocket(websocket: WebSocket) -> str:
    forwarded_for = websocket.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    client = websocket.client.host if websocket.client else ""
    return client or "unknown"


def admin_basic_credentials() -> tuple[str, str] | None:
    import os

    username = os.getenv("ADMIN_BASIC_USERNAME", "").strip()
    password = os.getenv("ADMIN_BASIC_PASSWORD", "").strip()
    if username and password:
        return username, password
    return None


def session_cookie_settings() -> dict[str, Any]:
    return {
        "httponly": True,
        "samesite": "lax",
        "secure": False,
        "path": "/",
        "max_age": 60 * 60 * 12,
    }


def get_staff_user_from_request(request: Request) -> dict[str, Any] | None:
    session_id = request.cookies.get(SESSION_COOKIE_NAME, "").strip()
    if not session_id:
        return None
    with call_center_db.db_session() as conn:
        return call_center_db.get_staff_user_by_session(conn, session_id)


def get_staff_user_from_websocket(websocket: WebSocket) -> dict[str, Any] | None:
    session_id = websocket.cookies.get(SESSION_COOKIE_NAME, "").strip()
    if not session_id:
        return None
    with call_center_db.db_session() as conn:
        return call_center_db.get_staff_user_by_session(conn, session_id)


def require_staff_roles(
    request: Request,
    *,
    roles: set[str],
    allow_operations_key: bool = False,
) -> dict[str, Any]:
    import os

    user = get_staff_user_from_request(request)
    if user and user.get("role") in roles and int(user.get("is_active", 0)) == 1:
        return user
    if allow_operations_key:
        expected = os.getenv("OPERATIONS_API_KEY", "").strip()
        provided = request.headers.get("X-Operations-Key", "").strip()
        if expected and provided and secrets.compare_digest(provided, expected):
            return {
                "id": "operations-key",
                "username": "operations-key",
                "display_name": "Operations Key",
                "role": "operaciones",
                "is_active": 1,
            }
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Autenticacion requerida.",
    )


def ensure_status_transition_allowed(
    *,
    role: str,
    current_status: str,
    next_status: str,
) -> None:
    if role in {"admin", "operaciones"}:
        return
    if role == "cocina":
        allowed = {
            ("nuevo", "en_preparacion"),
            ("en_preparacion", "listo"),
        }
        if (current_status, next_status) not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Cocina solo puede pasar de nuevo a en_preparacion y de "
                    "en_preparacion a listo."
                ),
            )
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Este rol no puede actualizar el estado operativo del pedido.",
    )


def verify_admin_access(credentials: HTTPBasicCredentials | None) -> None:
    configured = admin_basic_credentials()
    if configured is None:
        return
    expected_user, expected_password = configured
    provided_user = credentials.username if credentials else ""
    provided_password = credentials.password if credentials else ""
    if not (
        secrets.compare_digest(provided_user, expected_user)
        and secrets.compare_digest(provided_password, expected_password)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales de administrador invalidas.",
            headers={"WWW-Authenticate": "Basic"},
        )


def ensure_admin_authenticated(
    request: Request,
    credentials: HTTPBasicCredentials | None,
) -> dict[str, Any]:
    user = get_staff_user_from_request(request)
    if user and user.get("role") == "admin" and int(user.get("is_active", 0)) == 1:
        return user
    configured = admin_basic_credentials()
    if configured is not None:
        verify_admin_access(credentials)
        return {
            "id": "basic-admin",
            "username": configured[0],
            "display_name": "Administrador",
            "role": "admin",
            "is_active": 1,
        }
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Autenticacion de administrador requerida.",
    )


def operations_ws_key_valid(websocket: WebSocket) -> bool:
    import os

    expected = os.getenv("OPERATIONS_API_KEY", "").strip()
    user = get_staff_user_from_websocket(websocket)
    if user and user.get("role") in {"admin", "cocina", "caja", "operaciones"}:
        return True
    if not expected:
        return True
    provided = websocket.query_params.get("ops_key", "").strip()
    return bool(provided) and secrets.compare_digest(provided, expected)


async def validate_twilio_request(
    request: Request,
    form: dict[str, str],
) -> None:
    import os

    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    if not auth_token:
        return
    signature = request.headers.get("X-Twilio-Signature", "").strip()
    if not signature:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Firma Twilio ausente.",
        )
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    )
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    full_url = f"{proto}://{host}{request.url.path}"
    if request.url.query:
        full_url = f"{full_url}?{request.url.query}"
    validator = RequestValidator(auth_token)
    if not validator.validate(full_url, form, signature):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Firma Twilio invalida.",
        )
