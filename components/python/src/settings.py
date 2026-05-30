"""Application settings and constants."""

import mimetypes
import os
import re
from pathlib import Path

from dotenv import load_dotenv

import call_center_db

load_dotenv()
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")

SRC_DIR = Path(__file__).resolve().parent
PYTHON_DIR = SRC_DIR.parent
STATIC_DIR = PYTHON_DIR.parent / "web" / "dist"
ADMIN_HTML_PATH = SRC_DIR / "admin.html"
TRACK_HTML_PATH = SRC_DIR / "track.html"
LOGIN_HTML_PATH = SRC_DIR / "login.html"

SESSION_COOKIE_NAME = "call_center_staff_session"
PHONE_RE = re.compile(r"(?<!\w)(\+?\d[\d\s-]{6,}\d)")

VOICE_GREETING = (
    "Hola, gracias por llamar al centro de pedidos. "
    "Hoy tenemos hamburguesas, pizzas, tacos, bebidas y acompanamientos. "
    "Puede decirme su pedido, preguntar por precios, pedir una categoria o "
    "solicitar una recomendacion. Tambien aceptamos pagos con PayPal."
)


def allowed_origins() -> list[str]:
    raw = os.getenv("ALLOWED_ORIGINS", "").strip()
    if raw:
        return [item.strip() for item in raw.split(",") if item.strip()]
    return [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


def langgraph_checkpoint_db_path() -> Path:
    return call_center_db.db_path().with_name("langgraph_checkpoints.db")

