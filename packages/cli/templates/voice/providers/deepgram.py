"""Deepgram STT provider."""
import asyncio
from typing import Callable, Optional
from deepgram import DeepgramClient, LiveTranscriptionEvents, LiveOptions


class DeepgramSTT:
    """Deepgram streaming STT."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = None
        self.connection = None

    async def start(self, on_transcript: Callable[[str, bool], None]):
        """Start streaming transcription.

        Args:
            on_transcript: Callback(text, is_final)
        """
        self.client = DeepgramClient(self.api_key)
        self.connection = self.client.listen.asynclive.v("1")

        async def on_message(_, result, **kwargs):
            sentence = result.channel.alternatives[0].transcript
            if sentence:
                is_final = result.is_final
                await on_transcript(sentence, is_final)

        self.connection.on(LiveTranscriptionEvents.Transcript, on_message)

        options = LiveOptions(
            model="nova-2",
            language="en-US",
            smart_format=True,
            encoding="mulaw",
            sample_rate=8000,
            channels=1,
            interim_results=True,
            utterance_end_ms=1000,
            vad_events=True
        )

        await self.connection.start(options)

    async def send_audio(self, audio_data: bytes):
        """Send audio chunk to Deepgram."""
        if self.connection:
            self.connection.send(audio_data)

    async def close(self):
        """Close connection."""
        if self.connection:
            await self.connection.finish()
