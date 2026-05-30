"""Menu, order and operations routes."""

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket
from fastapi.responses import HTMLResponse
from starlette.websockets import WebSocketDisconnect

import call_center_db
from event_hub import kitchen_events
from rate_limit import enforce_rate_limit
from schemas import OrderStatusBody, PaymentStatusBody
from security import (
    client_ip_from_request,
    client_ip_from_websocket,
    ensure_status_transition_allowed,
    operations_ws_key_valid,
    require_staff_roles,
)
from settings import TRACK_HTML_PATH
from workflow import get_workflow

router = APIRouter()


@router.get("/track/{tracking_code}", response_class=HTMLResponse)
async def track_page(tracking_code: str):
    if not TRACK_HTML_PATH.is_file():
        raise HTTPException(status_code=500, detail="track.html missing")
    return HTMLResponse(content=TRACK_HTML_PATH.read_text(encoding="utf-8"))


@router.get("/api/menu")
def api_list_menu():
    with call_center_db.db_session() as conn:
        return call_center_db.list_available_products(conn)


@router.get("/api/kitchen/orders")
def api_kitchen_orders(request: Request):
    require_staff_roles(
        request,
        roles={"admin", "cocina", "caja", "operaciones"},
        allow_operations_key=True,
    )
    with call_center_db.db_session() as conn:
        return call_center_db.list_orders(conn)


@router.get("/api/orders/{tracking_code}")
def api_order_tracking(tracking_code: str):
    with call_center_db.db_session() as conn:
        order = call_center_db.get_order_by_tracking_code(conn, tracking_code)
    if not order:
        raise HTTPException(status_code=404, detail="Pedido no encontrado.")
    return order


@router.get("/api/operations/overview")
def api_operations_overview(request: Request):
    require_staff_roles(
        request,
        roles={"admin", "cocina", "caja", "operaciones"},
        allow_operations_key=True,
    )
    with call_center_db.db_session() as conn:
        return call_center_db.operations_snapshot(conn)


@router.get("/api/knowledge/search")
def api_knowledge_search(q: str = Query(default="", min_length=1)):
    with call_center_db.db_session() as conn:
        hits = get_workflow().retriever.search(conn, q, k=5)
    return {"query": q, "hits": hits}


@router.post("/api/kitchen/orders/{order_id}/status")
async def api_update_order_status(
    order_id: str,
    body: OrderStatusBody,
    request: Request,
):
    user = require_staff_roles(
        request,
        roles={"admin", "cocina", "operaciones"},
        allow_operations_key=True,
    )
    enforce_rate_limit(
        f"ops-status:{client_ip_from_request(request)}",
        limit=40,
        window_seconds=60,
    )
    with call_center_db.db_session() as conn:
        current = call_center_db.get_order(conn, order_id)
        if not current:
            raise HTTPException(status_code=404, detail="Pedido no encontrado.")
        ensure_status_transition_allowed(
            role=str(user["role"]),
            current_status=str(current["status"]),
            next_status=body.status,
        )
        result = call_center_db.update_order_status(conn, order_id, body.status)
        if not result["ok"]:
            raise HTTPException(status_code=404, detail=result["error"])
        order = result["order"]
    await kitchen_events.publish({"type": "order_updated", "order": order})
    return order


@router.post("/api/kitchen/orders/{order_id}/payment-status")
async def api_update_order_payment_status(
    order_id: str,
    body: PaymentStatusBody,
    request: Request,
):
    require_staff_roles(
        request,
        roles={"admin", "caja", "operaciones"},
        allow_operations_key=True,
    )
    enforce_rate_limit(
        f"ops-payment:{client_ip_from_request(request)}",
        limit=40,
        window_seconds=60,
    )
    with call_center_db.db_session() as conn:
        result = call_center_db.update_order_payment_status(
            conn,
            order_id,
            body.payment_status,
        )
        if not result["ok"]:
            raise HTTPException(status_code=404, detail=result["error"])
        order = result["order"]
    await kitchen_events.publish({"type": "order_updated", "order": order})
    return order


@router.websocket("/kitchen/ws")
async def kitchen_websocket(websocket: WebSocket):
    enforce_rate_limit(
        f"ops-ws:{client_ip_from_websocket(websocket)}",
        limit=20,
        window_seconds=60,
    )
    if not operations_ws_key_valid(websocket):
        await websocket.close(code=4401)
        return
    await kitchen_events.connect(websocket)
    try:
        with call_center_db.db_session() as conn:
            await websocket.send_json(
                {"type": "snapshot", "orders": call_center_db.list_orders(conn)}
            )
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await kitchen_events.disconnect(websocket)
