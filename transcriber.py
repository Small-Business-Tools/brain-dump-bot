import io
import logging
from openai import AsyncOpenAI, APIError

logger = logging.getLogger(__name__)
openai_client = AsyncOpenAI()  # reads OPENAI_API_KEY from environment automatically


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
        audio_file = io.BytesIO(file_bytes)
        audio_file.name = "voice.ogg"  # Whisper needs a filename to infer format

        transcript = await openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="en",
        )

        text = transcript.text.strip()

        if not text:
            logger.warning("Whisper returned an empty transcription.")
            return None

        logger.info(f"Whisper transcribed {len(file_bytes)} bytes → {len(text)} chars")
        return text

    except APIError as e:
        logger.error(f"OpenAI Whisper API error: {e}")
        return None

    except Exception as e:
        logger.error(f"Unexpected transcription error: {type(e).__name__}: {e}")
        return None
