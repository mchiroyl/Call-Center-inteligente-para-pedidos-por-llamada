"""Audio helpers for Twilio Media Streams."""

import base64
import struct


def decode_twilio_media_payload(payload: str) -> bytes:
    """Decode Twilio base64 mu-law/8000 media into signed PCM16 bytes."""
    mulaw = base64.b64decode(payload)
    return b"".join(struct.pack("<h", _ulaw_byte_to_pcm16(byte)) for byte in mulaw)


def encode_twilio_media_payload(audio_chunk: bytes) -> str:
    """Encode raw mu-law bytes for a Twilio outbound media message."""
    return base64.b64encode(audio_chunk).decode("ascii")


def _ulaw_byte_to_pcm16(byte: int) -> int:
    """Convert a single G.711 mu-law byte to 16-bit signed PCM."""
    value = byte ^ 0xFF
    sign = value & 0x80
    exponent = (value >> 4) & 0x07
    mantissa = value & 0x0F
    sample = ((mantissa << 3) + 0x84) << exponent
    sample -= 0x84
    if sign:
        sample = -sample
    return sample
