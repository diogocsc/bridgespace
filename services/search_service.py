"""
BridgeSpace - Search Service
Semantic similarity search over closed, consented mediation agreements.
Uses Ollama Cloud (gpt-oss:120b) for:
  - PII masking before indexing
  - Generating summaries and tags
  - Scoring relevance between a query and indexed agreements
  - Translating results into the user's preferred language
"""
import json
import logging
import math
from flask import current_app

logger = logging.getLogger(__name__)


# Re-use the core _ask() helper from ai_service to keep one HTTP client
def _ask(prompt: str) -> str:
    from services.ai_service import _ask as ollama_ask
    return ollama_ask(prompt)


# ── Privacy masking ────────────────────────────────────────────────────────

def mask_pii(text: str) -> str:
    """Strip all PII from text, preserving dispute substance."""
    prompt = f"""You are a privacy protection specialist for a confidential mediation platform.
Rewrite the text below so that NO personal information remains,
while preserving the nature of the dispute and the substance of any agreements.

Replace or remove:
- Full names → "Party A", "Party B", "Party C" (use consistently)
- Company names → "Company X" or "the Organisation"
- Locations (cities, addresses) → "the workplace", "the property", "the location"
- Specific dates → keep only month+year if relevant, else "recently"
- Phone numbers, emails, account numbers → [REDACTED]
- Any other identifying detail → [REDACTED]

Output ONLY the rewritten text. No preamble or explanation.

Original text:
{text}

Anonymised text:"""

    return _ask(prompt)


# ── Index metadata generation ──────────────────────────────────────────────

def generate_agreement_index(posts: list, agreement_text: str) -> dict:
    """
    From anonymised posts and a final agreement, produce structured metadata
    for search indexing. Returns dict with: summary, agreement_summary, tags, dispute_type.
    """
    posts_block = "\n\n".join(
        [f"[{p['role'].upper()}]: {p['content']}" for p in posts]
    )

    prompt = f"""You are an expert at analysing conflict mediation cases.
Given the anonymised mediation posts and final agreement below,
produce a JSON object with EXACTLY these keys:
{{
  "summary": "2-3 sentence neutral summary of the dispute (no personal info)",
  "agreement_summary": "2-3 sentence summary of what was agreed (no personal info)",
  "tags": ["tag1", "tag2", "tag3"],
  "dispute_type": "one of: workplace, family, commercial, property, neighbour, consumer, other"
}}

Choose 2-5 relevant tags from: workplace, salary, harassment, contract, inheritance,
noise, debt, custody, discrimination, partnership, tenancy, dismissal, boundary, other.

Output ONLY valid JSON. No preamble, no markdown fences.

MEDIATION POSTS:
{posts_block}

FINAL AGREEMENT:
{agreement_text}

JSON:"""

    raw = _ask(prompt)

    # Strip markdown fences if the model adds them
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        import re
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        logger.warning("Could not parse index JSON, using fallback. Raw: %s", raw[:200])
        return {
            "summary": raw[:300],
            "agreement_summary": agreement_text[:300],
            "tags": [],
            "dispute_type": "other",
        }


# ── Lightweight keyword scoring ────────────────────────────────────────────

def _keyword_score(query: str, document: str) -> float:
    """TF-style keyword overlap — used as the base relevance signal."""
    query_words = set(query.lower().split())
    doc_words   = document.lower().split()
    if not query_words or not doc_words:
        return 0.0
    matches = sum(1 for w in doc_words if w in query_words)
    return matches / len(doc_words)


# ── LLM relevance scoring ──────────────────────────────────────────────────

def _llm_score_candidates(query: str, candidates: list) -> list:
    """
    Ask the LLM to score each candidate's relevance to the user's query.
    Returns a list of floats in the same order as candidates.
    """
    summaries_block = "\n\n".join(
        [f"[{i+1}] {ag.masked_summary}" for i, (_, ag) in enumerate(candidates)]
    )

    prompt = f"""You are an expert mediator evaluating case similarity.
Given a user's case description and a list of anonymised past case summaries,
score each past case's relevance to the user's case from 0.0 (unrelated) to 1.0 (very similar).

Consider:
- Nature and domain of the dispute (workplace, family, commercial, etc.)
- The relationship between the parties
- The key issues and stakes involved
- Whether the resolution approach could be a useful precedent

Output ONLY a JSON object: {{"scores": [score1, score2, ...]}}
Scores must correspond to the cases in the order listed. No preamble.

USER'S CASE:
{query}

PAST CASES:
{summaries_block}

JSON:"""

    raw = _ask(prompt).strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        data = json.loads(raw)
        scores = data.get("scores", [])
        return [float(s) for s in scores]
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.warning("LLM scoring parse failed: %s — raw: %s", exc, raw[:200])
        return []


# ── Main search function ───────────────────────────────────────────────────

