"""
SQLite persistence for the call-center ordering demo.

This module stores:
- product catalog
- knowledge-base documents and embedding chunks
- call sessions and workflow traces
- draft orders extracted from the conversation
- confirmed operational orders
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent


def db_path() -> Path:
    raw = os.environ.get("CALL_CENTER_DB_PATH", "").strip()
    if not raw:
        raw = os.environ.get("RESTAURANT_DB_PATH", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return _project_root() / "data" / "call_center.db"


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


ORDER_STATUSES = (
    "nuevo",
    "en_preparacion",
    "listo",
    "en_camino",
    "entregado",
    "cancelado",
)

PAYMENT_STATUSES = (
    "pending_cash",
    "paid",
)

PICKUP_ADDRESS_TEXT = "Cliente pasara a recoger."

STAFF_ROLES = (
    "admin",
    "cocina",
    "caja",
    "operaciones",
)

MENU_CATEGORY_ALIASES = {
    "bebida": "bebidas",
    "bebidas": "bebidas",
    "refresco": "bebidas",
    "refrescos": "bebidas",
    "agua": "bebidas",
    "aguas": "bebidas",
    "gaseosa": "bebidas",
    "gaseosas": "bebidas",
    "hamburguesa": "hamburguesas",
    "hamburguesas": "hamburguesas",
    "amburguesa": "hamburguesas",
    "amburguesas": "hamburguesas",
    "amburgesa": "hamburguesas",
    "amburgesas": "hamburguesas",
    "burger": "hamburguesas",
    "burgers": "hamburguesas",
    "pizza": "pizzas",
    "pizzas": "pizzas",
    "taco": "tacos",
    "tacos": "tacos",
    "papa": "acompanamientos",
    "papas": "acompanamientos",
    "papa frita": "acompanamientos",
    "papas fritas": "acompanamientos",
    "acompanamiento": "acompanamientos",
    "acompanamientos": "acompanamientos",
}


PHONE_RE = re.compile(r"(?<!\w)(\+?\d[\d\s-]{6,}\d)")


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def db_session():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


PRODUCT_SEED: list[dict[str, Any]] = [
    {
        "id": "burger-clasica",
        "name": "Hamburguesa clasica",
        "category": "hamburguesas",
        "description": "Pan brioche, carne de res, queso americano, lechuga y tomate.",
        "stock": 40,
        "price_cents": 4200,
        "keywords": ["hamburguesa", "clasica", "res", "burger"],
    },
    {
        "id": "burger-doble-bacon",
        "name": "Hamburguesa doble bacon",
        "category": "hamburguesas",
        "description": "Doble carne, bacon, cheddar y salsa especial.",
        "stock": 28,
        "price_cents": 5900,
        "keywords": ["doble", "bacon", "hamburguesa", "burger"],
    },
    {
        "id": "pizza-pepperoni-mediana",
        "name": "Pizza pepperoni mediana",
        "category": "pizzas",
        "description": "Pizza mediana de pepperoni para dos o tres personas.",
        "stock": 20,
        "price_cents": 7800,
        "keywords": ["pizza", "pepperoni", "mediana"],
    },
    {
        "id": "pizza-vegetariana-familiar",
        "name": "Pizza vegetariana familiar",
        "category": "pizzas",
        "description": "Pizza familiar con champinones, aceitunas, cebolla y pimientos.",
        "stock": 16,
        "price_cents": 11800,
        "keywords": ["pizza", "vegetariana", "familiar"],
    },
    {
        "id": "tacos-pollo-orden",
        "name": "Orden de tacos de pollo",
        "category": "tacos",
        "description": "Tres tacos de pollo con cebolla, cilantro y salsa.",
        "stock": 36,
        "price_cents": 3600,
        "keywords": ["tacos", "pollo", "orden"],
    },
    {
        "id": "papas-grandes",
        "name": "Papas fritas grandes",
        "category": "acompanamientos",
        "description": "Porcion grande de papas fritas.",
        "stock": 45,
        "price_cents": 1900,
        "keywords": ["papas", "fritas", "acompanamiento"],
    },
    {
        "id": "refresco-cola",
        "name": "Refresco de cola",
        "category": "bebidas",
        "description": "Bebida gaseosa de cola en lata.",
        "stock": 80,
        "price_cents": 900,
        "keywords": ["cola", "gaseosa", "refresco", "coca"],
    },
    {
        "id": "agua-botella",
        "name": "Agua pura",
        "category": "bebidas",
        "description": "Botella de agua pura de 600 ml.",
        "stock": 60,
        "price_cents": 700,
        "keywords": ["agua", "botella"],
    },
]


def _staff_seed() -> list[dict[str, str]]:
    return [
        {
            "username": "admin",
            "display_name": "Administrador",
            "role": "admin",
            "password": os.getenv("STAFF_ADMIN_PASSWORD", "admin123"),
        },
        {
            "username": "cocina",
            "display_name": "Cocina",
            "role": "cocina",
            "password": os.getenv("STAFF_KITCHEN_PASSWORD", "cocina123"),
        },
        {
            "username": "caja",
            "display_name": "Caja",
            "role": "caja",
            "password": os.getenv("STAFF_CASHIER_PASSWORD", "caja123"),
        },
        {
            "username": "operaciones",
            "display_name": "Operaciones",
            "role": "operaciones",
            "password": os.getenv("STAFF_OPERATIONS_PASSWORD", "operaciones123"),
        },
    ]


def _seed_knowledge_documents() -> list[dict[str, str]]:
    menu_docs = []
    for product in PRODUCT_SEED:
        menu_docs.append(
            {
                "id": f"menu-{product['id']}",
                "title": f"Menu - {product['name']}",
                "source": "menu",
                "tags_json": json.dumps(
                    [product["category"], product["id"], *product["keywords"]],
                    ensure_ascii=False,
                ),
                "content": (
                    f"Producto: {product['name']}. "
                    f"Categoria: {product['category']}. "
                    f"Descripcion: {product['description']} "
                    f"Precio: Q{product['price_cents'] / 100:.2f}. "
                    f"Alias utiles: {', '.join(product['keywords'])}."
                ),
            }
        )

    policy_docs = [
        {
            "id": "policy-hours",
            "title": "Horario del centro de pedidos",
            "source": "policy",
            "tags_json": json.dumps(["horario", "atencion"], ensure_ascii=False),
            "content": (
                "El centro de pedidos atiende todos los dias de 10:00 a 22:00. "
                "Los pedidos para entrega a domicilio se aceptan hasta las 21:30."
            ),
        },
        {
            "id": "policy-delivery",
            "title": "Cobertura de entrega",
            "source": "policy",
            "tags_json": json.dumps(
                ["domicilio", "zonas", "entrega"], ensure_ascii=False
            ),
            "content": (
                "La cobertura de entrega incluye zonas 1, 4, 7, 9, 10, 11, 12, 13, 14 y 15. "
                "El tiempo estimado de entrega es de 35 a 55 minutos segun trafico y volumen."
            ),
        },
        {
            "id": "policy-payment",
            "title": "Metodos de pago",
            "source": "policy",
            "tags_json": json.dumps(
                ["pago", "tarjeta", "efectivo", "transferencia", "paypal"], ensure_ascii=False
            ),
            "content": (
                "Se aceptan pagos con efectivo, tarjeta, transferencia y PayPal. "
                "Los pedidos con tarjeta, transferencia o PayPal quedan marcados como pagados al confirmarse. "
                "Los pedidos en efectivo quedan pendientes de cobro para caja o reparto."
            ),
        },
        {
            "id": "policy-confirmation",
            "title": "Politica de confirmacion",
            "source": "policy",
            "tags_json": json.dumps(
                ["confirmacion", "pedido", "flujo"], ensure_ascii=False
            ),
            "content": (
                "Antes de generar una orden final el asistente debe resumir el pedido, "
                "verificar modalidad de entrega o recoger, confirmar direccion si aplica "
                "y registrar el metodo de pago."
            ),
        },
    ]
    return [*menu_docs, *policy_docs]


def bootstrap_db() -> None:
    conn = connect()
    try:
        init_db(conn)
        seed_products_if_empty(conn)
        seed_knowledge_if_empty(conn)
        upgrade_seeded_knowledge(conn)
        seed_staff_users_if_empty(conn)
    finally:
        conn.close()


def reset_demo_state(conn: sqlite3.Connection) -> dict[str, int]:
    """
    Reset operational and conversational runtime state for a clean demo.

    Preserves:
    - product catalog identities and metadata
    - knowledge documents/chunks

    Resets:
    - orders and order lines
    - call sessions, workflow traces and drafts
    - product stock levels back to seed values
    """
    init_db(conn)
    counts = {
        "orders_removed": int(
            conn.execute("SELECT COUNT(*) AS c FROM orders").fetchone()["c"]
        ),
        "sessions_removed": int(
            conn.execute("SELECT COUNT(*) AS c FROM call_sessions").fetchone()["c"]
        ),
        "events_removed": int(
            conn.execute("SELECT COUNT(*) AS c FROM workflow_events").fetchone()["c"]
        ),
    }

    conn.execute("DELETE FROM order_lines")
    conn.execute("DELETE FROM order_status_events")
    conn.execute("DELETE FROM orders")
    conn.execute("DELETE FROM workflow_events")
    conn.execute("DELETE FROM order_drafts")
    conn.execute("DELETE FROM call_sessions")
    conn.execute("DELETE FROM staff_sessions")

    for product in PRODUCT_SEED:
        conn.execute(
            """
            UPDATE products
            SET stock = ?, is_active = 1
            WHERE id = ?
            """,
            (product["stock"], product["id"]),
        )

    conn.commit()
    return counts


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _rebuild_legacy_schema_if_needed(conn: sqlite3.Connection) -> None:
    existing_tables = {
        str(row["name"])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "products" in existing_tables and "description" not in _table_columns(conn, "products"):
        conn.executescript(
            """
            DROP TABLE IF EXISTS order_lines;
            DROP TABLE IF EXISTS orders;
            DROP TABLE IF EXISTS cart_lines;
            DROP TABLE IF EXISTS order_drafts;
            DROP TABLE IF EXISTS workflow_events;
            DROP TABLE IF EXISTS call_sessions;
            DROP TABLE IF EXISTS knowledge_chunks;
            DROP TABLE IF EXISTS knowledge_documents;
            DROP TABLE IF EXISTS products;
            """
        )
        conn.commit()
        return

    if "orders" in existing_tables and "payment_status" not in _table_columns(conn, "orders"):
        conn.executescript(
            """
            DROP TABLE IF EXISTS order_lines;
            DROP TABLE IF EXISTS orders;
            """
        )
        conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    _rebuild_legacy_schema_if_needed(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS products (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            stock INTEGER NOT NULL CHECK (stock >= 0),
            price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
            is_active INTEGER NOT NULL DEFAULT 1,
            keywords_json TEXT NOT NULL DEFAULT '[]'
        );

        CREATE TABLE IF NOT EXISTS knowledge_documents (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            source TEXT NOT NULL,
            tags_json TEXT NOT NULL DEFAULT '[]'
        );

        CREATE TABLE IF NOT EXISTS knowledge_chunks (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            embedding_json TEXT,
            embedding_model TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS call_sessions (
            id TEXT PRIMARY KEY,
            channel TEXT NOT NULL,
            customer_name TEXT,
            customer_phone TEXT,
            status TEXT NOT NULL,
            current_state TEXT NOT NULL,
            latest_transcript TEXT,
            latest_response TEXT,
            order_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS order_drafts (
            session_id TEXT PRIMARY KEY REFERENCES call_sessions(id) ON DELETE CASCADE,
            draft_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS workflow_events (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES call_sessions(id) ON DELETE CASCADE,
            step TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            customer_name TEXT,
            customer_phone TEXT,
            delivery_type TEXT NOT NULL,
            address TEXT,
            notes TEXT,
            payment_method TEXT NOT NULL,
            payment_status TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            total_cents INTEGER NOT NULL CHECK (total_cents >= 0),
            source_channel TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS order_lines (
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            product_id TEXT NOT NULL,
            product_name TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK (quantity > 0),
            unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0)
        );

        CREATE TABLE IF NOT EXISTS order_status_events (
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            status TEXT NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS staff_users (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS staff_sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES staff_users(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        """
    )
    _backfill_order_status_history(conn)
    conn.commit()


