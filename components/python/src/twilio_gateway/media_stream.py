"""Twilio Media Streams message builders and WebSocket bridge."""

import asyncio
import contextlib
import logging
import os
from typing import Any, AsyncIterator

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect
from twilio.rest import Client

import call_center_db
from assemblyai_stt import AssemblyAISTT
from cartesia_tts import CartesiaTTS
from events import AgentChunkEvent, AgentEndEvent
from openai_tts import OpenAITTS
from settings import VOICE_GREETING
from twilio_gateway.audio import (
    decode_twilio_media_payload,
    encode_twilio_media_payload,
)
from twilio_gateway.security import validate_stream_token
from voice_pipeline import agent_stream, stt_stream, tts_stream

logger = logging.getLogger(__name__)


def _farewell_hangup_delay_s(text: str) -> float:
    estimated = max(3.0, min(10.0, (len(text) / 14.0) + 1.5))
    return estimated


async def _hangup_twilio_call(call_sid: str, reason: str) -> None:
    account_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    if not account_sid or not auth_token or not call_sid:
        raise RuntimeError("Missing Twilio credentials or call SID for hangup")

    client = Client(account_sid, auth_token)
    await asyncio.to_thread(client.calls(call_sid).update, status="completed")
    logger.info("Twilio call hangup requested via REST: call_sid=%s reason=%s", call_sid, reason)


class FallbackTTS:
    def __init__(self, providers: list[object]) -> None:
        self.providers = providers
        self.active: object | None = None

    async def prepare(self) -> None:
        last_exc: Exception | None = None
        for provider in self.providers:
            try:
                await provider.prepare()
                self.active = provider
                logger.info(
                    "FallbackTTS using provider %s",
                    provider.__class__.__name__,
                )
                return
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "FallbackTTS provider failed during prepare: %s (%s)",
                    provider.__class__.__name__,
                    exc,
                )
        raise RuntimeError("No TTS provider available") from last_exc

    async def send_text(self, text: str | None) -> None:
        if self.active is None:
            raise RuntimeError("FallbackTTS has no active provider")
        await self.active.send_text(text)

    async def receive_events(self) -> AsyncIterator[Any]:
        if self.active is None:
            raise RuntimeError("FallbackTTS has no active provider")
        async for event in self.active.receive_events():
            yield event

    async def close(self) -> None:
        for provider in self.providers:
            with contextlib.suppress(Exception):
                await provider.close()


def build_twilio_media_message(stream_sid: str, audio_chunk: bytes) -> dict:
    """Build a Twilio outbound media message for raw mu-law audio bytes."""
    return {
        "event": "media",
        "streamSid": stream_sid,
        "media": {"payload": encode_twilio_media_payload(audio_chunk)},
    }


def build_twilio_mark_message(stream_sid: str, name: str) -> dict:
    return {
        "event": "mark",
        "streamSid": stream_sid,
        "mark": {"name": name},
    }


def build_twilio_clear_message(stream_sid: str) -> dict:
    return {
        "event": "clear",
        "streamSid": stream_sid,
    }


