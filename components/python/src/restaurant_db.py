"""
SQLite persistence for menu stock, per-session carts, and confirmed orders.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _demo_root() -> Path:
    # components/python/src -> …/voice-sandwich-demo
    return Path(__file__).resolve().parent.parent.parent.parent


def db_path() -> Path:
    raw = os.environ.get("RESTAURANT_DB_PATH", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return _demo_root() / "data" / "restaurant.db"


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def normalize_product_id(raw: str) -> str:
    """Strip whitespace and trailing punctuation often appended by LLMs."""
    s = raw.strip()
    while s and s[-1] in ".:,;":
        s = s[:-1].rstrip()
    return s


def bootstrap_db() -> None:
    """Create schema and seed once; closes connection (safe across threads)."""
    conn = connect()
    try:
        init_db(conn)
        seed_demo_if_empty(conn)
    finally:
        conn.close()


@contextmanager
def db_session():
    """One SQLite connection per use (LangChain runs tools in thread pools)."""


def _demo_root() -> Path:
    # components/python/src -> …/voice-sandwich-demo
    return Path(__file__).resolve().parent.parent.parent.parent


def db_path() -> Path:
    raw = os.environ.get("RESTAURANT_DB_PATH", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return _demo_root() / "data" / "restaurant.db"


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def normalize_product_id(raw: str) -> str:
    """Strip whitespace and trailing punctuation often appended by LLMs."""
    s = raw.strip()
    while s and s[-1] in ".:,;":
        s = s[:-1].rstrip()
    return s


def bootstrap_db() -> None:
    """Create schema and seed once; closes connection (safe across threads)."""
    conn = connect()
    try:
        init_db(conn)
        seed_demo_if_empty(conn)
    finally:
        conn.close()


@contextmanager
def db_session():
    """One SQLite connection per use (LangChain runs tools in thread pools)."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS products (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            image_url TEXT NOT NULL,
            stock INTEGER NOT NULL CHECK (stock >= 0),
            price_cents INTEGER NOT NULL CHECK (price_cents >= 0)
        );

        CREATE TABLE IF NOT EXISTS cart_lines (
            session_id TEXT NOT NULL,
            product_id TEXT NOT NULL REFERENCES products(id),
            quantity INTEGER NOT NULL CHECK (quantity > 0),
            PRIMARY KEY (session_id, product_id)
        );

        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            payment_method TEXT
        );

        CREATE TABLE IF NOT EXISTS order_lines (
            order_id TEXT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            product_id TEXT NOT NULL,
            product_name TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK (quantity > 0),
            unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
            PRIMARY KEY (order_id, product_id)
        );
        """
    )
    # Migrate existing DBs that lack the payment_method column
    cols = {row[1] for row in conn.execute("PRAGMA table_info(orders)").fetchall()}
    if "payment_method" not in cols:
        conn.execute("ALTER TABLE orders ADD COLUMN payment_method TEXT")
    conn.commit()


def seed_demo_if_empty(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()
    if row and row["c"] > 0:
        return
    # Royalty-free hotlink-friendly URLs (Unsplash); static IDs for stable tool calls.
    menu: list[tuple[str, str, str, str, int, int]] = [
        (
            "bread-ciabatta",
            "Pan ciabatta",
            "pan",
            "https://images.unsplash.com/photo-1549931319-a545dcf3bc73?w=400&q=80",
            40,
            150,
        ),
        (
            "meat-turkey",
            "Pavo",
            "carne",
            "https://images.unsplash.com/photo-1604503468506-a8da13d82791?w=400&q=80",
            25,
            350,
        ),
        (
            "meat-chicken",
            "Pollo",
            "carne",
            "https://images.unsplash.com/photo-1598515214211-89d3c73ae83b?w=400&q=80",
            30,
            320,
        ),
        (
            "meat-pork",
            "Cerdo",
            "carne",
            "https://images.unsplash.com/photo-1544025162-d76694265947?w=400&q=80",
            20,
            340,
        ),
        (
            "cheese-cheddar",
            "Queso cheddar",
            "queso",
            "https://images.unsplash.com/photo-1486297678162-eb2a19b0a32d?w=400&q=80",
            35,
            120,
        ),
        (
            "cheese-mozzarella",
            "Queso mozzarella",
            "queso",
            "https://images.unsplash.com/photo-1618164436261-4473940d1f5c?w=400&q=80",
            35,
            130,
        ),
        (
            "veg-lettuce",
            "Lechuga",
            "vegetal",
            "https://images.unsplash.com/photo-1622206151226-18ca2c9ab4a1?w=400&q=80",
            50,
            80,
        ),
        (
            "veg-tomato",
            "Tomate",
            "vegetal",
            "https://images.unsplash.com/photo-1546470427-e262649bba83?w=400&q=80",
            45,
            70,
        ),
        (
            "condiment-mayo",
            "Mayonesa",
            "salsa",
            "https://images.unsplash.com/photo-1589189288044-d00708f73514?w=400&q=80",
            60,
            50,
        ),
    ]
    conn.executemany(
        """
        INSERT INTO products (id, name, category, image_url, stock, price_cents)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        menu,
    )
    conn.commit()