def _backfill_order_status_history(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT o.id, o.status, o.created_at
        FROM orders o
        LEFT JOIN order_status_events ose ON ose.order_id = o.id
        GROUP BY o.id
        HAVING COUNT(ose.id) = 0
        """
    ).fetchall()
    for row in rows:
        conn.execute(
            """
            INSERT INTO order_status_events (id, order_id, status, note, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                row["id"],
                row["status"],
                "historial inicial reconstruido",
                row["created_at"],
            ),
        )


def seed_products_if_empty(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()
    if row and row["c"] > 0:
        return
    conn.executemany(
        """
        INSERT INTO products (
            id, name, category, description, stock, price_cents, is_active, keywords_json
        ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
        """,
        [
            (
                product["id"],
                product["name"],
                product["category"],
                product["description"],
                product["stock"],
                product["price_cents"],
                json.dumps(product["keywords"], ensure_ascii=False),
            )
            for product in PRODUCT_SEED
        ],
    )
    conn.commit()


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    effective_salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        effective_salt.encode("utf-8"),
        240000,
    )
    return digest.hex(), effective_salt


def verify_password(password: str, password_hash: str, password_salt: str) -> bool:
    digest, _ = hash_password(password, password_salt)
    return secrets.compare_digest(digest, password_hash)


