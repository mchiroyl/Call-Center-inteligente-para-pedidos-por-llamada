"""Browser-based simulated voice call WebSocket."""

import asyncio
import contextlib
import logging
from typing import AsyncIterator

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from assemblyai_stt import AssemblyAISTT
from events import event_to_dict
from rate_limit import enforce_rate_limit
from security import client_ip_from_websocket
from voice_pipeline import build_web_voice_pipeline

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    client_id = client_ip_from_websocket(websocket)
    enforce_rate_limit(
        f"voice-ws:{client_id}",
        limit=12,
        window_seconds=60,
    )
    await websocket.accept()
    logger.info("Web simulation voice connected: %s", client_id)
    stt = AssemblyAISTT(sample_rate=16000)

    async def prewarm_stt() -> None:
        try:
            await stt._ensure_connection()
            logger.info("AssemblyAI STT ready.")
        except Exception:
            logger.warning("AssemblyAI STT prewarm failed; retrying on first audio.")

    async def websocket_audio_stream() -> AsyncIterator[bytes]:
        try:
            while True:
                try:
                    data = await websocket.receive_bytes()
                except RuntimeError:
                    return
                yield data
        except WebSocketDisconnect:
            return

    pipeline = build_web_voice_pipeline(stt)
    output_stream = pipeline.atransform(websocket_audio_stream())
    prewarm_task = asyncio.create_task(prewarm_stt())

    try:
        await websocket.send_json({"type": "ready"})
        async for event in output_stream:
            event = event  # type: VoiceAgentEvent
            if getattr(event, "type", None) in {"stt_chunk", "stt_output"}:
                logger.info(
                    "Web simulation %s transcript: %s",
                    event.type,
                    getattr(event, "transcript", None),
                )
            try:
                await websocket.send_json(event_to_dict(event))
            except (RuntimeError, WebSocketDisconnect):
                break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("Voice pipeline failed")
        with contextlib.suppress(Exception):
            await websocket.send_json(
                {
                    "type": "error",
                    "message": (
                        "La llamada simulada se interrumpio. "
                        "Presione hablar para reanudar."
                    ),
                }
            )
            await websocket.close(code=1011, reason=str(exc)[:120])
    finally:
        prewarm_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await prewarm_task