def list_available_products(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, name, category, image_url, stock, price_cents
        FROM products
        WHERE stock > 0
        ORDER BY category, name
        """
    ).fetchall()
    return [dict(r) for r in rows]


def list_all_products(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """All products including zero stock (admin)."""
    rows = conn.execute(
        """
        SELECT id, name, category, image_url, stock, price_cents
        FROM products
        ORDER BY category, name
        """
    ).fetchall()
    return [dict(r) for r in rows]


def adjust_product_stock(
    conn: sqlite3.Connection,
    product_id: str,
    action: str,
    amount: int = 1,
) -> dict[str, Any]:
    """Adjust stock: increment, decrement (floor 0), or zero."""
    row = conn.execute(
        "SELECT id, name, stock FROM products WHERE id = ?", (product_id,)
    ).fetchone()
    if not row:
        return {"ok": False, "error": f"Unknown product: {product_id}"}

    stock = int(row["stock"])
    step = max(1, min(amount, 999))

    if action == "zero":
        new_stock = 0
    elif action == "increment":
        new_stock = stock + step
    elif action == "decrement":
        new_stock = max(0, stock - step)
    else:
        return {"ok": False, "error": f"Invalid action: {action}"}

    conn.execute(
        "UPDATE products SET stock = ? WHERE id = ?",
        (new_stock, product_id),
    )
    conn.commit()
    return {
        "ok": True,
        "product_id": product_id,
        "name": row["name"],
        "stock": new_stock,
    }


def _cart_lines_with_products(
    conn: sqlite3.Connection, session_id: str
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT cl.product_id AS product_id, p.name AS name, p.image_url AS image_url,
               p.stock AS stock_available, p.price_cents AS unit_price_cents,
               cl.quantity AS quantity
        FROM cart_lines cl
        JOIN products p ON p.id = cl.product_id
        WHERE cl.session_id = ?
        ORDER BY p.name
        """,
        (session_id,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["line_total_cents"] = d["unit_price_cents"] * d["quantity"]
        out.append(d)
    return out


def get_cart_view(conn: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    lines = _cart_lines_with_products(conn, session_id)
    total = sum(x["line_total_cents"] for x in lines)
    return {"lines": lines, "total_cents": total}


def add_cart_line(
    conn: sqlite3.Connection, session_id: str, product_id: str, quantity: int
) -> dict[str, Any]:
    if quantity < 1:
        return {"ok": False, "error": "La cantidad debe ser al menos 1."}

    product_id = normalize_product_id(product_id)

    row = conn.execute(
        "SELECT id, name, stock FROM products WHERE id = ?", (product_id,)
    ).fetchone()
    if not row:
        return {"ok": False, "error": f"Producto desconocido: {product_id}."}

    cur_qty_row = conn.execute(
        """
        SELECT quantity FROM cart_lines
        WHERE session_id = ? AND product_id = ?
        """,
        (session_id, product_id),
    ).fetchone()
    cur_qty = int(cur_qty_row["quantity"]) if cur_qty_row else 0
    new_qty = cur_qty + quantity
    if new_qty > int(row["stock"]):
        return {
            "ok": False,
            "error": (
                f"No hay suficiente stock de {row['name']}: "
                f"disponible {row['stock']}, en carrito {cur_qty}, "
                f"pediste agregar {quantity}."
            ),
        }

    conn.execute(
        """
        INSERT INTO cart_lines (session_id, product_id, quantity)
        VALUES (?, ?, ?)
        ON CONFLICT(session_id, product_id) DO UPDATE SET quantity = excluded.quantity
        """,
        (session_id, product_id, new_qty),
    )
    conn.commit()
    return {"ok": True}


def confirm_order_transaction(
    conn: sqlite3.Connection, session_id: str
) -> dict[str, Any]:
    lines = _cart_lines_with_products(conn, session_id)
    if not lines:
        return {
            "ok": False,
            "error": "El carrito está vacío; no hay nada que confirmar.",
        }

    try:
        conn.execute("BEGIN IMMEDIATE")
        # Re-read stock under transaction
        for line in lines:
            stock_row = conn.execute(
                "SELECT stock, name FROM products WHERE id = ?", (line["product_id"],)
            ).fetchone()
            if not stock_row or line["quantity"] > int(stock_row["stock"]):
                conn.rollback()
                name = stock_row["name"] if stock_row else line["product_id"]
                return {
                    "ok": False,
                    "error": f"Stock insuficiente para {name} al confirmar.",
                }

        order_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        conn.execute(
            (
                "INSERT INTO orders (id, session_id, status, created_at) "
                "VALUES (?, ?, ?, ?)"
            ),
            (order_id, session_id, "nuevo", now),
        )
        for line in lines:
            conn.execute(
                """
                INSERT INTO order_lines
                    (order_id, product_id, product_name, quantity, unit_price_cents)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    line["product_id"],
                    line["name"],
                    line["quantity"],
                    line["unit_price_cents"],
                ),
            )
            conn.execute(
                """
                UPDATE products SET stock = stock - ? WHERE id = ?
                """,
                (line["quantity"], line["product_id"]),
            )
        conn.execute("DELETE FROM cart_lines WHERE session_id = ?", (session_id,))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise

    summary_parts = [f"{line['quantity']} x {line['name']}" for line in lines]
    return {
        "ok": True,
        "order_id": order_id,
        "summary": ", ".join(summary_parts),
        "total_cents": sum(x["line_total_cents"] for x in lines),
    }


def get_order(conn: sqlite3.Connection, order_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, session_id, status, created_at, payment_method
        FROM orders
        WHERE id = ?
        """,
        (order_id,),
    ).fetchone()
    if not row:
        return None

    lines = conn.execute(
        """
        SELECT product_id, product_name, quantity, unit_price_cents
        FROM order_lines
        WHERE order_id = ?
        ORDER BY product_name
        """,
        (order_id,),
    ).fetchall()
    items = [dict(line) for line in lines]
    total_cents = sum(item["quantity"] * item["unit_price_cents"] for item in items)
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "status": row["status"],
        "created_at": row["created_at"],
        "payment_method": row["payment_method"],
        "items": items,
        "summary": ", ".join(
            f"{item['quantity']} x {item['product_name']}" for item in items
        ),
        "total_cents": total_cents,
    }


def save_payment_method(
    conn: sqlite3.Connection, order_id: str, method: str
) -> None:
    """Persist the payment method chosen by the customer."""
    conn.execute(
        "UPDATE orders SET payment_method = ? WHERE id = ?",
        (method, order_id),
    )
    conn.commit()


def list_orders(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id
        FROM orders
        ORDER BY created_at DESC
        LIMIT 50
        """
    ).fetchall()
    return [order for row in rows if (order := get_order(conn, row["id"]))]


def update_order_status(
    conn: sqlite3.Connection, order_id: str, status: str
) -> dict[str, Any]:
    allowed = {"nuevo", "en_preparacion", "listo"}
    if status not in allowed:
        return {"ok": False, "error": f"Estado invalido: {status}"}

    row = conn.execute("SELECT id FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not row:
        return {"ok": False, "error": f"Pedido no encontrado: {order_id}"}

    conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
    conn.commit()
    order = get_order(conn, order_id)
    return {"ok": True, "order": order}


def tool_envelope(
    kind: str,
    *,
    message: str,
    lines: list[dict[str, Any]] | None = None,
    menu: list[dict[str, Any]] | None = None,
    order: dict[str, Any] | None = None,
    total_cents: int | None = None,
    error: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "kind": kind,
        "message": message,
        "lines": lines,
        "menu": menu,
        "order": order,
        "total_cents": total_cents,
        "error": error,
    }
    return json.dumps(payload, ensure_ascii=False)


def format_cart_tool_result(
    conn: sqlite3.Connection, session_id: str, message: str
) -> str:
    view = get_cart_view(conn, session_id)
    lines_out = [
        {
            "product_id": x["product_id"],
            "name": x["name"],
            "quantity": x["quantity"],
            "image_url": x["image_url"],
            "unit_price_cents": x["unit_price_cents"],
            "line_total_cents": x["line_total_cents"],
        }
        for x in view["lines"]
    ]
    return tool_envelope(
        "cart",
        message=message,
        lines=lines_out,
        total_cents=view["total_cents"],
        order=None,
    )


def format_menu_tool_result(conn: sqlite3.Connection, message: str) -> str:
    items = list_available_products(conn)
    slim = [
        {
            "id": x["id"],
            "name": x["name"],
            "category": x["category"],
            "stock": x["stock"],
            "image_url": x["image_url"],
            "price_cents": x["price_cents"],
        }
        for x in items
    ]
    return tool_envelope("menu", message=message, menu=slim)


def format_order_tool_result(
    order_id: str, summary: str, total_cents: int, message: str
) -> str:
    return tool_envelope(
        "order",
        message=message,
        lines=[],
        total_cents=total_cents,
        order={"order_id": order_id, "status": "nuevo", "summary": summary},
    )


def format_error_tool_result(error: str) -> str:
    return tool_envelope("error", message=error, error=error)


def format_payment_tool_result(
    order_id: str, method: str, total_cents: int, message: str
) -> str:
    """Envelope for a completed payment simulation."""
    return tool_envelope(
        "payment_complete",
        message=message,
        lines=[],
        total_cents=total_cents,
        order={
            "order_id": order_id,
            "status": "en_preparacion",
            "summary": method,
            "payment_method": method,
        },
    )