def seed_staff_users_if_empty(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT COUNT(*) AS c FROM staff_users").fetchone()
    if row and row["c"] > 0:
        return
    now = utc_now_iso()
    for staff in _staff_seed():
        password_hash, password_salt = hash_password(staff["password"])
        conn.execute(
            """
            INSERT INTO staff_users (
                id, username, display_name, role, password_hash, password_salt,
                is_active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                staff["username"],
                staff["display_name"],
                staff["role"],
                password_hash,
                password_salt,
                now,
                now,
            ),
        )
    conn.commit()


def get_staff_user_by_username(conn: sqlite3.Connection, username: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, username, display_name, role, password_hash, password_salt, is_active
        FROM staff_users
        WHERE username = ?
        """,
        (username,),
    ).fetchone()
    return dict(row) if row else None


def get_staff_user_by_session(conn: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT su.id, su.username, su.display_name, su.role, su.is_active, ss.expires_at
        FROM staff_sessions ss
        JOIN staff_users su ON su.id = ss.user_id
        WHERE ss.id = ?
        """,
        (session_id,),
    ).fetchone()
    if not row:
        return None
    data = dict(row)
    if data["expires_at"] < utc_now_iso():
        conn.execute("DELETE FROM staff_sessions WHERE id = ?", (session_id,))
        conn.commit()
        return None
    return data


def create_staff_session(conn: sqlite3.Connection, user_id: str, *, ttl_hours: int = 12) -> str:
    session_id = secrets.token_urlsafe(32)
    created_at = utc_now_iso()
    expires_at = datetime.now(UTC).replace(microsecond=0)
    expires_at = expires_at.timestamp() + (ttl_hours * 3600)
    expires_at_iso = datetime.fromtimestamp(expires_at, UTC).isoformat()
    conn.execute(
        """
        INSERT INTO staff_sessions (id, user_id, created_at, expires_at)
        VALUES (?, ?, ?, ?)
        """,
        (session_id, user_id, created_at, expires_at_iso),
    )
    conn.commit()
    return session_id


def delete_staff_session(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute("DELETE FROM staff_sessions WHERE id = ?", (session_id,))
    conn.commit()


def list_menu_categories(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT category
        FROM products
        WHERE is_active = 1 AND stock > 0
        ORDER BY category
        """
    ).fetchall()
    return [str(row["category"]) for row in rows]


def normalize_menu_category(category: str | None) -> str | None:
    raw = " ".join((category or "").strip().lower().split())
    if not raw:
        return None
    if raw in MENU_CATEGORY_ALIASES:
        return MENU_CATEGORY_ALIASES[raw]
    for alias, canonical in MENU_CATEGORY_ALIASES.items():
        if alias in raw:
            return canonical
    return None


def menu_guidance_text(conn: sqlite3.Connection) -> str:
    categories = list_menu_categories(conn)
    if not categories:
        return "En este momento no hay productos disponibles."
    labels = {
        "hamburguesas": "hamburguesas",
        "pizzas": "pizzas",
        "tacos": "tacos",
        "bebidas": "bebidas",
        "acompanamientos": "acompanamientos",
    }
    friendly = [labels.get(category, category) for category in categories]
    return (
        "Tenemos disponible "
        + ", ".join(friendly[:-1])
        + (" y " + friendly[-1] if len(friendly) > 1 else friendly[0])
        + ". Tambien puede preguntarme precios, opciones por categoria o pedir una recomendacion."
    )


def browse_menu(
    conn: sqlite3.Connection,
    *,
    category: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    normalized = normalize_menu_category(category)
    query = """
        SELECT id, name, category, description, stock, price_cents
        FROM products
        WHERE is_active = 1 AND stock > 0
    """
    params: list[Any] = []
    if normalized:
        query += " AND lower(category) = ?"
        params.append(normalized)
    query += " ORDER BY price_cents ASC, name ASC LIMIT ?"
    params.append(max(1, min(limit, 10)))
    rows = conn.execute(query, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def _chunk_text(text: str, size: int = 320, overlap: int = 40) -> list[str]:
    clean = " ".join(text.split())
    if len(clean) <= size:
        return [clean]
    out: list[str] = []
    start = 0
    while start < len(clean):
        end = min(len(clean), start + size)
        out.append(clean[start:end])
        if end >= len(clean):
            break
        start = max(end - overlap, start + 1)
    return out


def seed_knowledge_if_empty(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT COUNT(*) AS c FROM knowledge_documents").fetchone()
    if row and row["c"] > 0:
        return

    docs = _seed_knowledge_documents()
    conn.executemany(
        """
        INSERT INTO knowledge_documents (id, title, content, source, tags_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                doc["id"],
                doc["title"],
                doc["content"],
                doc["source"],
                doc["tags_json"],
            )
            for doc in docs
        ],
    )

    for doc in docs:
        chunks = _chunk_text(doc["content"])
        for idx, chunk in enumerate(chunks):
            chunk_id = f"{doc['id']}::chunk::{idx}"
            metadata = {"title": doc["title"], "source": doc["source"]}
            conn.execute(
                """
                INSERT INTO knowledge_chunks (
                    id, document_id, chunk_index, content, metadata_json, embedding_json,
                    embedding_model, updated_at
                ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?)
                """,
                (
                    chunk_id,
                    doc["id"],
                    idx,
                    chunk,
                    json.dumps(metadata, ensure_ascii=False),
                    utc_now_iso(),
                ),
            )
    conn.commit()


def list_available_products(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, name, category, description, stock, price_cents, keywords_json
        FROM products
        WHERE is_active = 1 AND stock > 0
        ORDER BY category, name
        """
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["keywords"] = json.loads(item.pop("keywords_json"))
        out.append(item)
    return out


def list_all_products(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, name, category, description, stock, price_cents, is_active, keywords_json
        FROM products
        ORDER BY category, name
        """
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["keywords"] = json.loads(item.pop("keywords_json"))
        out.append(item)
    return out


def product_catalog_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in list_available_products(conn)}


def menu_catalog_for_prompt(conn: sqlite3.Connection) -> str:
    lines = []
    for item in list_available_products(conn):
        aliases = ", ".join(item["keywords"])
        lines.append(
            f"- {item['id']}: {item['name']} | categoria={item['category']} | "
            f"precio=Q{item['price_cents'] / 100:.2f} | descripcion={item['description']} | "
            f"aliases={aliases}"
        )
    return "\n".join(lines)


def get_product(conn: sqlite3.Connection, product_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, name, category, description, stock, price_cents, keywords_json
        FROM products
        WHERE id = ? AND is_active = 1
        """,
        (product_id,),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["keywords"] = json.loads(item.pop("keywords_json"))
    return item


def get_or_create_session(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    channel: str = "web-simulated-call",
) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM call_sessions WHERE id = ?", (session_id,)).fetchone()
    if row:
        return dict(row)
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO call_sessions (
            id, channel, status, current_state, latest_transcript, latest_response,
            order_id, created_at, updated_at
        ) VALUES (?, ?, 'active', 'call_started', NULL, NULL, NULL, ?, ?)
        """,
        (session_id, channel, now, now),
    )
    conn.execute(
        """
        INSERT INTO order_drafts (session_id, draft_json, updated_at)
        VALUES (?, ?, ?)
        """,
        (session_id, json.dumps(empty_draft(), ensure_ascii=False), now),
    )
    conn.commit()
    return dict(
        conn.execute("SELECT * FROM call_sessions WHERE id = ?", (session_id,)).fetchone()
    )


def update_session(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    customer_name: str | None = None,
    customer_phone: str | None = None,
    current_state: str | None = None,
    latest_transcript: str | None = None,
    latest_response: str | None = None,
    status: str | None = None,
    order_id: str | None = None,
) -> None:
    current = get_or_create_session(conn, session_id)
    payload = {
        "customer_name": customer_name if customer_name is not None else current["customer_name"],
        "customer_phone": customer_phone if customer_phone is not None else current["customer_phone"],
        "current_state": current_state if current_state is not None else current["current_state"],
        "latest_transcript": latest_transcript if latest_transcript is not None else current["latest_transcript"],
        "latest_response": latest_response if latest_response is not None else current["latest_response"],
        "status": status if status is not None else current["status"],
        "order_id": order_id if order_id is not None else current["order_id"],
    }
    conn.execute(
        """
        UPDATE call_sessions
        SET customer_name = ?, customer_phone = ?, current_state = ?, latest_transcript = ?,
            latest_response = ?, status = ?, order_id = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            payload["customer_name"],
            payload["customer_phone"],
            payload["current_state"],
            payload["latest_transcript"],
            payload["latest_response"],
            payload["status"],
            payload["order_id"],
            utc_now_iso(),
            session_id,
        ),
    )
    conn.commit()


def empty_draft() -> dict[str, Any]:
    return {
        "customer_name": None,
        "customer_phone": None,
        "delivery_type": None,
        "address": None,
        "payment_method": None,
        "notes": None,
        "items": [],
        "missing_fields": ["items", "customer_name", "customer_phone", "delivery_type", "payment_method"],
        "ready_for_confirmation": False,
        "last_intent": "greeting",
        "last_retrieval_hits": [],
        "last_updated_at": utc_now_iso(),
    }


def get_draft(conn: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    get_or_create_session(conn, session_id)
    row = conn.execute(
        "SELECT draft_json FROM order_drafts WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if not row:
        return empty_draft()
    draft = json.loads(row["draft_json"])
    return draft


def save_draft(conn: sqlite3.Connection, session_id: str, draft: dict[str, Any]) -> None:
    payload = json.dumps(draft, ensure_ascii=False)
    conn.execute(
        """
        INSERT INTO order_drafts (session_id, draft_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET draft_json = excluded.draft_json,
                                              updated_at = excluded.updated_at
        """,
        (session_id, payload, utc_now_iso()),
    )
    conn.commit()


def log_workflow_event(
    conn: sqlite3.Connection,
    session_id: str,
    step: str,
    payload: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO workflow_events (id, session_id, step, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            session_id,
            step,
            json.dumps(_sanitize_event_payload(payload), ensure_ascii=False),
            utc_now_iso(),
        ),
    )
    conn.commit()


def _sanitize_event_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sanitize_event_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_event_payload(item) for item in value]
    if isinstance(value, str):
        return PHONE_RE.sub(lambda match: _mask_phone_like(match.group(1)), value)
    return value


def _mask_phone_like(raw: str) -> str:
    digits = [char for char in raw if char.isdigit()]
    if len(digits) < 4:
        return "***"
    visible = "".join(digits[-2:])
    return f"***{visible}"


def list_unembedded_chunks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT kc.id, kc.content, kc.metadata_json, kd.title, kd.source
        FROM knowledge_chunks kc
        JOIN knowledge_documents kd ON kd.id = kc.document_id
        WHERE kc.embedding_json IS NULL
        ORDER BY kc.document_id, kc.chunk_index
        """
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json"))
        out.append(item)
    return out


def save_chunk_embedding(
    conn: sqlite3.Connection,
    chunk_id: str,
    vector: list[float],
    embedding_model: str,
) -> None:
    conn.execute(
        """
        UPDATE knowledge_chunks
        SET embedding_json = ?, embedding_model = ?, updated_at = ?
        WHERE id = ?
        """,
        (json.dumps(vector), embedding_model, utc_now_iso(), chunk_id),
    )
    conn.commit()


def list_embedded_chunks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT kc.id, kc.content, kc.metadata_json, kc.embedding_json, kc.embedding_model,
               kd.title, kd.source
        FROM knowledge_chunks kc
        JOIN knowledge_documents kd ON kd.id = kc.document_id
        WHERE kc.embedding_json IS NOT NULL
        """
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json"))
        item["embedding"] = json.loads(item.pop("embedding_json"))
        out.append(item)
    return out


def draft_items_total(draft: dict[str, Any]) -> int:
    return sum(int(item["quantity"]) * int(item["unit_price_cents"]) for item in draft["items"])


def draft_summary_text(draft: dict[str, Any]) -> str:
    if not draft["items"]:
        return "Sin productos confirmados todavia."
    parts = [f"{item['quantity']} x {item['name']}" for item in draft["items"]]
    address = draft.get("address") or (
        PICKUP_ADDRESS_TEXT if draft.get("delivery_type") == "pickup" else "sin direccion"
    )
    delivery_type = draft.get("delivery_type") or "sin modalidad"
    payment_method = draft.get("payment_method") or "sin metodo de pago"
    return (
        f"Pedido actual: {', '.join(parts)}. "
        f"Modalidad: {delivery_type}. "
        f"Direccion: {address}. "
        f"Pago: {payment_method}. "
        f"Total: Q{draft_items_total(draft) / 100:.2f}."
    )


def reset_draft(conn: sqlite3.Connection, session_id: str) -> None:
    save_draft(conn, session_id, empty_draft())


def short_order_code(order_id: str) -> str:
    return order_id[:8].upper()


def list_order_status_events(
    conn: sqlite3.Connection,
    order_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT status, note, created_at
        FROM order_status_events
        WHERE order_id = ?
        ORDER BY created_at ASC
        """,
        (order_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def append_order_status_event(
    conn: sqlite3.Connection,
    order_id: str,
    status: str,
    *,
    note: str | None = None,
    created_at: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO order_status_events (id, order_id, status, note, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            order_id,
            status,
            note,
            created_at or utc_now_iso(),
        ),
    )


def finalize_order_from_draft(conn: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    session = get_or_create_session(conn, session_id)
    draft = get_draft(conn, session_id)
    if not draft["items"]:
        return {"ok": False, "error": "No hay productos en el borrador del pedido."}
    if not (draft.get("customer_name") or session.get("customer_name")):
        return {"ok": False, "error": "Falta registrar el nombre del cliente."}
    if not (draft.get("customer_phone") or session.get("customer_phone")):
        return {"ok": False, "error": "Falta registrar el telefono del cliente."}
    if draft.get("delivery_type") == "delivery" and not draft.get("address"):
        return {"ok": False, "error": "Falta la direccion para entrega a domicilio."}
    if not draft.get("payment_method"):
        return {"ok": False, "error": "Falta registrar el metodo de pago."}

    try:
        conn.execute("BEGIN IMMEDIATE")
        catalog = product_catalog_map(conn)
        total_cents = 0
        for item in draft["items"]:
            product = catalog.get(item["product_id"])
            if not product:
                conn.rollback()
                return {"ok": False, "error": f"Producto no disponible: {item['name']}"}
            if int(item["quantity"]) > int(product["stock"]):
                conn.rollback()
                return {"ok": False, "error": f"Stock insuficiente para {product['name']}."}
            total_cents += int(item["quantity"]) * int(product["price_cents"])

        order_id = str(uuid.uuid4())
        payment_method = str(draft["payment_method"])
        address = draft.get("address")
        if draft.get("delivery_type") == "pickup" and not address:
            address = PICKUP_ADDRESS_TEXT
        payment_status = "paid" if payment_method in ("tarjeta", "transferencia", "paypal") else "pending_cash"
        conn.execute(
            """
            INSERT INTO orders (
                id, session_id, customer_name, customer_phone, delivery_type, address,
                notes, payment_method, payment_status, status, created_at, total_cents,
                source_channel
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'nuevo', ?, ?, ?)
            """,
            (
                order_id,
                session_id,
                draft.get("customer_name") or session.get("customer_name"),
                draft.get("customer_phone") or session.get("customer_phone"),
                draft["delivery_type"],
                address,
                draft.get("notes"),
                payment_method,
                payment_status,
                utc_now_iso(),
                total_cents,
                session["channel"],
            ),
            )

        append_order_status_event(
            conn,
            order_id,
            "nuevo",
            note="Pedido confirmado y enviado al flujo operativo.",
            created_at=utc_now_iso(),
        )

        for item in draft["items"]:
            product = catalog[item["product_id"]]
            conn.execute(
                """
                INSERT INTO order_lines (
                    id, order_id, product_id, product_name, quantity, unit_price_cents
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    order_id,
                    item["product_id"],
                    product["name"],
                    int(item["quantity"]),
                    int(product["price_cents"]),
                ),
            )
            conn.execute(
                "UPDATE products SET stock = stock - ? WHERE id = ?",
                (int(item["quantity"]), item["product_id"]),
            )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise

    update_session(
        conn,
        session_id,
        current_state="order_confirmed",
        status="completed",
        order_id=order_id,
        customer_name=draft.get("customer_name"),
        customer_phone=draft.get("customer_phone"),
    )
    order = get_order(conn, order_id)
    reset_draft(conn, session_id)
    return {"ok": True, "order": order}


def upgrade_seeded_knowledge(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT content FROM knowledge_documents WHERE id = 'policy-payment'"
    ).fetchone()
    if not row:
        return
    content = str(row["content"])
    if "PayPal" in content or "paypal" in content:
        return
    updated = (
        "Se aceptan pagos con efectivo, tarjeta, transferencia y PayPal. "
        "Los pedidos con tarjeta, transferencia o PayPal quedan marcados como pagados al confirmarse. "
        "Los pedidos en efectivo quedan pendientes de cobro para caja o reparto."
    )
    now = utc_now_iso()
    conn.execute(
        "UPDATE knowledge_documents SET content = ? WHERE id = 'policy-payment'",
        (updated,),
    )
    conn.execute(
        "UPDATE knowledge_chunks SET content = ?, updated_at = ? WHERE document_id = 'policy-payment'",
        (updated, now),
    )
    conn.commit()


def get_order(conn: sqlite3.Connection, order_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, session_id, customer_name, customer_phone, delivery_type, address,
               notes, payment_method, payment_status, status, created_at, total_cents,
               source_channel
        FROM orders
        WHERE id = ?
        """,
        (order_id,),
    ).fetchone()
    if not row:
        return None

    line_rows = conn.execute(
        """
        SELECT product_id, product_name, quantity, unit_price_cents
        FROM order_lines
        WHERE order_id = ?
        ORDER BY product_name
        """,
        (order_id,),
    ).fetchall()
    items = [dict(line) for line in line_rows]
    status_history = list_order_status_events(conn, order_id)
    return {
        **dict(row),
        "items": items,
        "tracking_code": short_order_code(order_id),
        "status_history": status_history,
        "summary": ", ".join(f"{item['quantity']} x {item['product_name']}" for item in items),
    }


def get_order_by_tracking_code(
    conn: sqlite3.Connection,
    tracking_code: str,
) -> dict[str, Any] | None:
    normalized = tracking_code.strip().upper()
    if not normalized:
        return None
    row = conn.execute(
        """
        SELECT id
        FROM orders
        WHERE UPPER(SUBSTR(id, 1, 8)) = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (normalized,),
    ).fetchone()
    if not row:
        return None
    return get_order(conn, row["id"])


def list_orders(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id
        FROM orders
        ORDER BY created_at DESC
        LIMIT 75
        """
    ).fetchall()
    return [order for row in rows if (order := get_order(conn, row["id"]))]


def update_order_status(
    conn: sqlite3.Connection,
    order_id: str,
    status: str,
) -> dict[str, Any]:
    allowed = set(ORDER_STATUSES)
    if status not in allowed:
        return {"ok": False, "error": f"Estado invalido: {status}"}
    row = conn.execute(
        "SELECT id, status FROM orders WHERE id = ?",
        (order_id,),
    ).fetchone()
    if not row:
        return {"ok": False, "error": f"Pedido no encontrado: {order_id}"}
    previous_status = str(row["status"])
    if previous_status == status:
        return {"ok": True, "order": get_order(conn, order_id)}
    conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
    append_order_status_event(
        conn,
        order_id,
        status,
        note=f"Estado actualizado de {previous_status} a {status}.",
    )
    conn.commit()
    return {"ok": True, "order": get_order(conn, order_id)}


def update_order_payment_status(
    conn: sqlite3.Connection,
    order_id: str,
    payment_status: str,
) -> dict[str, Any]:
    if payment_status not in PAYMENT_STATUSES:
        return {"ok": False, "error": f"Estado de pago invalido: {payment_status}"}
    row = conn.execute(
        "SELECT id, payment_status FROM orders WHERE id = ?",
        (order_id,),
    ).fetchone()
    if not row:
        return {"ok": False, "error": f"Pedido no encontrado: {order_id}"}
    previous_status = str(row["payment_status"])
    if previous_status == payment_status:
        return {"ok": True, "order": get_order(conn, order_id)}
    conn.execute(
        "UPDATE orders SET payment_status = ? WHERE id = ?",
        (payment_status, order_id),
    )
    append_order_status_event(
        conn,
        order_id,
        str(conn.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()["status"]),
        note=f"Pago actualizado de {previous_status} a {payment_status}.",
    )
    conn.commit()
    return {"ok": True, "order": get_order(conn, order_id)}


def operations_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    orders = list_orders(conn)
    return {
        "orders": orders,
        "cashier_queue": [
            order
            for order in orders
            if order["payment_status"] == "pending_cash" and order["status"] != "entregado"
        ],
        "active_orders": [
            order
            for order in orders
            if order["status"] not in ("entregado", "cancelado")
        ],
    }


def tool_envelope(kind: str, **payload: Any) -> str:
    body = {"kind": kind, **payload}
    return json.dumps(body, ensure_ascii=False)


def format_draft_tool_result(draft: dict[str, Any], *, message: str) -> str:
    return tool_envelope(
        "draft",
        message=message,
        draft=draft,
        total_cents=draft_items_total(draft),
    )


def format_retrieval_tool_result(
    *,
    query: str,
    hits: list[dict[str, Any]],
    message: str,
) -> str:
    return tool_envelope(
        "retrieval",
        message=message,
        query=query,
        hits=hits,
    )


def format_order_tool_result(order: dict[str, Any], *, message: str) -> str:
    return tool_envelope(
        "order",
        message=message,
        order=order,
        total_cents=order["total_cents"],
    )


def format_error_tool_result(error: str) -> str:
    return tool_envelope("error", message=error, error=error)
