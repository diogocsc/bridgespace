"""
BridgeSpace - Search Routes
Allows users to search anonymised past agreements for similar cases.
"""
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from services.search_service import (
    search_similar_cases,
    get_all_tags,
    get_dispute_types,
    index_closed_mediation,
)

search_bp = Blueprint('search', __name__, url_prefix='/search')


@search_bp.route('/', methods=['GET'])
@login_required
def search_page():
    """Render the search UI."""
    tags = get_all_tags()
    dispute_types = get_dispute_types()
    return render_template(
        'search/search.html',
        tags=tags,
        dispute_types=dispute_types,
        user_lang=current_user.preferred_language,
    )


@search_bp.route('/query', methods=['POST'])
@login_required
def query():
    """
    AJAX endpoint — receives a case description and returns
    ranked, privacy-masked similar agreements as JSON.
    """
    data = request.get_json() or {}
    query_text = data.get('query', '').strip()
    dispute_type = data.get('dispute_type', '')
    tag_filter = data.get('tag', '')
    limit = min(int(data.get('limit', 8)), 20)

    if not query_text:
        return jsonify({'error': 'Please describe your case to search.'}), 400
    if len(query_text) < 20:
        return jsonify({'error': 'Please provide a more detailed description '
                                 '(at least 20 characters).'}), 400

    try:
        results = search_similar_cases(
            query=query_text,
            user_lang=current_user.preferred_language,
            dispute_type_filter=dispute_type,
            tag_filter=tag_filter,
            limit=limit,
        )
        return jsonify({'results': results, 'total': len(results)})
    except Exception as exc:
        return jsonify({'error': f'Search failed: {str(exc)}'}), 500


@search_bp.route('/index-mediation/<int:mediation_id>', methods=['POST'])
@login_required
def trigger_index(mediation_id):
    """
    Manually trigger indexing of a closed mediation (creator only).
    Called after a mediation is closed and agreement text is submitted.
    """
    from models import Mediation
    med = Mediation.query.get_or_404(mediation_id)
    if med.creator_id != current_user.id:
        return jsonify({'error': 'Only the mediation creator can submit an agreement.'}), 403
    if med.status != 'closed':
        return jsonify({'error': 'Mediation must be closed before indexing.'}), 400

    data = request.get_json() or {}
    agreement_text = data.get('agreement_text', '').strip()
    if not agreement_text:
        return jsonify({'error': 'Agreement text is required.'}), 400

    success = index_closed_mediation(med, agreement_text)
    if success:
        return jsonify({'success': True,
                        'message': 'Agreement indexed for future search.'})
    return jsonify({'success': False,
                    'message': 'Indexing skipped — participant consent required.'}), 200