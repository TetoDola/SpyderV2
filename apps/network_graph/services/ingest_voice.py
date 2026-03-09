"""Voice note ingestion service: audio file → transcribed plain text."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


class TranscriptionError(Exception):
    """Raised when the transcription API call fails."""


def transcribe_audio(file_path: str | Path) -> str:
    """Send an audio file to the transcription API and return plain text.

    Args:
        file_path: Path to the audio file on disk.

    Returns:
        Transcribed plain text.

    Raises:
        TranscriptionError: If the API call fails after retries.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise TranscriptionError(f"Audio file not found: {file_path}")

    api_url: str = settings.TRANSCRIPTION_API_URL
    api_key: str = settings.TRANSCRIPTION_API_KEY

    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    with file_path.open("rb") as f:
        files = {"file": (file_path.name, f, _mime_type(file_path))}
        response = httpx.post(
            api_url,
            files=files,
            headers=headers,
            timeout=120.0,
        )

    if response.status_code != 200:
        raise TranscriptionError(
            f"Transcription API returned {response.status_code}: {response.text}"
        )

    data = response.json()
    # Support common response shapes: {"text": "..."} or {"transcript": "..."}
    text = data.get("text") or data.get("transcript") or ""
    if not isinstance(text, str):
        text = str(text)

    return text.strip()


def _mime_type(path: Path) -> str:
    """Return MIME type for common audio formats."""
    suffix = path.suffix.lower()
    return {
        ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".webm": "audio/webm",
    }.get(suffix, "application/octet-stream")
