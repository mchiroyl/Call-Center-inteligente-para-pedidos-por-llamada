import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))


class TwilioStreamingTests(unittest.TestCase):
    def test_build_connect_stream_twiml_uses_bidirectional_stream(self):
        from twilio_gateway.twiml import build_connect_stream_twiml

        xml = build_connect_stream_twiml(
            websocket_url="wss://example.ngrok-free.app/twilio/media-stream",
            call_sid="CA123",
            stream_token="token-123",
        )

        self.assertIn("<Connect><Stream", xml)
        self.assertIn(
            'url="wss://example.ngrok-free.app/twilio/media-stream"',
            xml,
        )
        self.assertIn('<Parameter name="call_sid" value="CA123"/>', xml)
        self.assertIn('<Parameter name="stream_token" value="token-123"/>', xml)
        self.assertNotIn("/twilio/voice/process", xml)
        self.assertNotIn("<Gather", xml)

    def test_stream_token_round_trip_and_rejects_tampering(self):
        from twilio_gateway.security import make_stream_token, validate_stream_token

        token = make_stream_token("CA123", secret="secret")

        self.assertTrue(validate_stream_token("CA123", token, secret="secret"))
        self.assertFalse(validate_stream_token("CA999", token, secret="secret"))
        self.assertFalse(validate_stream_token("CA123", f"{token}x", secret="secret"))
        self.assertFalse(validate_stream_token("CA123", token, secret=""))

    def test_twilio_mulaw_payload_decodes_to_pcm16(self):
        from twilio_gateway.audio import decode_twilio_media_payload

        # 0xff is mu-law silence. Each byte expands to one 16-bit PCM sample.
        payload = base64.b64encode(bytes([0xFF, 0xFF, 0xFF, 0xFF])).decode(
            "ascii"
        )

        pcm = decode_twilio_media_payload(payload)

        self.assertEqual(len(pcm), 8)
        self.assertEqual(pcm, b"\x00\x00" * 4)

    def test_cartesia_audio_bytes_are_encoded_for_twilio_media(self):
        from twilio_gateway.media_stream import build_twilio_media_message

        message = build_twilio_media_message(
            stream_sid="MZ123",
            audio_chunk=b"\xff\xff\x7f\x7f",
        )

        self.assertEqual(
            message,
            {
                "event": "media",
                "streamSid": "MZ123",
                "media": {"payload": "//9/fw=="},
            },
        )

    def test_twilio_voice_endpoint_returns_media_stream_twiml(self):
        os.environ["TWILIO_AUTH_TOKEN"] = "secret"
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["CALL_CENTER_DB_PATH"] = str(Path(tmpdir) / "test.db")

            from fastapi.testclient import TestClient

            import call_center_db
            import routers.twilio as twilio_router
            from app_factory import create_app

            async def skip_signature_validation(request, form):
                return None

            call_center_db.bootstrap_db()
            twilio_router.validate_twilio_request = skip_signature_validation
            client = TestClient(create_app())

            response = client.post(
                "/twilio/voice",
                data={
                    "CallSid": "CA123",
                    "From": "+50255550000",
                    "To": "+50222220000",
                },
                headers={
                    "host": "example.ngrok-free.app",
                    "x-forwarded-proto": "https",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("<Connect><Stream", response.text)
        self.assertIn(
            'url="wss://example.ngrok-free.app/twilio/media-stream"',
            response.text,
        )
        self.assertIn('<Parameter name="call_sid" value="CA123"/>', response.text)
        self.assertNotIn("/twilio/voice/process", response.text)
        self.assertNotIn("<Gather", response.text)


if __name__ == "__main__":
    unittest.main()
