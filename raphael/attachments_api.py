"""Authenticated multipart chat endpoint; uploads are processed in memory only."""
from flask import Blueprint, request, jsonify
from flask_login import login_required
from attachment_processor import prepare_attachments, AttachmentError

attachments_bp = Blueprint("attachments", __name__)


@attachments_bp.post("/api/chat-with-files")
@login_required
def chat_with_files():
    # Import after app initialization to avoid a circular import.
    import app as core
    request.max_content_length = 16 * 1024 * 1024
    conversation_id = request.form.get("conversation_id", type=int)
    message = (request.form.get("message") or "").strip()
    uploads = request.files.getlist("files")
    if isinstance(conversation_id, bool) or not conversation_id or conversation_id <= 0:
        return jsonify({"error": "conversation_id no válido."}), 400
    if len(message) > 10000:
        return jsonify({"error": "El mensaje supera el límite de 10000 caracteres."}), 413
    if not uploads or not any(item and item.filename for item in uploads):
        return jsonify({"error": "Adjunta al menos un archivo."}), 400
    with core.sqlite3.connect(core.DB_PATH) as conn:
        exists = conn.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    if not exists:
        return jsonify({"error": "Conversación no encontrada."}), 404
    if not core.get_anthropic_api_key():
        return jsonify({"error": "Falta configurar ANTHROPIC_API_KEY."}), 500
    try:
        extracted, media_blocks, filenames = prepare_attachments(uploads)
    except AttachmentError as error:
        return jsonify({"error": str(error)}), 400

    user_text = message or "Analiza los archivos adjuntos."
    stored_text = user_text + "\n\n" + extracted
    core.maybe_set_conversation_title(conversation_id, user_text)
    core.save_message(conversation_id, "user", stored_text)
    try:
        history = core.get_recent_history(conversation_id)
        content = [{"type": "text", "text": stored_text}]
        content.extend(media_blocks)
        history[-1] = {"role": "user", "content": content}
        response = core.create_anthropic_client().messages.create(
            model=core.get_config_value("ANTHROPIC_MODEL", core.DEFAULT_MODEL),
            max_tokens=core.get_int_config_value("ANTHROPIC_MAX_TOKENS", core.DEFAULT_MAX_TOKENS),
            system=core.build_system_prompt(user_text + " " + " ".join(filenames)),
            messages=history,
            tools=[{
                "type": core.get_config_value("ANTHROPIC_WEB_SEARCH_TOOL", core.DEFAULT_WEB_SEARCH_TOOL),
                "name": "web_search",
                "max_uses": 3,
            }],
        )
        raw_text = "".join(block.text for block in response.content if block.type == "text")
        raw_text = core.strip_and_store_memories(raw_text)
        core.save_message(conversation_id, "assistant", raw_text)
        return jsonify({"reply": raw_text, "attachments": filenames})
    except core.anthropic.AuthenticationError:
        return jsonify({"error": "La clave de Anthropic no es válida."}), 401
    except core.anthropic.APIError:
        core.app.logger.exception("Anthropic attachment request failed")
        return jsonify({"error": "No se pudo contactar con el proveedor de IA."}), 502
