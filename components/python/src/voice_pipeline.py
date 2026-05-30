"""Reusable voice pipeline pieces for web and Twilio channels."""

import asyncio
import contextlib
import logging
from typing import AsyncIterator
from uuid import uuid4

from langchain_core.runnables import RunnableGenerator

import call_center_db
from agent_tools import stream_agent_response
from assemblyai_stt import AssemblyAISTT
from cartesia_tts import CartesiaTTS
from event_hub import kitchen_events
from events import (
    AgentChunkEvent,
    AgentEndEvent,
    CallControlEvent,
    ToolResultEvent,
    VoiceAgentEvent,
)
from settings import VOICE_GREETING
from utils import merge_async_iters
from workflow import get_workflow

logger = logging.getLogger(__name__)


def _compact_text(value: str | None) -> str:
    text = " ".join((value or "").split())
    return text[:500]


def log_customer_turn(channel_label: str, session_id: str, transcript: str) -> None:
    logger.info(
        "[%s][%s] CLIENTE: %s",
        channel_label,
        session_id,
        _compact_text(transcript),
    )


def log_agent_turn(channel_label: str, session_id: str, text: str) -> None:
    logger.info(
        "[%s][%s] AGENTE: %s",
        channel_label,
        session_id,
        _compact_text(text),
    )

async def stt_stream(
    audio_stream: AsyncIterator[bytes],
    stt: AssemblyAISTT | None = None,
) -> AsyncIterator[VoiceAgentEvent]:
    if stt is None:
        stt = AssemblyAISTT(sample_rate=16000)

    async def send_audio() -> None:
        try:
            async for audio_chunk in audio_stream:
                await stt.send_audio(audio_chunk)
        finally:
            await stt.close()

    send_task = asyncio.create_task(send_audio())
    try:
        async for event in stt.receive_events():
            if event.type == "stt_chunk":
                logger.info("STT partial transcript: %s", event.transcript)
            elif event.type == "stt_output":
                logger.info("STT final transcript: %s", event.transcript)
            yield event
    finally:
        with contextlib.suppress(asyncio.CancelledError):
            send_task.cancel()
            await send_task
        await stt.close()


async def agent_stream(
    event_stream: AsyncIterator[VoiceAgentEvent],
    *,
    thread_id: str | None = None,
    channel_label: str = "VOICE",
) -> AsyncIterator[VoiceAgentEvent]:
    session_id = thread_id or str(uuid4())

    async for event in event_stream:
        # Forward upstream events (stt_chunk, stt_output, etc.)
        if event.type == "stt_chunk":
            logger.info("agent_stream: received stt_chunk: %s", event.transcript)
            yield event
            continue

        if event.type == "stt_output":
            logger.info("agent_stream: received stt_output (final): %s", event.transcript)
            yield event
            # Run the agent workflow for the finalized transcript
            async for response_event in run_text_turn(
                session_id=session_id,
                transcript=event.transcript,
                channel_label=channel_label,
            ):
                if response_event.type == "agent_chunk":
                    logger.info("agent_stream: agent_chunk -> %s", response_event.text)
                if response_event.type == "agent_end":
                    logger.info("agent_stream: agent_end for session %s", session_id)
                yield response_event
            continue

        # Other events (agent/tool/tts) are forwarded
        yield event


