"""
BridgeSpace - AI Service
All LLM calls routed through Ollama Cloud (gpt-oss:120b).
"""
import json
import logging
import os
import requests
from flask import current_app

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = "https://ollama.com"
MODEL = "gpt-oss:120b"


def _get_headers():
    api_key = current_app.config.get("OLLAMA_API_KEY", "")
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }


def _ask(prompt: str, max_retries: int = 2) -> str:
    """
    Core streaming call to Ollama Cloud.
    Mirrors the llm_client.py pattern exactly.
    Raises RuntimeError if the request fails after retries.
    """
    payload = {
        "model": MODEL,
        "prompt": prompt,
    }

    for attempt in range(max_retries + 1):
        try:
            full_response = ""
            with requests.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                headers=_get_headers(),
                json=payload,
                stream=True,
                timeout=120,
            ) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if line:
                        try:
                            data = json.loads(line.decode("utf-8"))
                            if "response" in data:
                                full_response += data["response"]
                        except json.JSONDecodeError:
                            continue
            return full_response.strip()

        except requests.exceptions.RequestException as exc:
            logger.warning("Ollama Cloud attempt %d failed: %s", attempt + 1, exc)
            if attempt == max_retries:
                raise RuntimeError(f"Ollama Cloud request failed: {exc}") from exc

    return ""


# ── Public AI functions ────────────────────────────────────────────────────

def reformulate_post(text: str, context: str = "", lang: str = "en") -> str:
    """
    De-escalate and reframe a mediation post using non-violent communication,
    strictly preserving all original facts and intent.
    Output only the reformulated text — no preamble.
    """
    prompt = f"""You are a professional conflict mediator and communication coach.
Reformulate the message below to make it more constructive, empathetic and
non-confrontational, while STRICTLY preserving all original facts and intent.

Rules:
- Do NOT add, remove or distort any facts
- Transform aggressive or accusatory language into assertive, calm communication
- Keep the same language as the original text
- Output ONLY the reformulated text, no preamble or explanation

Mediation context: {context or 'General dispute'}

Original message:
{text}

Reformulated message:"""

    return _ask(prompt)


def translate_text(text: str, target_language: str, source_language: str = "auto") -> str:
    """
    Translate text to target_language.
    Output only the translated text — no preamble.
    """
    from models import SUPPORTED_LANGUAGES
    lang_name = SUPPORTED_LANGUAGES.get(target_language, target_language)

    prompt = f"""Translate the following text to {lang_name}.
Output ONLY the translated text, no explanation or preamble.

Text to translate:
{text}

Translation:"""

    return _ask(prompt)


def get_mediation_summary(posts: list, lang: str = "en") -> str:
    """
    Generate a neutral, balanced summary of the mediation exchange,
    identifying key points of agreement and disagreement.
    """
    from models import SUPPORTED_LANGUAGES
    lang_name = SUPPORTED_LANGUAGES.get(lang, "English")

    posts_text = "\n\n".join(
        [f"Party {p['author']}: {p['content']}" for p in posts]
    )

    prompt = f"""You are a neutral mediator summarising a dispute.
Write a concise, balanced summary of this mediation exchange in {lang_name}.
Identify the key points of agreement and the main remaining disagreements.
Be objective and do not take sides.

Mediation exchange:
{posts_text}

Summary:"""

    return _ask(prompt)


def transcribe_voice_note(audio_base64: str, mime_type: str = "audio/webm") -> str:
    """
    Voice transcription stub.
    Ollama Cloud does not support audio input.
    Wire up OpenAI Whisper or Google Speech-to-Text for production:
      pip install openai
      from openai import OpenAI
      client = OpenAI()
      transcript = client.audio.transcriptions.create(model="whisper-1", file=audio_file)
    """
    logger.warning("Voice transcription called but no STT service is configured.")
    return "[Voice transcription not yet configured — please type your message.]"