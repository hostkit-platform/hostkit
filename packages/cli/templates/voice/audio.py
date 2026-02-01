"""Audio utilities for μ-law conversion."""
import base64
import audioop


def mulaw_decode(data: bytes) -> bytes:
    """Decode μ-law audio to linear PCM."""
    return audioop.ulaw2lin(data, 2)  # 16-bit


def mulaw_encode(data: bytes) -> bytes:
    """Encode linear PCM to μ-law."""
    return audioop.lin2ulaw(data, 2)  # 16-bit


def base64_decode(data: str) -> bytes:
    """Decode base64 audio payload."""
    return base64.b64decode(data)


def base64_encode(data: bytes) -> str:
    """Encode audio to base64."""
    return base64.b64encode(data).decode('utf-8')
