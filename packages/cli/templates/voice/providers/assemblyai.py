"""AssemblyAI STT provider using v3 streaming SDK."""
import asyncio
import audioop
import threading
import queue
from typing import Callable

import assemblyai as aai
from assemblyai.streaming.v3 import (
    BeginEvent,
    StreamingClient,
    StreamingClientOptions,
    StreamingError,
    StreamingEvents,
    StreamingParameters,
    StreamingSessionParameters,
    TerminationEvent,
    TurnEvent,
)


class AudioQueue:
    """Audio queue that acts as an iterator for the streaming client."""

    def __init__(self):
        self._queue = queue.Queue()
        self._closed = False

    def put(self, audio_data: bytes):
        """Add audio data to the queue."""
        if not self._closed:
            self._queue.put(audio_data)

    def close(self):
        """Signal end of audio stream."""
        self._closed = True
        self._queue.put(None)  # Sentinel to stop iteration

    def __iter__(self):
        return self

    def __next__(self):
        if self._closed and self._queue.empty():
            raise StopIteration
        item = self._queue.get()
        if item is None:
            raise StopIteration
        return item


class AssemblyAISTT:
    """AssemblyAI streaming STT using v3 SDK."""

    # Buffer size: 100ms at 16kHz = 1600 samples * 2 bytes = 3200 bytes
    BUFFER_SIZE = 3200

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.on_transcript = None
        self._client = None
        self._audio_queue = None
        self._stream_thread = None
        self._loop = None
        self._audio_buffer = b""

    async def start(self, on_transcript: Callable[[str, bool], None]):
        """Start streaming transcription.

        Args:
            on_transcript: Callback(text, is_final)
        """
        self.on_transcript = on_transcript
        self._loop = asyncio.get_event_loop()
        self._audio_queue = AudioQueue()

        # Create streaming client
        self._client = StreamingClient(
            StreamingClientOptions(
                api_key=self.api_key,
                api_host="streaming.assemblyai.com",
            )
        )

        # Set up event handlers
        def on_begin(client, event: BeginEvent):
            print(f"AssemblyAI: Session started: {event.id}")

        def on_turn(client, event: TurnEvent):
            text = event.transcript
            is_final = event.end_of_turn
            is_formatted = getattr(event, 'turn_is_formatted', False)
            print(f"AssemblyAI: '{text}' (final={is_final}, formatted={is_formatted})")

            # Only use formatted transcripts to avoid duplicates
            if text and is_final and is_formatted and self.on_transcript:
                # Schedule callback in async loop
                asyncio.run_coroutine_threadsafe(
                    self.on_transcript(text, True),
                    self._loop
                )

        def on_terminated(client, event: TerminationEvent):
            print(f"AssemblyAI: Session ended ({event.audio_duration_seconds}s processed)")

        def on_error(client, error: StreamingError):
            print(f"AssemblyAI error: {error}")

        self._client.on(StreamingEvents.Begin, on_begin)
        self._client.on(StreamingEvents.Turn, on_turn)
        self._client.on(StreamingEvents.Termination, on_terminated)
        self._client.on(StreamingEvents.Error, on_error)

        # Connect
        self._client.connect(
            StreamingParameters(
                sample_rate=16000,  # We'll resample from 8kHz
                format_turns=True
            )
        )
        print("AssemblyAI: Connected to streaming API")

        # Start streaming in a background thread
        def stream_audio():
            try:
                self._client.stream(self._audio_queue)
            except Exception as e:
                print(f"AssemblyAI stream error: {e}")

        self._stream_thread = threading.Thread(target=stream_audio, daemon=True)
        self._stream_thread.start()

    async def send_audio(self, audio_data: bytes):
        """Send audio chunk to AssemblyAI.

        Converts Twilio μ-law 8kHz to PCM 16-bit 16kHz.
        Buffers audio to meet AssemblyAI's 50-1000ms chunk requirement.
        """
        if self._audio_queue:
            # Convert μ-law to linear PCM 16-bit
            pcm_8k = audioop.ulaw2lin(audio_data, 2)

            # Resample from 8kHz to 16kHz
            pcm_16k, _ = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, None)

            # Add to buffer
            self._audio_buffer += pcm_16k

            # Send when buffer has enough data (100ms minimum)
            while len(self._audio_buffer) >= self.BUFFER_SIZE:
                chunk = self._audio_buffer[:self.BUFFER_SIZE]
                self._audio_buffer = self._audio_buffer[self.BUFFER_SIZE:]
                self._audio_queue.put(chunk)

    async def close(self):
        """Close connection."""
        # Flush remaining buffer if any
        if self._audio_queue and self._audio_buffer:
            # Pad to minimum size if needed
            if len(self._audio_buffer) > 0:
                self._audio_queue.put(self._audio_buffer)
            self._audio_buffer = b""

        if self._audio_queue:
            self._audio_queue.close()

        if self._client:
            try:
                self._client.disconnect(terminate=True)
            except Exception as e:
                print(f"AssemblyAI disconnect error: {e}")
