"""
BridgeSpace - AI Service
All LLM calls routed through Ollama Cloud (gpt-oss:120b).
Includes helpers for the four facilitative mediation phases.
"""
import json
import logging
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
    """Core streaming call to Ollama Cloud."""
    payload = {"model": MODEL, "prompt": prompt}

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


# ---------------------------------------------------------------------------
# Shared language lookup
# ---------------------------------------------------------------------------

def _lang_name(lang_code: str) -> str:
    """Return a human-readable language name from a BCP-47 code."""
    from models import SUPPORTED_LANGUAGES
    # SUPPORTED_LANGUAGES is a dict {code: label}
    return SUPPORTED_LANGUAGES.get(lang_code, lang_code)


# ---------------------------------------------------------------------------
# Phase 1 – NVC reformulation  (original + new alias)
# ---------------------------------------------------------------------------

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


# Alias used by the perspectives / proposals phase routes
def reformulate_nvc(text: str, context: str = "") -> str:
    return reformulate_post(text, context)


# ---------------------------------------------------------------------------
# Phase 2 – Agenda extraction
# ---------------------------------------------------------------------------

def extract_agenda_points(perspective_texts: list, lang: str = "en") -> list:
    """
    Given a list of perspective texts, return a list of agenda point dicts:
      [{"title": "...", "description": "..."}, ...]
    """
    combined = "\n\n---\n\n".join(perspective_texts)
    lang_name = _lang_name(lang)

    prompt = f"""You are an experienced facilitative mediator.
Read the following perspectives from conflicting parties and identify
the key agenda points that need to be addressed in negotiation.
Return ONLY a JSON array. Each item must have "title" (short label)
and "description" (one sentence explanation).
Write both title and description in {lang_name}.
No markdown, no extra text — only the raw JSON array.

Perspectives:
{combined}

JSON agenda points:"""

    raw = _ask(prompt)
    try:
        clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        points = json.loads(clean)
        if isinstance(points, list):
            return points
    except (json.JSONDecodeError, AttributeError):
        logger.warning("Could not parse agenda points JSON: %r", raw)

    return [{"title": "Main dispute", "description": "Core disagreement identified from perspectives."}]


# ---------------------------------------------------------------------------
# Phase 4 – Agreement drafting
# ---------------------------------------------------------------------------

def draft_agreement(mediation_title: str, accepted_proposals: list, lang: str = "en") -> str:
    """
    Draft a formal agreement text from accepted proposals.

    accepted_proposals format:
      [{"agenda_point": "...", "proposals": ["...", ...]}, ...]
    """
    if not accepted_proposals:
        return "No accepted proposals to include in the agreement yet."

    context_lines = []
    for item in accepted_proposals:
        context_lines.append(f"** {item['agenda_point']} **")
        for prop in item["proposals"]:
            context_lines.append(f"  - {prop}")
    context = "\n".join(context_lines)
    lang_name = _lang_name(lang)

    prompt = f"""You are a professional mediator drafting a formal agreement.
Use neutral, clear, and constructive language.
Structure the agreement with: an introduction, numbered clauses for each
agreed point, and a closing statement.
Do NOT add signatures — those will be added separately.
Write the agreement in {lang_name}.

Mediation title: {mediation_title}

Accepted proposals per agenda point:
{context}

Agreement text:"""

    result = _ask(prompt)
    return result or f"Agreement for: {mediation_title}\n\n{context}"


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------

def translate_text(text: str, target_language: str, source_language: str = "auto") -> str:
    """
    Translate text to target_language (BCP-47 code, e.g. 'pt', 'fr').
    Output only the translated text — no preamble.
    """
    lang_name = _lang_name(target_language)

    prompt = f"""Translate the following text to {lang_name}.
Output ONLY the translated text, no explanation or preamble.

Text to translate:
{text}

Translation:"""

    return _ask(prompt)


# ---------------------------------------------------------------------------
# Summary  (existing feature)
# ---------------------------------------------------------------------------

def get_mediation_summary(posts: list, lang: str = "en") -> str:
    """
    Generate a neutral, balanced summary of the mediation exchange,
    identifying key points of agreement and disagreement.
    """
    lang_name = _lang_name(lang)

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


# ---------------------------------------------------------------------------
# Voice transcription stub  (existing feature)
# ---------------------------------------------------------------------------

def transcribe_voice_note(audio_base64: str, mime_type: str = "audio/webm") -> str:
    """
    Voice transcription stub.
    Ollama Cloud does not support audio input.
    Wire up OpenAI Whisper or Google Speech-to-Text for production.
    """
    logger.warning("Voice transcription called but no STT service is configured.")
    return "[Voice transcription not yet configured — please type your message.]"
