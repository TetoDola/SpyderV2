"""Voice note ingestion service: audio file → transcribed plain text.

Default configuration targets the Deepgram pre-recorded API but stays
provider-agnostic — swap TRANSCRIPTION_API_URL / TRANSCRIPTION_API_KEY
in settings to point at any STT service that accepts a binary audio POST.
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlencode

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
        TranscriptionError: If the API call fails.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise TranscriptionError(f"Audio file not found: {file_path}")

    api_url: str = settings.TRANSCRIPTION_API_URL
    api_key: str = settings.TRANSCRIPTION_API_KEY
    model: str = getattr(settings, "TRANSCRIPTION_MODEL", "nova-3")
    language: str = getattr(settings, "TRANSCRIPTION_LANGUAGE", "en")

    # --- Build request ---
    # Deepgram expects: binary body, Content-Type = audio MIME, Token auth,
    # and features as query params.  Other providers (e.g. a local Whisper
    # wrapper) typically accept multipart form data.  We detect the provider
    # from the URL and adapt.
    is_deepgram = "deepgram.com" in api_url

    if is_deepgram:
        text = _transcribe_deepgram(file_path, api_url, api_key, model, language)
    else:
        text = _transcribe_generic(file_path, api_url, api_key)

    return text.strip()


# ---------------------------------------------------------------------------
# Deepgram path
# ---------------------------------------------------------------------------

def _transcribe_deepgram(
    file_path: Path,
    api_url: str,
    api_key: str,
    model: str,
    language: str,
) -> str:
    """Call Deepgram pre-recorded endpoint with binary audio body."""
    params = {
        "model": model,
        "language": language,
        "smart_format": "true",
        "punctuate": "true",
    }
    url = f"{api_url}?{urlencode(params)}"

    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": _mime_type(file_path),
    }

    with file_path.open("rb") as f:
        response = httpx.post(
            url,
            content=f.read(),
            headers=headers,
            timeout=180.0,
        )

    if response.status_code != 200:
        raise TranscriptionError(
            f"Deepgram returned {response.status_code}: {response.text}"
        )

    data = response.json()

    # Deepgram response: results.channels[0].alternatives[0].transcript
    try:
        channels = data["results"]["channels"]
        text = channels[0]["alternatives"][0]["transcript"]
    except (KeyError, IndexError) as e:
        raise TranscriptionError(
            f"Unexpected Deepgram response structure: {e}"
        ) from e

    if not isinstance(text, str):
        text = str(text)

    return text


# ---------------------------------------------------------------------------
# Generic / fallback path (multipart upload — works with local Whisper, etc.)
# ---------------------------------------------------------------------------

def _transcribe_generic(
    file_path: Path,
    api_url: str,
    api_key: str,
) -> str:
    """Call a generic STT endpoint that accepts multipart file upload."""
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
    # Support common response shapes
    text = data.get("text") or data.get("transcript") or ""
    if not isinstance(text, str):
        text = str(text)

    return text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mime_type(path: Path) -> str:
    """Return MIME type for common audio formats."""
    suffix = path.suffix.lower()
    return {
        ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".webm": "audio/webm",
        ".flac": "audio/flac",
        ".aac": "audio/aac",
    }.get(suffix, "application/octet-stream")