def search_similar_cases(
    query: str,
    user_lang: str = "en",
    dispute_type_filter: str = "",
    tag_filter: str = "",
    limit: int = 8,
) -> list:
    """
    Search indexed agreements for cases similar to the user's query.
    Returns a ranked list of result dicts with privacy-masked content.
    """
    from models import MediationAgreement
    from services.ai_service import translate_text

    # Fetch indexed agreements, apply optional filters
    q = MediationAgreement.query.filter_by(is_indexed=True)
    if dispute_type_filter:
        q = q.filter_by(dispute_type=dispute_type_filter)
    agreements = q.all()

    if not agreements:
        return []

    # ── Stage 1: keyword pre-scoring ──────────────────────────────────────
    scored = []
    for ag in agreements:
        # Tag filter
        if tag_filter:
            tags = ag.get_tags_list()
            if tag_filter.lower() not in [t.lower() for t in tags]:
                continue

        score = _keyword_score(
            query,
            ag.masked_summary + " " + ag.masked_agreement
        )
        scored.append((score, ag))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Take top 20 for LLM re-ranking
    candidates = scored[:20]
    if not candidates:
        return []

    # ── Stage 2: LLM re-ranking ───────────────────────────────────────────
    llm_scores = _llm_score_candidates(query, candidates)

    reranked = []
    for i, (kw_score, ag) in enumerate(candidates):
        if i < len(llm_scores):
            # Blend: 30% keyword + 70% LLM semantic
            blended = (kw_score * 0.3) + (llm_scores[i] * 0.7)
        else:
            blended = kw_score
        reranked.append((blended, ag))

    reranked.sort(key=lambda x: x[0], reverse=True)

    # ── Stage 3: build result objects ─────────────────────────────────────
    results = []
    for score, ag in reranked[:limit]:
        if score < 0.05:
            continue

        summary   = ag.masked_summary
        agreement = ag.masked_agreement

        # Translate to user's language if not English
        if user_lang != "en":
            try:
                summary   = translate_text(summary,   user_lang, "en")
                agreement = translate_text(agreement, user_lang, "en")
            except Exception as exc:
                logger.warning("Translation failed: %s", exc)

        results.append({
            "id":            ag.id,
            "score":         round(score, 3),
            "relevance_pct": min(100, int(score * 100)),
            "summary":       summary,
            "agreement":     agreement,
            "tags":          ag.get_tags_list(),
            "created_at":    ag.created_at.strftime("%b %Y"),
        })

    return results


# ── Index a closed mediation ───────────────────────────────────────────────

def index_closed_mediation(mediation, agreement_text: str) -> bool:
    """
    Called when a mediation is closed and all parties have consented.
    Masks PII, generates searchable metadata, persists to MediationAgreement.
    """
    from extensions import db
    from models import MediationAgreement, MediationParticipant, Post

    # Require all parties (non-mediator) to have given per-mediation consent for search sharing
    participants = MediationParticipant.query.filter_by(
        mediation_id=mediation.id, is_active=True
    ).all()
    parties = [p for p in participants if getattr(p, "role", None) != "mediator"]
    all_consented = bool(parties) and all(
        getattr(p, "consent_search_share", None) is True for p in parties
    )

    if not all_consented and not getattr(mediation, "shared_publicly", False):
        logger.info("Mediation %s not indexed: consent not given by all parties.",
                    mediation.id)
        return False

    try:
        # Gather submitted posts
        posts = Post.query.filter_by(
            mediation_id=mediation.id, is_draft=False
        ).order_by(Post.created_at).all()

        # Mask PII from each post
        masked_posts = []
        for i, post in enumerate(posts):
            content = post.get_display_content()
            masked  = mask_pii(content)
            role    = f"Party {chr(65 + i)}"   # A, B, C …
            masked_posts.append({"role": role, "content": masked})

        masked_agreement = mask_pii(agreement_text)

        # Generate searchable index metadata
        index_data = generate_agreement_index(masked_posts, masked_agreement)

        # Upsert MediationAgreement record
        ag = MediationAgreement.query.filter_by(
            mediation_id=mediation.id
        ).first()
        if not ag:
            ag = MediationAgreement(mediation_id=mediation.id)
            db.session.add(ag)

        ag.masked_summary   = index_data.get("summary", "")
        ag.masked_agreement = index_data.get("agreement_summary", masked_agreement)
        ag.set_tags_list(index_data.get("tags", []))
        ag.is_indexed       = True
        db.session.commit()

        logger.info("Mediation %s indexed successfully.", mediation.id)
        return True

    except Exception as exc:
        logger.error("Failed to index mediation %s: %s", mediation.id, exc)
        db.session.rollback()
        return False


# ── Utility helpers ────────────────────────────────────────────────────────

def get_all_tags() -> list:
    """Return all unique tags across indexed agreements, sorted by frequency."""
    from models import MediationAgreement
    agreements = MediationAgreement.query.filter_by(is_indexed=True).all()
    tag_counts = {}
    for ag in agreements:
        for tag in ag.get_tags_list():
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    return sorted(tag_counts.keys(), key=lambda t: -tag_counts[t])


def get_dispute_types() -> list:
    return [
        ("workplace",  "Workplace"),
        ("family",     "Family"),
        ("commercial", "Commercial"),
        ("property",   "Property"),
        ("neighbour",  "Neighbour"),
        ("consumer",   "Consumer"),
        ("other",      "Other"),
    ]