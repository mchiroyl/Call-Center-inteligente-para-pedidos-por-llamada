"""Security helpers for Twilio HTTP webhooks and media stream parameters."""

import base64
import hashlib
import hmac


def make_stream_token(call_sid: str, *, secret: str) -> str:
    """Create a URL-safe HMAC token for a Twilio Media Stream."""
    if not call_sid or not secret:
        return ""
    digest = hmac.new(
        secret.encode("utf-8"),
        call_sid.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def validate_stream_token(call_sid: str, token: str, *, secret: str) -> bool:
    """Validate a stream token without leaking timing information."""
    if not call_sid or not token or not secret:
        return False
    expected = make_stream_token(call_sid, secret=secret)
    return hmac.compare_digest(expected, token)

