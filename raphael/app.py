import os
import re
import sqlite3
import hmac
from datetime import datetime, timezone, timedelta

from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session
from dotenv import load_dotenv
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
import anthropic
import memory_manager

load_dotenv()

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("FLASK_SECRET_KEY"),
    MAX_CONTENT_LENGTH=1024 * 1024,
    MAX_FORM_MEMORY_SIZE=64 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("RAPHAEL_COOKIE_SECURE", "true").lower() == "true",
    SESSION_COOKIE_NAME="raphael_session",
    SESSION_COOKIE_PATH="/",
    SESSION_COOKIE_DOMAIN=None,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    WTF_CSRF_TIME_LIMIT=60 * 60 * 12,
    TRUSTED_HOSTS=[host.strip() for host in os.environ.get("RAPHAEL_TRUSTED_HOSTS", "").split(",") if host.strip()] or None,
)

secret_key = app.config["SECRET_KEY"]
if not secret_key or len(secret_key) < 32 or "REEMPLAZAR" in secret_key:
    raise RuntimeError("FLASK_SECRET_KEY debe contener al menos 32 caracteres aleatorios y no puede ser un valor de ejemplo.")


def get_trusted_proxy_hops(environment):
    raw_value = os.environ.get("RAPHAEL_TRUSTED_PROXY_HOPS", "0").strip()
    try:
        hops = int(raw_value)
    except ValueError as error:
        raise RuntimeError("RAPHAEL_TRUSTED_PROXY_HOPS debe ser un entero entre 0 y 5.") from error
    if hops < 0 or hops > 5:
        raise RuntimeError("RAPHAEL_TRUSTED_PROXY_HOPS debe ser un entero entre 0 y 5.")
    # Cero significa no confiar en cabeceras reenviadas por proxies.
    # En ese caso el rate limiter verá la IP del proxy del alojamiento.
    return hops


def validate_production_config(config, environment):
    if environment != "production":
        return
    if not config.get("TRUSTED_HOSTS"):
        raise RuntimeError("RAPHAEL_TRUSTED_HOSTS es obligatorio en producción.")
    if not config.get("SESSION_COOKIE_SECURE"):
        raise RuntimeError("RAPHAEL_COOKIE_SECURE=true es obligatorio en producción.")
    storage = os.environ.get("RAPHAEL_LIMITER_STORAGE", "memory://").strip().lower()
    if not storage.startswith(("redis://", "rediss://")):
        raise RuntimeError("En producción, RAPHAEL_LIMITER_STORAGE debe apuntar a Redis (redis:// o rediss://).")
    get_trusted_proxy_hops(environment)


environment = os.environ.get("RAPHAEL_ENV", "development").strip().lower()
validate_production_config(app.config, environment)
trusted_proxy_hops = get_trusted_proxy_hops(environment)
if trusted_proxy_hops:
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=trusted_proxy_hops,
        x_proto=trusted_proxy_hops,
        x_host=0,
        x_port=0,
        x_prefix=0,
    )

login_manager = LoginManager()
login_manager.init_app(app)
csrf = CSRFProtect(app)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["120 per hour"],
    storage_uri=os.environ.get("RAPHAEL_LIMITER_STORAGE", "memory://"),
)

class RaphaelOwner(UserMixin):
    id = "owner"

@login_manager.user_loader
def load_user(user_id):
    if user_id == "owner" and os.environ.get("RAPHAEL_USERNAME") and os.environ.get("RAPHAEL_PASSWORD_HASH"):
        return RaphaelOwner()
    return None

@login_manager.unauthorized_handler
def unauthorized():
    if request.path.startswith("/api/"):
        return jsonify({"error": "Autenticación requerida."}), 401
    return redirect(url_for("login"))


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), geolocation=(), payment=()")
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'; img-src 'self' data:; style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; script-src 'self' https://cdnjs.cloudflare.com https://unpkg.com")
    return response

