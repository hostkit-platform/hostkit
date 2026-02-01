"""Twilio Media Streams WebSocket handler."""
import json
from typing import Callable, Optional
from fastapi import WebSocket


class TwilioMediaStream:
    """Handles Twilio Media Streams WebSocket protocol."""

    def __init__(self, websocket: WebSocket):
        self.ws = websocket
        self.stream_sid: Optional[str] = None

    async def receive_loop(
        self,
        on_audio: Callable[[bytes], None],
        on_start: Optional[Callable[[], None]] = None,
        on_stop: Optional[Callable[[], None]] = None
    ):
        """Main receive loop.

        Args:
            on_audio: Callback for audio chunks
            on_start: Called when stream starts
            on_stop: Called when stream stops
        """
        try:
            while True:
                data = await self.ws.receive_text()
                msg = json.loads(data)

                event = msg.get("event")

                if event == "start":
                    self.stream_sid = msg.get("streamSid")
                    if on_start:
                        await on_start()

                elif event == "media":
                    # Base64-encoded μ-law audio
                    payload = msg.get("media", {}).get("payload", "")
                    if payload:
                        import base64
                        audio_data = base64.b64decode(payload)
                        await on_audio(audio_data)

                elif event == "stop":
                    if on_stop:
                        await on_stop()
                    break

        except Exception as e:
            print(f"WebSocket error: {e}")

    async def send_audio(self, audio_data: bytes):
        """Send audio to Twilio (μ-law encoded, base64)."""
        import base64
        payload = base64.b64encode(audio_data).decode('utf-8')

        msg = {
            "event": "media",
            "streamSid": self.stream_sid,
            "media": {
                "payload": payload
            }
        }

        await self.ws.send_text(json.dumps(msg))

    async def send_mark(self, name: str):
        """Send mark event."""
        msg = {
            "event": "mark",
            "streamSid": self.stream_sid,
            "mark": {"name": name}
        }
        await self.ws.send_text(json.dumps(msg))

    async def clear_buffer(self):
        """Clear Twilio's audio buffer."""
        msg = {
            "event": "clear",
            "streamSid": self.stream_sid
        }
        await self.ws.send_text(json.dumps(msg))
