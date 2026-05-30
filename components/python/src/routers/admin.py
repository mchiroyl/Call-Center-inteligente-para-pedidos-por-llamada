"""Admin HTML and inventory routes."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasicCredentials

import call_center_db
from event_hub import kitchen_events
from rate_limit import enforce_rate_limit
from schemas import ResetDemoBody, StockAdjustBody
from security import (
    admin_basic_credentials,
    basic_security,
    client_ip_from_request,
    ensure_admin_authenticated,
    get_staff_user_from_request,
    verify_admin_access,
)
from settings import ADMIN_HTML_PATH, LOGIN_HTML_PATH

router = APIRouter()


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(basic_security),
):
    user = get_staff_user_from_request(request)
    if user and user.get("role") == "admin" and int(user.get("is_active", 0)) == 1:
        pass
    elif admin_basic_credentials() is not None:
        verify_admin_access(credentials)
    else:
        return RedirectResponse(url="/login?next=/admin", status_code=303)
    if not ADMIN_HTML_PATH.is_file():
        raise HTTPException(status_code=500, detail="admin.html missing")
    return HTMLResponse(content=ADMIN_HTML_PATH.read_text(encoding="utf-8"))


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    if not LOGIN_HTML_PATH.is_file():
        raise HTTPException(status_code=500, detail="login.html missing")
    return HTMLResponse(content=LOGIN_HTML_PATH.read_text(encoding="utf-8"))


@router.get("/api/admin/products")
def api_admin_list_products(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(basic_security),
):
    ensure_admin_authenticated(request, credentials)
    enforce_rate_limit(
        f"admin-products:{client_ip_from_request(request)}",
        limit=60,
        window_seconds=60,
    )
    with call_center_db.db_session() as conn:
        return call_center_db.list_all_products(conn)


@router.post("/api/admin/products/{product_id}/stock")
def api_admin_adjust_stock(
    product_id: str,
    body: StockAdjustBody,
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(basic_security),
):
    ensure_admin_authenticated(request, credentials)
    enforce_rate_limit(
        f"admin-stock:{client_ip_from_request(request)}",
        limit=30,
        window_seconds=60,
    )
    with call_center_db.db_session() as conn:
        product = call_center_db.get_product(conn, product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Producto no encontrado.")
        stock = int(product["stock"])
        amount = max(1, min(body.amount, 999))
        if body.action == "zero":
            new_stock = 0
        elif body.action == "increment":
            new_stock = stock + amount
        else:
            new_stock = max(0, stock - amount)
        conn.execute(
            "UPDATE products SET stock = ? WHERE id = ?",
            (new_stock, product_id),
        )
        conn.commit()
        return {"ok": True, "product_id": product_id, "stock": new_stock}


@router.post("/api/admin/reset-demo")
async def api_admin_reset_demo(
    body: ResetDemoBody,
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(basic_security),
):
    ensure_admin_authenticated(request, credentials)
    enforce_rate_limit(
        f"admin-reset:{client_ip_from_request(request)}",
        limit=5,
        window_seconds=300,
    )
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="Confirme el reinicio enviando confirm=true.",
        )

    with call_center_db.db_session() as conn:
        result = call_center_db.reset_demo_state(conn)
        snapshot = call_center_db.operations_snapshot(conn)
        products = call_center_db.list_all_products(conn)

    await kitchen_events.publish({"type": "snapshot", "orders": snapshot["orders"]})
    return {
        "ok": True,
        "message": "Estado de demo reiniciado.",
        "result": result,
        "products": products,
        "snapshot": snapshot,
    }