PLACEHOLDER_API_KEYS = {"", "tu_api_key_aqui", "sk-ant-..."}
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 2048
DEFAULT_WEB_SEARCH_TOOL = "web_search_20250305"

DB_PATH = os.environ.get("RAPHAEL_DB_PATH") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance", "raphael.db")
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)

HISTORY_LIMIT = 60  # mensajes recientes que se mandan a la API por conversacion
MEMORY_PATTERN = re.compile(r"\[MEMORIA:\s*([^\]]+)\]", re.IGNORECASE)

BASE_SYSTEM_PROMPT = """Eres Raphael, un copiloto de IA diseñado para ayudar a cada usuario.
Tu proposito es ayudarle a pensar, decidir, crear, aprender y resolver problemas
con un nivel alto de claridad, criterio y autonomia.

Identidad y tono:
- Dirígete a la persona de forma natural y utiliza su nombre solo si ella lo proporciona.
- Tono calido, inteligente, directo y con presencia. Puedes tener personalidad,
  pero sin fingir capacidades, experiencias o emociones humanas que no tienes.
- Responde en el mismo idioma que use la persona.
- Se concreto primero; profundiza cuando el tema lo pida.

Forma de pensar:
- No te quedes en una respuesta generica. Identifica el objetivo real, los
  supuestos, riesgos, pasos siguientes y la mejor accion practica.
- Si una pregunta es ambigua pero puedes avanzar con una suposicion razonable,
  hazlo y di cual asumiste. Pregunta solo cuando sea necesario.
- Para tareas complejas, organiza la solucion por fases, prioridades o checklist.
- Si detectas una idea mejor que la pedida, proponla con brevedad.
- Si no sabes algo, dilo y busca o pide el dato que falta.

Capacidades:
- Tienes acceso a busqueda web. Usala cuando la respuesta dependa de informacion
  actual, local, reciente o cambiante: precios, leyes, noticias, clima, horarios,
  eventos, software, documentacion, disponibilidad o datos que puedan caducar.
- Cuando uses busqueda, integra la informacion con criterio y menciona
  brevemente la fuente.
- Puedes ayudar con programacion, estudio, escritura, estrategia, organizacion,
  decisiones personales, planes, creatividad y analisis.
- Puedes ser proactivo, pero no tomes decisiones irreversibles por la persona.

Memoria:
- Tienes memoria a largo plazo compartida entre conversaciones.
- Cuando la persona te cuente algo importante y duradero, como preferencias,
  proyectos, metas, datos personales relevantes o formas en que quiere trabajar,
  guardalo al final de tu respuesta con una linea exacta:
  [MEMORIA: el dato en pocas palabras]
- Puedes guardar varias memorias si de verdad son utiles, una por linea.
- No guardes detalles triviales, temporales, sensibles sin necesidad, ni cosas
  que solo sirvan para una unica conversacion.

Formato:
- Usa markdown cuando mejore la claridad: encabezados, listas, tablas o bloques
  de codigo. En respuestas cortas, conversa con naturalidad.
- Para codigo, da soluciones completas y ejecutables cuando sea posible.
- Evita relleno, disculpas largas y rodeos.
"""


def get_anthropic_api_key():
    load_dotenv(override=True)
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if api_key in PLACEHOLDER_API_KEYS:
        return None
    return api_key


def get_config_value(name, default):
    load_dotenv(override=True)
    return (os.environ.get(name) or default).strip() or default


def get_int_config_value(name, default):
    raw_value = get_config_value(name, str(default))
    try:
        return int(raw_value)
    except ValueError:
        return default


def create_anthropic_client():
    return anthropic.Anthropic(api_key=get_anthropic_api_key())