async def run_text_turn(
    *,
    session_id: str,
    transcript: str,
    channel_label: str = "VOICE",
) -> AsyncIterator[VoiceAgentEvent]:
    logger.info("run_text_turn: session=%s transcript=%s", session_id, transcript)
    log_customer_turn(channel_label, session_id, transcript)
    try:
        turn = await asyncio.wait_for(
            get_workflow().run_turn(session_id, transcript), timeout=25.0
        )
        if isinstance(turn, dict):
            logger.info("run_text_turn: workflow returned keys=%s", list(turn.keys()))
        else:
            logger.info("run_text_turn: workflow returned non-dict result type=%s", type(turn))
    except asyncio.TimeoutError:
        logger.exception("run_text_turn: workflow.run_turn timed out for session %s", session_id)
        yield AgentChunkEvent.create(
            "Disculpe, el sistema tardo en procesar su solicitud. "
            "Puede ser latencia de red o problema con la API de IA (cuota/credenciales). "
            "Intente de nuevo en unos segundos."
        )
        yield AgentEndEvent.create()
        return
    except Exception:
        logger.exception("run_text_turn: exception calling run_turn for session %s", session_id)
        yield AgentChunkEvent.create(
            "Disculpe, ocurrió un error interno. Intente nuevamente más tarde."
        )
        yield AgentEndEvent.create()
        return
    draft = turn.get("draft") or call_center_db.empty_draft()
    retrieval_hits = turn.get("retrieval_hits", [])
    finalized_order = turn.get("finalized_order")

    if retrieval_hits:
        yield ToolResultEvent.create(
            tool_call_id=f"retrieval-{uuid4()}",
            name="retrieval_context",
            result=call_center_db.format_retrieval_tool_result(
                query=transcript,
                hits=retrieval_hits,
                message="Contexto semantico recuperado para esta llamada.",
            ),
        )

    yield ToolResultEvent.create(
        tool_call_id=f"draft-{uuid4()}",
        name="draft_state",
        result=call_center_db.format_draft_tool_result(
            draft,
            message="Borrador estructurado sincronizado.",
        ),
    )

    if finalized_order:
        kitchen_events.publish_from_thread(
            {"type": "order_created", "order": finalized_order}
        )
        yield ToolResultEvent.create(
            tool_call_id=f"order-{uuid4()}",
            name="confirmed_order",
            result=call_center_db.format_order_tool_result(
                finalized_order,
                message="Orden final confirmada y enviada a operaciones.",
            ),
        )
    logger.info("run_text_turn: prepared draft ready_for_confirmation=%s, finalized_order=%s", draft.get("ready_for_confirmation"), bool(finalized_order))

    collected_text: list[str] = []
    if turn.get("response_mode") == "template":
        text = turn.get("response_text") or "Un momento por favor."
        collected_text.append(text)
        logger.info("run_text_turn: yielding template response chunk: %s", text)
        yield AgentChunkEvent.create(text)
    else:
        chunks, tool_events = await stream_agent_response(
            thread_id=session_id,
            agent_prompt=turn["agent_prompt"],
        )
        logger.info("run_text_turn: stream_agent_response produced %d chunks", len(chunks))
        collected_text.extend(chunks)
        for tool_event in tool_events:
            logger.info("run_text_turn: yielding tool_event %s", tool_event)
            yield tool_event

    response_text = "".join(collected_text).strip()
    if response_text:
        log_agent_turn(channel_label, session_id, response_text)
    with call_center_db.db_session() as conn:
        call_center_db.update_session(
            conn,
            session_id,
            latest_response=response_text or turn.get("response_text"),
            current_state=turn.get("workflow_state_name"),
        )
    # If agent produced no response, send a polite fallback so callers always hear something
    if not response_text:
        fallback = "Disculpe, no pude procesar su solicitud. ¿Puede repetir o pedir otra cosa?"
        logger.warning("run_text_turn: agent produced no response for session %s; sending fallback", session_id)
        yield AgentChunkEvent.create(fallback)
        yield AgentEndEvent.create()
        return

    yield AgentEndEvent.create()
    if turn.get("end_call"):
        logger.info(
            "run_text_turn: end_call requested for session=%s reason=%s",
            session_id,
            turn.get("call_end_reason", "completed"),
        )
        yield CallControlEvent.hangup(str(turn.get("call_end_reason", "completed")))


async def tts_stream(
    event_stream: AsyncIterator[VoiceAgentEvent],
    *,
    tts: CartesiaTTS | None = None,
) -> AsyncIterator[VoiceAgentEvent]:
    tts = tts or CartesiaTTS()
    tts_ready = False

    async def process_upstream() -> AsyncIterator[VoiceAgentEvent]:
        buffer: list[str] = []
        async for event in event_stream:
            yield event
            if event.type == "agent_chunk":
                buffer.append(event.text)
            if event.type == "agent_end" and tts_ready:
                await tts.send_text("".join(buffer))
                buffer = []

    try:
        try:
            await tts.prepare()
            tts_ready = True
        except Exception:
            logger.exception("tts_stream: failed preparing TTS provider")
            # Graceful degradation: keep the session alive without TTS audio.
            async for event in event_stream:
                yield event
            return
        async for event in merge_async_iters(process_upstream(), tts.receive_events()):
            yield event
    finally:
        await tts.close()


def build_web_voice_pipeline(stt: AssemblyAISTT) -> RunnableGenerator:
    async def stt_stream_with_greeting(
        audio: AsyncIterator[bytes],
    ) -> AsyncIterator[VoiceAgentEvent]:
        yield AgentChunkEvent.create(VOICE_GREETING)
        yield AgentEndEvent.create()
        async for event in stt_stream(audio, stt=stt):
            yield event

    async def web_agent_stream(
        event_stream: AsyncIterator[VoiceAgentEvent],
    ) -> AsyncIterator[VoiceAgentEvent]:
        async for event in agent_stream(event_stream, channel_label="WEB"):
            yield event

    return (
        RunnableGenerator(stt_stream_with_greeting)
        | RunnableGenerator(web_agent_stream)
        | RunnableGenerator(tts_stream)
    )
