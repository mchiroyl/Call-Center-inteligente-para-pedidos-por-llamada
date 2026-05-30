"""TwiML builders for Twilio webhooks."""

from html import escape


def build_connect_stream_twiml(
    *,
    websocket_url: str,
    call_sid: str,
    stream_token: str,
) -> str:
    """Build TwiML that opens a bidirectional Twilio Media Stream."""
    safe_url = escape(websocket_url, quote=True)
    safe_call_sid = escape(call_sid, quote=True)
    safe_token = escape(stream_token, quote=True)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Connect><Stream url="{safe_url}">'
        f'<Parameter name="call_sid" value="{safe_call_sid}"/>'
        f'<Parameter name="stream_token" value="{safe_token}"/>'
        "</Stream></Connect>"
        "</Response>"
    )


def build_message_twiml(message: str) -> str:
    safe_message = escape(message, quote=False)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Message>{safe_message}</Message></Response>"
    )

