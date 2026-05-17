"""
AssemblyAI Real-Time Streaming STT Transform

Python implementation that mirrors the TypeScript AssemblyAISTTTransform.
Connects to AssemblyAI's v3 WebSocket API for streaming speech-to-text.

Input: PCM 16-bit audio buffer (bytes)
Output: STT events (stt_chunk for partials, stt_output for final transcripts).

Final transcripts use Turn.end_of_turn (AssemblyAI recommends this over
turn_is_formatted for Universal Streaming). Multilingual: set speech model to
universal-streaming-multilingual, language_detection (default on for that model),
and optional ASSEMBLYAI_STREAMING_LANGUAGE=multi (API values: en | multi) per
Universal Streaming docs. Turn.language_code still reports es/fr/… when detection is on.

Turn patience (avoid finalizing while the user is still speaking) is controlled via
``min_turn_silence``, ``max_turn_silence``, and ``end_of_turn_confidence_threshold``
on the WebSocket URL — see AssemblyAI turn detection docs. Defaults are slightly more
patient than the API defaults; override with ASSEMBLYAI_* env vars.
"""

import asyncio
import contextlib
import json
import os
from typing import AsyncIterator, Optional
from urllib.parse import urlencode

import websockets
from websockets.client import WebSocketClientProtocol

from events import STTChunkEvent, STTEvent, STTOutputEvent


def _env_truthy(name: str) -> Optional[bool]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip(), 10)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        return default


def _normalize_streaming_language(raw: Optional[str]) -> Optional[str]:
    """Map env to API `language` query: only ``en`` and ``multi`` are valid."""
    if not raw:
        return None
    s = raw.strip().lower()
    if s in ("en", "english"):
        return "en"
    if s in ("multi", "multilingual", "es", "spanish"):
        return "multi"
    return None


class AssemblyAISTT:
    def __init__(
        self,
        api_key: Optional[str] = None,
        sample_rate: int = 16000,
        format_turns: bool = True,
        speech_model: Optional[str] = None,
        language_detection: Optional[bool] = None,
        streaming_language: Optional[str] = None,
        min_turn_silence_ms: Optional[int] = None,
        max_turn_silence_ms: Optional[int] = None,
        end_of_turn_confidence_threshold: Optional[float] = None,
    ):
        self.api_key = api_key or os.getenv("ASSEMBLYAI_API_KEY")
        if not self.api_key:
            raise ValueError("AssemblyAI API key is required")

        self.sample_rate = sample_rate
        self.format_turns = format_turns
        # Required by v3 streaming WebSocket; see Universal Streaming docs.
        self.speech_model = speech_model or os.getenv(
            "ASSEMBLYAI_SPEECH_MODEL", "universal-streaming-multilingual"
        )
        ld_env = _env_truthy("ASSEMBLYAI_LANGUAGE_DETECTION")
        if language_detection is not None:
            self.language_detection = language_detection
        elif ld_env is not None:
            self.language_detection = ld_env
        else:
            self.language_detection = (
                "multilingual" in self.speech_model.lower()
            )

        lang = streaming_language if streaming_language is not None else os.getenv(
            "ASSEMBLYAI_STREAMING_LANGUAGE"
        )
        normalized = _normalize_streaming_language(lang)
        if "multilingual" in self.speech_model.lower():
            self.streaming_language = normalized or "multi"
        else:
            # `multi` is only valid with universal-streaming-multilingual
            self.streaming_language = (
                normalized if normalized == "en" else None
            )

        # Higher silence / confidence values wait longer before end_of_turn.
        # API defaults: min=400, max=1280, confidence=0.4.
        self.min_turn_silence_ms = (
            min_turn_silence_ms
            if min_turn_silence_ms is not None
            else _env_int("ASSEMBLYAI_MIN_TURN_SILENCE_MS", 850)
        )
        self.max_turn_silence_ms = (
            max_turn_silence_ms
            if max_turn_silence_ms is not None
            else _env_int("ASSEMBLYAI_MAX_TURN_SILENCE_MS", 2400)
        )
        self.end_of_turn_confidence_threshold = (
            end_of_turn_confidence_threshold
            if end_of_turn_confidence_threshold is not None
            else _env_float("ASSEMBLYAI_END_OF_TURN_CONFIDENCE", 0.55)
        )
        self._ws_open_timeout = max(5.0, _env_float("ASSEMBLYAI_WS_OPEN_TIMEOUT", 45.0))

        self._ws: Optional[WebSocketClientProtocol] = None
        self._connection_signal = asyncio.Event()
        self._close_signal = asyncio.Event()

    async def receive_events(self) -> AsyncIterator[STTEvent]:
        while not self._close_signal.is_set():
            _, pending = await asyncio.wait(
                [
                    asyncio.create_task(self._close_signal.wait()),
                    asyncio.create_task(self._connection_signal.wait()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )

            with contextlib.suppress(asyncio.CancelledError):
                for task in pending:
                    task.cancel()

            if self._close_signal.is_set():
                break

            if self._ws and self._ws.close_code is None:
                self._connection_signal.clear()
                try:
                    async for raw_message in self._ws:
                        try:
                            message = json.loads(raw_message)
                            message_type = message.get("type")

                            if message_type == "Begin":
                                pass
                            elif message_type == "Turn":
                                transcript = (message.get("transcript") or "").strip()
                                utterance = (message.get("utterance") or "").strip()
                                end_of_turn = message.get("end_of_turn", False)

                                if end_of_turn:
                                    final = utterance or transcript
                                    if final:
                                        yield STTOutputEvent.create(final)
                                elif transcript:
                                    yield STTChunkEvent.create(transcript)

                            elif message_type == "Termination":
                                # no-op
                                pass
                            else:
                                if "error" in message:
                                    print(f"AssemblyAISTT error: {message['error']}")
                                    break
                        except json.JSONDecodeError as e:
                            print(f"[DEBUG] AssemblyAISTT JSON decode error: {e}")
                            continue
                except websockets.exceptions.ConnectionClosed:
                    print("AssemblyAISTT: WebSocket connection closed")

    async def send_audio(self, audio_chunk: bytes) -> None:
        ws = await self._ensure_connection()
        await ws.send(audio_chunk)

    async def close(self) -> None:
        if self._ws and self._ws.close_code is None:
            await self._ws.close()
        self._ws = None
        self._close_signal.set()

    async def _ensure_connection(self) -> WebSocketClientProtocol:
        if self._close_signal.is_set():
            raise RuntimeError(
                "AssemblyAISTT tried establishing a connection after it was closed"
            )
        if self._ws and self._ws.close_code is None:
            return self._ws

        query: list[tuple[str, str]] = [
            ("sample_rate", str(self.sample_rate)),
            ("format_turns", str(self.format_turns).lower()),
            ("speech_model", self.speech_model),
        ]
        if self.language_detection:
            query.append(("language_detection", "true"))
        if self.streaming_language:
            query.append(("language", self.streaming_language))

        query.append(("min_turn_silence", str(self.min_turn_silence_ms)))
        query.append(("max_turn_silence", str(self.max_turn_silence_ms)))
        query.append(
            (
                "end_of_turn_confidence_threshold",
                str(self.end_of_turn_confidence_threshold),
            )
        )

        params = urlencode(query)
        url = f"wss://streaming.assemblyai.com/v3/ws?{params}"
        self._ws = await websockets.connect(
            url,
            additional_headers={"Authorization": self.api_key},
            open_timeout=self._ws_open_timeout,
        )

        self._connection_signal.set()
        return self._ws