def init_db():
    conn = sqlite3.connect(DB_PATH)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fact TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    memory_manager.ensure_schema(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    # Migracion suave: si messages venia de una version anterior sin conversation_id
    cols = [row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()]
    if "conversation_id" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN conversation_id INTEGER")

    # Asegurar que exista al menos una conversacion, y asignar mensajes huerfanos a ella
    first_conv = conn.execute(
        "SELECT id FROM conversations ORDER BY id ASC LIMIT 1"
    ).fetchone()
    if first_conv is None:
        conn.execute(
            "INSERT INTO conversations (title, created_at) VALUES (?, ?)",
            ("Conversación 1", datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        first_conv = conn.execute(
            "SELECT id FROM conversations ORDER BY id ASC LIMIT 1"
        ).fetchone()

    conn.execute(
        "UPDATE messages SET conversation_id = ? WHERE conversation_id IS NULL",
        (first_conv[0],),
    )

    conn.commit()
    conn.close()


def list_conversations():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id, title, created_at FROM conversations ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return [{"id": r[0], "title": r[1], "created_at": r[2]} for r in rows]


def create_conversation(title="Nueva conversación"):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "INSERT INTO conversations (title, created_at) VALUES (?, ?)",
        (title, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def maybe_set_conversation_title(conversation_id, first_message):
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (conversation_id,)
    ).fetchone()[0]
    if count == 0:
        clean = first_message.strip()
        title = clean[:40] + ("..." if len(clean) > 40 else "")
        conn.execute(
            "UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id)
        )
        conn.commit()
    conn.close()


def save_message(conversation_id, role, content):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (conversation_id, role, content, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def get_recent_history(conversation_id, limit=HISTORY_LIMIT):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id ASC",
        (conversation_id,),
    ).fetchall()
    conn.close()
    rows = rows[-limit:]
    return [{"role": role, "content": content} for role, content in rows]


def save_memory(fact):
    fact = " ".join(fact.split()).strip()
    if not fact:
        return
    try:
        memory_manager.create_memory(
            DB_PATH,
            fact,
            category="general",
            source="anthropic_suggestion",
            confidence=0.5,
            status="pending",
        )
    except ValueError:
        # Duplicate suggestions are ignored; owner approval is always explicit.
        return


def get_memories():
    return memory_manager.active_memory_facts(DB_PATH)


def build_system_prompt(query=None):
    memories = memory_manager.retrieve_relevant_memories(DB_PATH, query or "", limit=8)
    current_context = (
        "\n\nContexto actual:\n"
        f"- Fecha y hora UTC del servidor: {datetime.now(timezone.utc).isoformat()}\n"
        "- Si el usuario habla de hoy, manana o fechas relativas, interpreta el contexto con cuidado."
    )
    if not memories:
        return BASE_SYSTEM_PROMPT + current_context
    memory_lines = "\n".join(f"- {fact}" for fact in memories)
    return (
        BASE_SYSTEM_PROMPT
        + "\n\nContexto útil de conversaciones anteriores:\n"
        + memory_lines
        + current_context
    )


def strip_and_store_memories(raw_text):
    for memory_match in MEMORY_PATTERN.finditer(raw_text):
        save_memory(memory_match.group(1))
    return MEMORY_PATTERN.sub("", raw_text).strip()


init_db()

from attachments_api import attachments_bp
app.register_blueprint(attachments_bp)
app.view_functions["attachments.chat_with_files"] = limiter.limit("10 per minute")(app.view_functions["attachments.chat_with_files"])


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        expected_username = os.environ.get("RAPHAEL_USERNAME", "")
        password_hash = os.environ.get("RAPHAEL_PASSWORD_HASH", "")
        if expected_username and password_hash and hmac.compare_digest(username, expected_username) and check_password_hash(password_hash, password):
            session.clear()
            login_user(RaphaelOwner(), remember=False, fresh=True)
            session.permanent = True
            return redirect(url_for("index"))
        flash("Credenciales incorrectas.", "error")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/api/conversations", methods=["GET"])
@login_required
def conversations_list():
    return jsonify({"conversations": list_conversations()})


@app.route("/api/conversations", methods=["POST"])
@login_required
def conversations_create():
    new_id = create_conversation()
    return jsonify({"id": new_id, "title": "Nueva conversación"})


@app.route("/api/history", methods=["GET"])
@login_required
def history_endpoint():
    conversation_id = request.args.get("conversation_id", type=int)
    if not conversation_id or conversation_id <= 0:
        return jsonify({"error": "Falta un conversation_id válido."}), 400
    with sqlite3.connect(DB_PATH) as conn:
        exists = conn.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    if not exists:
        return jsonify({"error": "Conversación no encontrada."}), 404
    return jsonify({"history": get_recent_history(conversation_id, limit=50)})


@app.route("/api/chat", methods=["POST"])
@login_required
@limiter.limit("30 per minute")
def chat():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Se esperaba un objeto JSON válido."}), 400
    conversation_id = data.get("conversation_id")
    if isinstance(conversation_id, bool) or not isinstance(conversation_id, int):
        return jsonify({"error": "conversation_id no válido."}), 400
    user_message = data.get("message")
    if not isinstance(user_message, str):
        return jsonify({"error": "El mensaje debe ser texto."}), 400
    user_message = user_message.strip()

    if conversation_id <= 0:
        return jsonify({"error": "conversation_id no válido."}), 400
    if not user_message:
        return jsonify({"error": "No se recibio ningun mensaje."}), 400
    if len(user_message) > 10000:
        return jsonify({"error": "El mensaje supera el límite de 10000 caracteres."}), 413
    with sqlite3.connect(DB_PATH) as conn:
        exists = conn.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    if not exists:
        return jsonify({"error": "Conversación no encontrada."}), 404
    if not get_anthropic_api_key():
        return jsonify({"error": "Falta configurar ANTHROPIC_API_KEY en el archivo .env"}), 500

    maybe_set_conversation_title(conversation_id, user_message)
    save_message(conversation_id, "user", user_message)

    try:
        client = create_anthropic_client()
        response = client.messages.create(
            model=get_config_value("ANTHROPIC_MODEL", DEFAULT_MODEL),
            max_tokens=get_int_config_value("ANTHROPIC_MAX_TOKENS", DEFAULT_MAX_TOKENS),
            system=build_system_prompt(user_message),
            messages=get_recent_history(conversation_id),
            tools=[
                {
                    "type": get_config_value(
                        "ANTHROPIC_WEB_SEARCH_TOOL", DEFAULT_WEB_SEARCH_TOOL
                    ),
                    "name": "web_search",
                    "max_uses": 3,
                }
            ],
        )
        raw_text = "".join(
            block.text for block in response.content if block.type == "text"
        )
        raw_text = strip_and_store_memories(raw_text)

        save_message(conversation_id, "assistant", raw_text)
        return jsonify({"reply": raw_text})

    except anthropic.AuthenticationError:
        return jsonify(
            {"error": "La ANTHROPIC_API_KEY no es valida. Revisa el archivo .env."}
        ), 401
    except anthropic.APIError:
        app.logger.exception("Anthropic API request failed")
        return jsonify({"error": "No se pudo contactar con el proveedor de IA. Inténtalo de nuevo."}), 502


@app.route("/api/memories", methods=["GET"])
@login_required
def memories_list_endpoint():
    status = request.args.get("status")
    category = request.args.get("category")
    try:
        return jsonify({"memories": memory_manager.list_memories(DB_PATH, status, category)})
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@app.route("/api/memories", methods=["POST"])
@login_required
def memories_create_endpoint():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Se esperaba un objeto JSON válido."}), 400
    try:
        memory_id = memory_manager.create_memory(
            DB_PATH,
            data.get("fact", ""),
            category=data.get("category", "general"),
            source="owner",
            confidence=data.get("confidence", 1.0),
            status="active",
            expires_at=data.get("expires_at"),
        )
        return jsonify({"id": memory_id, "status": "active"}), 201
    except (TypeError, ValueError) as error:
        return jsonify({"error": str(error)}), 400


@app.route("/api/memories/<int:memory_id>", methods=["PATCH"])
@login_required
def memories_update_endpoint(memory_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Se esperaba un objeto JSON válido."}), 400
    try:
        fields = {key: data[key] for key in ("fact", "category", "confidence", "expires_at", "status") if key in data}
        if not fields:
            return jsonify({"error": "No se proporcionaron campos para actualizar."}), 400
        memory_manager.update_memory(DB_PATH, memory_id, **fields)
        return jsonify({"ok": True})
    except LookupError as error:
        return jsonify({"error": str(error)}), 404
    except (TypeError, ValueError) as error:
        return jsonify({"error": str(error)}), 400


@app.route("/api/memories/<int:memory_id>", methods=["DELETE"])
@login_required
def memories_delete_endpoint(memory_id):
    try:
        memory_manager.delete_memory(DB_PATH, memory_id)
        return jsonify({"ok": True})
    except LookupError as error:
        return jsonify({"error": str(error)}), 404


@app.route("/memories")
@login_required
def memories_page():
    return render_template("memories.html")




@app.route("/api/memories/<int:memory_id>/proposals", methods=["POST"])
@login_required
def memory_proposal_create_endpoint(memory_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Se esperaba un objeto JSON válido."}), 400
    proposed_fact = data.get("proposed_fact", "")
    try:
        matches = memory_manager.find_memory_conflicts(DB_PATH, proposed_fact)
        similarity = next(
            (item["similarity"] for item in matches if item["memory"]["id"] == memory_id),
            0.0,
        )
        proposal_id = memory_manager.create_memory_proposal(
            DB_PATH,
            memory_id,
            proposed_fact,
            reason=data.get("reason", "Posible actualización propuesta por el propietario"),
            similarity=similarity,
        )
        return jsonify({"id": proposal_id, "status": "pending", "similarity": similarity}), 201
    except LookupError as error:
        return jsonify({"error": str(error)}), 404
    except (TypeError, ValueError) as error:
        return jsonify({"error": str(error)}), 400


@app.route("/api/memory-proposals", methods=["GET"])
@login_required
def memory_proposals_list_endpoint():
    status = request.args.get("status", "pending")
    try:
        return jsonify({"proposals": memory_manager.list_memory_proposals(DB_PATH, status)})
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@app.route("/api/memory-proposals/<int:proposal_id>", methods=["PATCH"])
@login_required
def memory_proposal_decide_endpoint(proposal_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Se esperaba un objeto JSON válido."}), 400
    try:
        new_memory_id = memory_manager.decide_memory_proposal(
            DB_PATH, proposal_id, data.get("decision", "")
        )
        return jsonify({"ok": True, "new_memory_id": new_memory_id})
    except LookupError as error:
        return jsonify({"error": str(error)}), 404
    except ValueError as error:
        return jsonify({"error": str(error)}), 400




@app.route("/api/memories/<int:memory_id>/history", methods=["GET"])
@login_required
def memory_history_endpoint(memory_id):
    try:
        if not any(item["id"] == memory_id for item in memory_manager.list_memories(DB_PATH)):
            raise LookupError("Recuerdo no encontrado.")
    except LookupError as error:
        return jsonify({"error": str(error)}), 404
    return jsonify({"history": memory_manager.memory_history(DB_PATH, memory_id)})


@app.route("/api/memories/<int:memory_id>/relationships", methods=["GET", "POST"])
@login_required
def memory_relationships_endpoint(memory_id):
    try:
        if not any(item["id"] == memory_id for item in memory_manager.list_memories(DB_PATH)):
            raise LookupError("Recuerdo no encontrado.")
    except LookupError as error:
        return jsonify({"error": str(error)}), 404
    if request.method == "GET":
        return jsonify({"relationships": memory_manager.list_relationships(DB_PATH, memory_id)})
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Se esperaba un objeto JSON válido."}), 400
    try:
        target_memory_id = data.get("target_memory_id")
        if isinstance(target_memory_id, bool) or not isinstance(target_memory_id, int):
            raise ValueError("target_memory_id no válido.")
        memory_manager.add_relationship(DB_PATH, memory_id, target_memory_id, data.get("relation", "related_to"))
        return jsonify({"ok": True}), 201
    except LookupError as error:
        return jsonify({"error": str(error)}), 404
    except (TypeError, ValueError) as error:
        return jsonify({"error": str(error)}), 400


@app.route("/api/memories/timeline", methods=["GET"])
@login_required
def memories_timeline_endpoint():
    return jsonify({"memories": memory_manager.temporal_memories(DB_PATH, request.args.get("include_expired") == "true")})


@app.route("/api/memories/semantic-review", methods=["POST"])
@login_required
@limiter.limit("5 per minute")
def memory_semantic_review_endpoint():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Se esperaba un objeto JSON válido."}), 400
    fact = data.get("fact")
    if not isinstance(fact, str):
        return jsonify({"error": "El campo fact debe ser texto."}), 400
    proposed_fact = " ".join(fact.split()).strip()
    if not proposed_fact or len(proposed_fact) > 2000:
        return jsonify({"error": "El texto debe contener entre 1 y 2000 caracteres."}), 400
    candidates = memory_manager.find_memory_conflicts(DB_PATH, proposed_fact, threshold=0.08)
    if not candidates:
        return jsonify({"candidates": [], "message": "No se encontraron recuerdos relacionados por términos."})
    shortlist = candidates[:8]
    prompt = (
        "Evalúa si el hecho nuevo contradice, actualiza, complementa o no guarda relación con los recuerdos existentes. "
        "No inventes datos. Devuelve SOLO JSON válido con una clave results, una lista con memory_id, relation "
        "(contradicts|updates|complements|unrelated), confidence (0 a 1) y reason breve. "
        "No decidas ni modifiques recuerdos.\\nHECHO NUEVO: " + proposed_fact +
        "\\nRECUERDOS: " + str([{"memory_id": item["memory"]["id"], "fact": item["memory"]["fact"]} for item in shortlist])
    )
    try:
        response = create_anthropic_client().messages.create(
            model=get_config_value("ANTHROPIC_MODEL", DEFAULT_MODEL),
            max_tokens=900,
            system="Eres un clasificador conservador de relaciones entre recuerdos. Devuelve únicamente JSON.",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(block.text for block in response.content if block.type == "text")
        import json
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end < start:
            raise ValueError("Respuesta sin objeto JSON.")
        parsed = json.loads(raw[start:end + 1])
        if not isinstance(parsed, dict) or not isinstance(parsed.get("results"), list):
            raise ValueError("Formato de respuesta no válido.")
        allowed = {"contradicts", "updates", "complements", "unrelated"}
        allowed_ids = {item["memory"]["id"] for item in shortlist}
        results = []
        for item in parsed["results"]:
            if not isinstance(item, dict) or item.get("memory_id") not in allowed_ids:
                continue
            relation = item.get("relation")
            confidence = item.get("confidence")
            if relation not in allowed or isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
                continue
            results.append({
                "memory_id": item["memory_id"],
                "relation": relation,
                "confidence": float(confidence),
                "reason": str(item.get("reason", ""))[:500],
            })
        return jsonify({"results": results, "note": "Análisis asistido por Claude; requiere revisión humana."})
    except (anthropic.APIError, ValueError, KeyError, TypeError) as error:
        app.logger.warning("Semantic memory review failed: %s", type(error).__name__)
        return jsonify({"error": "No se pudo completar la revisión semántica."}), 502


if __name__ == "__main__":
    port = int(get_config_value("FLASK_PORT", "5000"))
    app.run(debug=False, host="127.0.0.1", port=port)