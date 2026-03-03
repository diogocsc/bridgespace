"""BridgeSpace - JSON API Routes"""
from flask import Blueprint, jsonify, request, abort
from flask_login import login_required, current_user
from extensions import db, socketio
from models import Mediation, Post, MediationParticipant
from services.ai_service import reformulate_post, translate_text
from datetime import datetime

api_bp = Blueprint('api', __name__)


def mediation_access(mediation_id):
    """Returns mediation if current user has access, else aborts 403."""
    med = Mediation.query.get_or_404(mediation_id)
    if not med.get_participant(current_user):
        abort(403)
    return med


@api_bp.route('/reformulate', methods=['POST'])
@login_required
def api_reformulate():
    data = request.get_json()
    text = (data or {}).get('text', '').strip()
    mediation_id = (data or {}).get('mediation_id')
    context = ''
    if mediation_id:
        med = mediation_access(mediation_id)
        context = med.description or med.title
    if not text:
        return jsonify({'error': 'No text provided'}), 400
    try:
        reformulated = reformulate_post(text, context, current_user.preferred_language)
        return jsonify({'reformulated': reformulated})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@api_bp.route('/translate', methods=['POST'])
@login_required
def api_translate():
    data = request.get_json()
    text = (data or {}).get('text', '').strip()
    post_id = (data or {}).get('post_id')
    target_lang = (data or {}).get('target_lang', current_user.preferred_language)

    if not text:
        return jsonify({'error': 'No text provided'}), 400

    # Check cache if post_id given
    if post_id:
        post = Post.query.get_or_404(post_id)
        cached = post.get_translation(target_lang)
        if cached:
            return jsonify({'translation': cached, 'cached': True})

    try:
        translation = translate_text(text, target_lang)
        if post_id:
            post = Post.query.get(post_id)
            if post:
                post.set_translation(target_lang, translation)
                db.session.commit()
        return jsonify({'translation': translation})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@api_bp.route('/post/submit', methods=['POST'])
@login_required
def api_submit_post():
    data = request.get_json()
    mediation_id = (data or {}).get('mediation_id')
    original = (data or {}).get('original', '').strip()
    reformulated = (data or {}).get('reformulated', '').strip()
    submitted_version = (data or {}).get('submitted_version', 'original')
    input_method = (data or {}).get('input_method', 'text')

    if not mediation_id or not original:
        return jsonify({'error': 'Missing required fields'}), 400

    med = mediation_access(mediation_id)
    if med.status == 'closed':
        return jsonify({'error': 'Mediation is closed'}), 403

    post = Post(
        mediation_id=mediation_id,
        author_id=current_user.id,
        original_content=original,
        reformulated_content=reformulated if reformulated else None,
        submitted_version=submitted_version,
        input_method=input_method,
        is_draft=False,
        created_at=datetime.utcnow()
    )
    db.session.add(post)
    db.session.commit()

    # Emit via SocketIO for live mediations
    room = f"mediation_{mediation_id}"
    socketio.emit('new_post', {
        'post_id': post.id,
        'author': current_user.display_name,
        'content': post.get_display_content(),
        'timestamp': post.created_at.isoformat()
    }, room=room)

    return jsonify({'success': True, 'post_id': post.id})


@api_bp.route('/post/<int:post_id>/original')
@login_required
def api_get_original(post_id):
    post = Post.query.get_or_404(post_id)
    # Verify access
    mediation_access(post.mediation_id)
    return jsonify({
        'original': post.original_content,
        'submitted_version': post.submitted_version
    })