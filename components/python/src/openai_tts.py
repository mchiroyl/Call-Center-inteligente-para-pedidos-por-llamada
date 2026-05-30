"""OpenAI Text-to-Speech adapter with basic PCM conversion for Twilio."""

from __future__ import annotations

import asyncio
import logging
import os
import struct
from typing import AsyncIterator, Literal, Optional

from openai import AsyncOpenAI

from events import TTSChunkEvent

logger = logging.getLogger(__name__)


def _pcm16_bytes_to_samples(audio: bytes) -> list[int]:
    return [sample[0] for sample in struct.iter_unpack("<h", audio)]


def _samples_to_pcm16_bytes(samples: list[int]) -> bytes:
    return b"".join(
        struct.pack("<h", max(-32768, min(32767, int(sample)))) for sample in samples
    )


def _downsample_24k_to_8k(audio: bytes) -> bytes:
    samples = _pcm16_bytes_to_samples(audio)
    return _samples_to_pcm16_bytes(samples[::3])


def _pcm16_to_mulaw_byte(sample: int) -> int:
    bias = 0x84
    clip = 32635
    sign = 0x80 if sample < 0 else 0
    magnitude = min(clip, abs(sample)) + bias
    exponent = 7
    mask = 0x4000
    while exponent > 0 and not (magnitude & mask):
        exponent -= 1
        mask >>= 1
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa)) & 0xFF


def _pcm16_to_mulaw(audio: bytes) -> bytes:
    samples = _pcm16_bytes_to_samples(audio)
    return bytes(_pcm16_to_mulaw_byte(sample) for sample in samples)


class OpenAITTS:
    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: str = "gpt-4o-mini-tts",
        voice: str = "alloy",
        sample_rate: int = 24000,
        encoding: Literal["pcm_s16le", "pcm_mulaw"] = "pcm_s16le",
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key is required")
        self.model = os.getenv("OPENAI_TTS_MODEL", model)
        self.voice = os.getenv("OPENAI_TTS_VOICE", voice)
        self.sample_rate = sample_rate
        self.encoding = encoding
        self.client = AsyncOpenAI(api_key=self.api_key)
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._closed = False

    async def prepare(self) -> None:
        return None

    async def send_text(self, text: Optional[str]) -> None:
        if self._closed or text is None or not text.strip():
            return
        response = await self.client.audio.speech.create(
            model=self.model,
            voice=self.voice,
            input=text,
            instructions="Habla en espanol de Guatemala con tono profesional y cordial.",
            response_format="pcm",
        )
        audio = await response.aread()
        processed = self._convert_audio(audio)
        chunk_size = 1600 if self.encoding == "pcm_mulaw" else 3200
        for index in range(0, len(processed), chunk_size):
            await self._queue.put(processed[index : index + chunk_size])

    def _convert_audio(self, audio: bytes) -> bytes:
        pcm = audio
        if self.sample_rate == 8000:
            pcm = _downsample_24k_to_8k(pcm)
        if self.encoding == "pcm_mulaw":
            return _pcm16_to_mulaw(pcm)
        return pcm

    async def receive_events(self) -> AsyncIterator[TTSChunkEvent]:
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                return
            if chunk:
                yield TTSChunkEvent.create(chunk)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._queue.put(None)
