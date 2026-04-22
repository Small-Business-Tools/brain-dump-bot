import io
import os
import logging
from openai import AsyncOpenAI, APIError

logger = logging.getLogger(__name__)


def _get_client() -> AsyncOpenAI:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        logger.error("OPENAI_API_KEY is not set in environment variables")
        raise ValueError("OPENAI_API_KEY missing")
    return AsyncOpenAI(api_key=key)


async def transcribe_voice(file_bytes: bytes, mime_type: str = "audio/ogg") -> str | None:
    """
    Send raw audio bytes to OpenAI Whisper and return the transcribed text.
    Returns None if transcription fails.

    Args:
        file_bytes: raw audio data downloaded from Telegram
        mime_type:  Telegram voice notes are ogg/opus by default

    Returns:
        Transcribed string, or None on failure.
    """
    try:
        if not file_bytes or len(file_bytes) < 100:
            logger.error(f"Audio file too small to transcribe: {len(file_bytes) if file_bytes else 0} bytes")
            return None

        logger.info(f"Sending {len(file_bytes)} bytes to Whisper...")

        audio_file = io.BytesIO(file_bytes)
        audio_file.name = "voice.ogg"  # Whisper needs a filename to infer format

        transcript = await _get_client().audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="en",
        )

        text = transcript.text.strip()

        if not text:
            logger.warning("Whisper returned an empty transcription.")
            return None

        logger.info(f"Whisper transcribed {len(file_bytes)} bytes → {len(text)} chars: '{text[:80]}'")
        return text

    except APIError as e:
        logger.error(f"OpenAI Whisper API error: {e}")
        return None

    except Exception as e:
        logger.error(f"Unexpected transcription error: {type(e).__name__}: {e}")
        return None
