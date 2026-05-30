"""
Cartesia Text-to-Speech Streaming

Python implementation of Cartesia's Sonic streaming TTS API.
Converts text to PCM audio in real-time using WebSocket streaming.

Input: Text strings
Output: TTS events (tts_chunk for audio chunks)
"""

import asyncio
import base64
import contextlib
import json
import logging
import os
import time
from typing import AsyncIterator, Literal, Optional

import websockets
from websockets.client import WebSocketClientProtocol
from websockets.exceptions import InvalidStatus

from events import TTSChunkEvent

# Ximena - Calm Navigator: Spanish Latina female, clear and professional.
DEFAULT_VOICE_ID = "3597a26f-80ef-4bd5-8101-9699bc764917"
logger = logging.getLogger(__name__)


def _ws_open_timeout_s() -> float:
    raw = os.getenv("CARTESIA_WS_OPEN_TIMEOUT")
    if raw and str(raw).strip():
        try:
            return max(5.0, float(str(raw).strip()))
        except ValueError:
            pass
    return 45.0


def _default_cartesia_language() -> str:
    """Sonic `language` for the WebSocket payload."""
    raw = (os.getenv("CARTESIA_LANGUAGE") or "").strip().lower()
    if raw:
        return raw
    aai = (os.getenv("ASSEMBLYAI_STREAMING_LANGUAGE") or "").strip().lower()
    if aai in ("es", "spanish", "multi", "multilingual"):
        return "es"
    if aai in ("en", "english"):
        return "en"
    return "es"


class CartesiaTTS:
    _ws: Optional[WebSocketClientProtocol]
    _connection_signal: asyncio.Event
    _close_signal: asyncio.Event

    def __init__(
        self,
        api_key: Optional[str] = None,
        voice_id: Optional[str] = None,
        model_id: str = "sonic-3",
        sample_rate: int = 24000,
        encoding: Literal[
            "pcm_s16le", "pcm_f32le", "pcm_mulaw", "pcm_alaw"
        ] = "pcm_s16le",
        language: Optional[str] = None,
        cartesia_version: str = "2025-04-16",
    ):
        self.api_key = api_key or os.getenv("CARTESIA_API_KEY")
        if not self.api_key:
            raise ValueError("Cartesia API key is required")

        self.voice_id = voice_id or os.getenv("CARTESIA_VOICE_ID") or DEFAULT_VOICE_ID
        self.model_id = model_id
        self.sample_rate = sample_rate
        self.encoding = encoding
        self.language = (
            language or _default_cartesia_language()
        ).strip().lower() or "es"
        self.cartesia_version = cartesia_version
        self._ws = None
        self._connection_signal = asyncio.Event()
        self._close_signal = asyncio.Event()
        self._context_counter = 0
        self._open_timeout = _ws_open_timeout_s()

    async def prepare(self) -> None:
        """Open the Cartesia WebSocket before merging streams."""
        await self._ensure_connection()

    def _generate_context_id(self) -> str:
        """
        Generate a valid context_id for Cartesia.
        Context IDs must only contain alphanumeric characters, underscores, and hyphens.
        """
        timestamp = int(time.time() * 1000)
        counter = self._context_counter
        self._context_counter += 1
        return f"ctx_{timestamp}_{counter}"

    async def send_text(self, text: Optional[str]) -> None:
        if text is None:
            return

        if not text.strip():
            return

        ws = await self._ensure_connection()

        payload = {
            "model_id": self.model_id,
            "transcript": text,
            "voice": {
                "mode": "id",
                "id": self.voice_id,
            },
            "output_format": {
                "container": "raw",
                "encoding": self.encoding,
                "sample_rate": self.sample_rate,
            },
            "language": self.language,
            "context_id": self._generate_context_id(),
            "continue": False,
        }
        await ws.send(json.dumps(payload))

    async def receive_events(self) -> AsyncIterator[TTSChunkEvent]:
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
                            if "data" in message and message["data"] is not None:
                                audio_chunk = base64.b64decode(message["data"])
                                if audio_chunk:
                                    yield TTSChunkEvent.create(audio_chunk)
                            if message.get("done") or message.get("type") == "done":
                                continue
                            if "error" in message and message["error"]:
                                print(f"[DEBUG] Cartesia error: {message['error']}")
                                break
                        except json.JSONDecodeError as e:
                            print(f"[DEBUG] Cartesia JSON decode error: {e}")
                            continue
                except websockets.exceptions.ConnectionClosed:
                    print("Cartesia: WebSocket connection closed")
                finally:
                    if self._ws and self._ws.close_code is None:
                        await self._ws.close()
                    self._ws = None

    async def close(self) -> None:
        if self._ws and self._ws.close_code is None:
            await self._ws.close()
        self._ws = None
        self._close_signal.set()

    async def _ensure_connection(self) -> WebSocketClientProtocol:
        if self._close_signal.is_set():
            raise RuntimeError(
                "CartesiaTTS tried establishing a connection after it was closed"
            )
        if self._ws and self._ws.close_code is None:
            return self._ws

        url = (
            f"wss://api.cartesia.ai/tts/websocket"
            f"?api_key={self.api_key}&cartesia_version={self.cartesia_version}"
        )
        try:
            self._ws = await websockets.connect(
                url,
                open_timeout=self._open_timeout,
            )
        except InvalidStatus as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code == 402:
                logger.error(
                    "Cartesia WebSocket rejected with HTTP 402. Check credits/billing for the TTS account."
                )
                raise RuntimeError(
                    "Cartesia TTS unavailable: HTTP 402 from provider (possible credits/billing issue)."
                ) from exc
            raise
        except Exception:
            logger.exception("Cartesia WebSocket connection failed")
            raise

        self._connection_signal.set()
        return self._ws
