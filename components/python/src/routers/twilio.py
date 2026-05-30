"""Twilio webhooks and Media Streams WebSocket routes."""

import os
import uuid
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request, Response, WebSocket, status

import call_center_db
from rate_limit import enforce_rate_limit
from security import (
    client_ip_from_request,
    client_ip_from_websocket,
    mask_phone,
    validate_twilio_request,
)
from twilio_gateway.media_stream import handle_twilio_media_stream
from twilio_gateway.security import make_stream_token
from twilio_gateway.twiml import build_connect_stream_twiml, build_message_twiml

router = APIRouter()


def twiml_response(xml_body: str) -> Response:
    return Response(content=xml_body, media_type="application/xml")


async def parse_twilio_form(request: Request) -> dict[str, str]:
    body = await request.body()
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    return {key: values[0] if values else "" for key, values in parsed.items()}


def _public_url_for_request(request: Request, path: str) -> str:
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    ws_proto = "wss" if proto == "https" else "ws"
    return f"{ws_proto}://{host}{path}"


@router.post("/twilio/voice")
async def twilio_voice_entry(request: Request):
    form = await parse_twilio_form(request)
    await validate_twilio_request(request, form)
    enforce_rate_limit(
        f"twilio-voice:{client_ip_from_request(request)}",
        limit=60,
        window_seconds=60,
    )
    secret = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="TWILIO_AUTH_TOKEN es requerido para Twilio Media Streams.",
        )

    call_sid = str(form.get("CallSid") or uuid.uuid4())
    from_number = str(form.get("From") or "")
    to_number = str(form.get("To") or "")
    stream_url = _public_url_for_request(request, "/twilio/media-stream")
    stream_token = make_stream_token(call_sid, secret=secret)

    with call_center_db.db_session() as conn:
        call_center_db.get_or_create_session(conn, call_sid, channel="twilio-voice")
        if from_number:
            call_center_db.update_session(conn, call_sid, customer_phone=from_number)
            draft = call_center_db.get_draft(conn, call_sid)
            if not draft.get("customer_phone"):
                draft["customer_phone"] = from_number
                call_center_db.save_draft(conn, call_sid, draft)
        call_center_db.log_workflow_event(
            conn,
            call_sid,
            "twilio_voice_entry",
            {"from": mask_phone(from_number), "to": mask_phone(to_number)},
        )

    return twiml_response(
        build_connect_stream_twiml(
            websocket_url=stream_url,
            call_sid=call_sid,
            stream_token=stream_token,
        )
    )


@router.websocket("/twilio/media-stream")
async def twilio_media_stream(websocket: WebSocket):
    enforce_rate_limit(
        f"twilio-media:{client_ip_from_websocket(websocket)}",
        limit=12,
        window_seconds=60,
    )
    await handle_twilio_media_stream(websocket)


@router.post("/twilio/status")
async def twilio_status_callback(request: Request):
    form = await parse_twilio_form(request)
    await validate_twilio_request(request, form)
    enforce_rate_limit(
        f"twilio-status:{client_ip_from_request(request)}",
        limit=240,
        window_seconds=60,
    )
    call_sid = str(form.get("CallSid") or "")
    call_status = str(form.get("CallStatus") or "")
    if call_sid:
        with call_center_db.db_session() as conn:
            call_center_db.get_or_create_session(conn, call_sid, channel="twilio-voice")
            call_center_db.log_workflow_event(
                conn,
                call_sid,
                "twilio_status_callback",
                {
                    "call_status": call_status,
                    "from": mask_phone(str(form.get("From") or "")),
                    "to": mask_phone(str(form.get("To") or "")),
                },
            )
    return Response(status_code=204)


@router.post("/twilio/message")
async def twilio_message_reply(request: Request):
    form = await parse_twilio_form(request)
    await validate_twilio_request(request, form)
    enforce_rate_limit(
        f"twilio-message:{client_ip_from_request(request)}",
        limit=60,
        window_seconds=60,
    )
    xml = build_message_twiml(
        "Este proyecto esta configurado para llamadas de voz. "
        "Por favor llame al numero para realizar su pedido."
    )
    return twiml_response(xml)