async def handle_twilio_media_stream(websocket: WebSocket) -> None:
    """Bridge a Twilio bidirectional Media Stream to the voice pipeline."""
    await websocket.accept()
    logger.info("Twilio Media Stream websocket accepted")
    audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    output_task: asyncio.Task[None] | None = None
    hangup_task: asyncio.Task[None] | None = None
    call_sid = ""
    stream_sid = ""

    async def audio_stream() -> AsyncIterator[bytes]:
        buffer = bytearray()
        min_payload_bytes = 800  # 50 ms of 16-bit PCM at 8 kHz
        while True:
            chunk = await audio_queue.get()
            if chunk is None:
                if buffer:
                    if len(buffer) < min_payload_bytes:
                        buffer.extend(b"\x00" * (min_payload_bytes - len(buffer)))
                    yield bytes(buffer)
                return
            buffer.extend(chunk)
            while len(buffer) >= min_payload_bytes:
                yield bytes(buffer[:min_payload_bytes])
                buffer = buffer[min_payload_bytes:]

    async def twilio_events() -> AsyncIterator[Any]:
        yield AgentChunkEvent.create(VOICE_GREETING)
        yield AgentEndEvent.create()
        stt = AssemblyAISTT(sample_rate=8000)
        async for event in stt_stream(audio_stream(), stt=stt):
            if event.type == "stt_chunk":
                logger.info(
                    "Twilio STT partial for call %s: %s",
                    call_sid or "unknown",
                    event.transcript,
                )
            elif event.type == "stt_output":
                logger.info(
                    "Twilio STT final for call %s: %s",
                    call_sid or "unknown",
                    event.transcript,
                )
            yield event

    async def forward_output() -> None:
        nonlocal hangup_task
        mark_counter = 0
        last_agent_text = ""
        tts = FallbackTTS(
            [
                CartesiaTTS(sample_rate=8000, encoding="pcm_mulaw"),
                OpenAITTS(sample_rate=8000, encoding="pcm_mulaw"),
            ]
        )
        try:
            pipeline = tts_stream(
                agent_stream(
                    twilio_events(),
                    thread_id=call_sid,
                    channel_label="TWILIO",
                ),
                tts=tts,
            )
            async for event in pipeline:
                if event.type == "agent_chunk":
                    last_agent_text = event.text
                    logger.info(
                        "Twilio agent chunk for call %s: %s",
                        call_sid or "unknown",
                        event.text,
                    )
                    continue
                if event.type == "agent_end":
                    logger.info("Twilio agent finished response for call %s", call_sid or "unknown")
                    continue
                if event.type == "call_control" and event.action == "hangup":
                    delay_s = _farewell_hangup_delay_s(last_agent_text)
                    hangup_reason = event.reason
                    logger.info(
                        "Twilio call control received for call %s: action=%s reason=%s delay=%.1fs",
                        call_sid or "unknown",
                        event.action,
                        hangup_reason,
                        delay_s,
                    )

                    async def hangup_after_delay() -> None:
                        await asyncio.sleep(delay_s)
                        try:
                            await _hangup_twilio_call(call_sid, hangup_reason)
                        except Exception:
                            logger.exception(
                                "Twilio REST hangup failed for call %s; closing websocket instead",
                                call_sid or "unknown",
                            )
                            with contextlib.suppress(Exception):
                                await websocket.close()

                    if hangup_task is not None and not hangup_task.done():
                        hangup_task.cancel()
                    hangup_task = asyncio.create_task(hangup_after_delay())
                    continue
                if event.type != "tts_chunk":
                    continue
                try:
                    audio_bytes = event.audio
                    logger.info(
                        "forward_output: sending tts_chunk size=%d for call=%s stream=%s",
                        len(audio_bytes),
                        call_sid or "unknown",
                        stream_sid or "unknown",
                    )
                    await websocket.send_json(build_twilio_media_message(stream_sid, audio_bytes))
                    mark_counter += 1
                    await websocket.send_json(build_twilio_mark_message(stream_sid, f"chunk-{mark_counter}"))
                except Exception:
                    logger.exception(
                        "forward_output: failed sending tts_chunk for call=%s stream=%s",
                        call_sid or "unknown",
                        stream_sid or "unknown",
                    )
        except Exception:
            logger.exception(
                "forward_output: fatal audio pipeline error for call=%s stream=%s",
                call_sid or "unknown",
                stream_sid or "unknown",
            )
            with contextlib.suppress(Exception):
                if call_sid:
                    await _hangup_twilio_call(call_sid, "tts_pipeline_failure")
            with contextlib.suppress(Exception):
                await websocket.close()

    try:
        while True:
            message = await websocket.receive_json()
            event_type = message.get("event")
            if event_type == "connected":
                logger.info("Twilio Media Stream connected")
                continue
            if event_type == "start":
                start = message.get("start") or {}
                stream_sid = str(
                    message.get("streamSid") or start.get("streamSid") or ""
                )
                call_sid = str(start.get("callSid") or "")
                custom = start.get("customParameters") or {}
                param_call_sid = str(custom.get("call_sid") or "")
                token = str(custom.get("stream_token") or "")
                secret = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
                if param_call_sid and param_call_sid != call_sid:
                    logger.warning(
                        "Twilio media stream rejected due to callSid mismatch: param=%s ws=%s",
                        param_call_sid,
                        call_sid,
                    )
                    await websocket.close(code=4401, reason="callSid mismatch")
                    return
                if not validate_stream_token(call_sid, token, secret=secret):
                    logger.warning(
                        "Twilio media stream rejected due to invalid stream token for call %s",
                        call_sid or "unknown",
                    )
                    await websocket.close(code=4401, reason="invalid stream token")
                    return
                with call_center_db.db_session() as conn:
                    call_center_db.get_or_create_session(
                        conn,
                        call_sid,
                        channel="twilio-media-stream",
                    )
                    call_center_db.log_workflow_event(
                        conn,
                        call_sid,
                        "twilio_media_stream_start",
                        {"stream_sid": stream_sid},
                    )
                output_task = asyncio.create_task(forward_output())
                logger.info(
                    "Twilio Media Stream started: call_sid=%s stream_sid=%s",
                    call_sid,
                    stream_sid,
                )
                continue
            if event_type == "media":
                media = message.get("media") or {}
                payload = str(media.get("payload") or "")
                if payload:
                    await audio_queue.put(decode_twilio_media_payload(payload))
                continue
            if event_type == "dtmf":
                digit = str((message.get("dtmf") or {}).get("digit") or "")
                if digit and call_sid:
                    with call_center_db.db_session() as conn:
                        call_center_db.log_workflow_event(
                            conn,
                            call_sid,
                            "twilio_dtmf",
                            {"digit": digit},
                        )
                continue
            if event_type == "mark":
                continue
            if event_type == "stop":
                if call_sid:
                    with call_center_db.db_session() as conn:
                        call_center_db.log_workflow_event(
                            conn,
                            call_sid,
                            "twilio_media_stream_stop",
                            {"stream_sid": stream_sid},
                        )
                break
    except WebSocketDisconnect:
        logger.info(
            "Twilio Media Stream websocket disconnected: call_sid=%s stream_sid=%s",
            call_sid or "unknown",
            stream_sid or "unknown",
        )
    except Exception:
        logger.exception(
            "Twilio Media Stream failed: call_sid=%s stream_sid=%s",
            call_sid or "unknown",
            stream_sid or "unknown",
        )
    finally:
        await audio_queue.put(None)
        if hangup_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                hangup_task.cancel()
                await hangup_task
        if output_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                output_task.cancel()
                await output_task
