import os
import tempfile
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-key-that-is-long-enough")
os.environ.setdefault("RAPHAEL_USERNAME", "owner")
from werkzeug.security import generate_password_hash
os.environ["RAPHAEL_PASSWORD_HASH"] = generate_password_hash("test-password")
os.environ.setdefault("RAPHAEL_DB_PATH", os.path.join(tempfile.gettempdir(), "raphael-security-tests.sqlite3"))
os.environ.setdefault("RAPHAEL_COOKIE_SECURE", "false")

from app import app, limiter


@pytest.fixture()
def client():
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
    limiter.storage.reset()
    with app.test_client() as client:
        yield client


def test_private_pages_redirect_to_login(client):
    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_api_requires_authentication(client):
    response = client.get("/api/conversations")
    assert response.status_code == 401


def test_login_page_is_available(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert b"Raphael" in response.data


def test_csrf_blocks_unauthenticated_post(client):
    app.config.update(WTF_CSRF_ENABLED=True)
    response = client.post("/login", data={"username": os.environ["RAPHAEL_USERNAME"], "password": "test-password"})
    assert response.status_code == 400
    app.config.update(WTF_CSRF_ENABLED=False)


def test_owner_can_login_and_access_private_api(client):
    response = client.post("/login", data={"username": os.environ["RAPHAEL_USERNAME"], "password": "test-password"})
    assert response.status_code == 302
    api_response = client.get("/api/conversations")
    assert api_response.status_code == 200


def test_invalid_credentials_do_not_authenticate(client):
    response = client.post(
        "/login",
        data={"username": os.environ["RAPHAEL_USERNAME"], "password": "wrong-password"},
    )
    assert response.status_code == 200
    assert b"Credenciales incorrectas" in response.data
    protected = client.get("/api/conversations")
    assert protected.status_code == 401


def test_logout_ends_authenticated_session(client):
    login_response = client.post(
        "/login",
        data={"username": os.environ["RAPHAEL_USERNAME"], "password": "test-password"},
    )
    assert login_response.status_code == 302
    with client.session_transaction() as current_session:
        assert current_session.get("_user_id") == "owner"
        assert current_session.get("_permanent") is True
    logout_response = client.post("/logout")
    assert logout_response.status_code == 302
    with client.session_transaction() as current_session:
        assert not current_session
    protected = client.get("/api/conversations")
    assert protected.status_code == 401


def test_session_and_csrf_timeouts_are_explicit_timedeltas():
    from datetime import timedelta

    assert app.config["PERMANENT_SESSION_LIFETIME"] == timedelta(hours=12)
    assert app.config["WTF_CSRF_TIME_LIMIT"] == 60 * 60 * 12


def test_login_cookie_has_secure_session_attributes(client):
    response = client.post(
        "/login",
        data={"username": os.environ["RAPHAEL_USERNAME"], "password": "test-password"},
    )
    assert response.status_code == 302
    cookie = response.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert "Expires=" in cookie


def test_flask_secret_and_session_cookie_configuration():
    assert len(app.config["SECRET_KEY"]) >= 32
    assert app.config["SESSION_COOKIE_NAME"] == "raphael_session"
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app.config["SESSION_COOKIE_DOMAIN"] is None


def test_production_config_requires_trusted_hosts():
    import pytest
    from app import validate_production_config

    config = {
        "TRUSTED_HOSTS": None,
        "SESSION_COOKIE_SECURE": True,
    }
    with pytest.raises(RuntimeError, match="TRUSTED_HOSTS"):
        validate_production_config(config, "production")


def test_production_config_requires_secure_cookies(monkeypatch):
    import pytest
    from app import validate_production_config

    monkeypatch.setenv("RAPHAEL_LIMITER_STORAGE", "rediss://localhost:6379/0")
    config = {
        "TRUSTED_HOSTS": ["raphael.example.com"],
        "SESSION_COOKIE_SECURE": False,
    }
    with pytest.raises(RuntimeError, match="COOKIE_SECURE"):
        validate_production_config(config, "production")


def test_production_config_requires_shared_redis_rate_limit_storage(monkeypatch):
    import pytest
    from app import validate_production_config

    monkeypatch.setenv("RAPHAEL_LIMITER_STORAGE", "memory://")
    config = {
        "TRUSTED_HOSTS": ["raphael.example.com"],
        "SESSION_COOKIE_SECURE": True,
    }
    with pytest.raises(RuntimeError, match="Redis"):
        validate_production_config(config, "production")


def test_production_allows_zero_trusted_proxy_hops(monkeypatch):
    from app import get_trusted_proxy_hops, validate_production_config

    monkeypatch.setenv("RAPHAEL_LIMITER_STORAGE", "rediss://localhost:6379/0")
    monkeypatch.setenv("RAPHAEL_TRUSTED_PROXY_HOPS", "0")
    config = {
        "TRUSTED_HOSTS": ["raphael.example.com"],
        "SESSION_COOKIE_SECURE": True,
    }
    validate_production_config(config, "production")
    assert get_trusted_proxy_hops("production") == 0


def test_proxy_hops_reject_invalid_values(monkeypatch):
    import pytest
    from app import get_trusted_proxy_hops

    for value in ("many", "-1", "6"):
        monkeypatch.setenv("RAPHAEL_TRUSTED_PROXY_HOPS", value)
        with pytest.raises(RuntimeError, match="entero entre 0 y 5"):
            get_trusted_proxy_hops("development")


def test_proxy_hops_accept_explicit_count(monkeypatch):
    from app import get_trusted_proxy_hops

    monkeypatch.setenv("RAPHAEL_TRUSTED_PROXY_HOPS", "1")
    assert get_trusted_proxy_hops("production") == 1


def test_development_config_does_not_require_production_services():
    from app import validate_production_config

    validate_production_config({}, "development")


def test_trusted_hosts_reject_unrecognized_host(client):
    original = app.config.get("TRUSTED_HOSTS")
    try:
        app.config["TRUSTED_HOSTS"] = ["raphael.example.com"]
        rejected = client.get("/login", headers={"Host": "attacker.example"})
        accepted = client.get("/login", headers={"Host": "raphael.example.com"})
        assert rejected.status_code == 400
        assert accepted.status_code == 200
    finally:
        app.config["TRUSTED_HOSTS"] = original


def test_security_headers_are_present(client):
    response = client.get("/login")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in response.headers


def test_api_write_requires_csrf_token(client):
    app.config.update(WTF_CSRF_ENABLED=True)
    response = client.post("/api/conversations", headers={"Content-Type": "application/json"}, json={})
    app.config.update(WTF_CSRF_ENABLED=False)
    assert response.status_code == 400


def test_memory_suggestions_are_not_active_until_approved(tmp_path):
    import memory_manager

    db_path = str(tmp_path / "memory.sqlite3")
    memory_manager.initialize(db_path)
    memory_id = memory_manager.create_memory(
        db_path, "Prefiere respuestas concisas", source="anthropic_suggestion",
        confidence=0.5, status="pending",
    )
    assert memory_manager.active_memory_facts(db_path) == []
    memory_manager.set_memory_status(db_path, memory_id, "active")
    assert memory_manager.active_memory_facts(db_path) == ["Prefiere respuestas concisas"]


def test_memory_update_and_delete(tmp_path):
    import memory_manager

    db_path = str(tmp_path / "memory.sqlite3")
    memory_manager.initialize(db_path)
    memory_id = memory_manager.create_memory(db_path, "Usa Python", category="project", status="active")
    memory_manager.update_memory(db_path, memory_id, fact="Usa Python para automatización", confidence=0.9)
    assert memory_manager.active_memory_facts(db_path) == ["Usa Python para automatización"]
    memory_manager.delete_memory(db_path, memory_id)
    assert memory_manager.active_memory_facts(db_path) == []


def test_memory_rejects_invalid_category_and_confidence(tmp_path):
    import memory_manager

    db_path = str(tmp_path / "memory.sqlite3")
    memory_manager.initialize(db_path)
    with pytest.raises(ValueError):
        memory_manager.create_memory(db_path, "dato", category="unknown")
    with pytest.raises(ValueError):
        memory_manager.create_memory(db_path, "dato", confidence=1.5)


def test_memories_api_requires_authentication(client):
    response = client.get("/api/memories")
    assert response.status_code == 401


def test_owner_can_create_and_list_memory(client):
    login = client.post(
        "/login",
        data={"username": os.environ["RAPHAEL_USERNAME"], "password": "test-password"},
    )
    assert login.status_code == 302
    created = client.post("/api/memories", json={"fact": "Dato de prueba API", "category": "personal"})
    assert created.status_code == 201
    listed = client.get("/api/memories?status=active")
    assert listed.status_code == 200
    assert any(item["fact"] == "Dato de prueba API" for item in listed.json["memories"])


def test_memory_dashboard_requires_authentication(client):
    response = client.get("/memories")
    assert response.status_code == 302


def test_owner_can_open_memory_dashboard(client):
    login = client.post(
        "/login",
        data={"username": os.environ["RAPHAEL_USERNAME"], "password": "test-password"},
    )
    assert login.status_code == 302
    response = client.get("/memories")
    assert response.status_code == 200
    assert b"Gestiona qu" not in response.data
    assert b"Memoria" in response.data


def test_new_database_creates_memory_proposals_table(tmp_path):
    import sqlite3
    import memory_manager

    db_path = str(tmp_path / "fresh.sqlite3")
    memory_manager.initialize(db_path)
    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "memory_proposals" in tables


def test_memory_conflict_candidates_are_ranked_without_auto_replacement(tmp_path):
    import memory_manager

    db_path = str(tmp_path / "conflicts.sqlite3")
    memory_manager.initialize(db_path)
    original_id = memory_manager.create_memory(
        db_path, "Prefiere respuestas breves en español", category="personal", status="active"
    )
    candidates = memory_manager.find_memory_conflicts(
        db_path, "Prefiere respuestas breves en inglés", category="personal", threshold=0.2
    )
    assert candidates
    assert candidates[0]["memory"]["id"] == original_id
    assert memory_manager.active_memory_facts(db_path) == ["Prefiere respuestas breves en español"]


def test_approved_memory_proposal_preserves_original_and_links_replacement(tmp_path):
    import memory_manager

    db_path = str(tmp_path / "approve.sqlite3")
    memory_manager.initialize(db_path)
    original_id = memory_manager.create_memory(
        db_path, "Prefiere respuestas breves en español", category="personal", status="active"
    )
    proposal_id = memory_manager.create_memory_proposal(
        db_path, original_id, "Prefiere respuestas detalladas en español",
        reason="El usuario actualizó su preferencia", similarity=0.5,
    )
    assert memory_manager.active_memory_facts(db_path) == ["Prefiere respuestas breves en español"]
    new_id = memory_manager.decide_memory_proposal(db_path, proposal_id, "approved")
    rows = memory_manager.list_memories(db_path)
    old = next(row for row in rows if row["id"] == original_id)
    new = next(row for row in rows if row["id"] == new_id)
    assert old["status"] == "pending"
    assert new["status"] == "active"
    assert new["supersedes_id"] == original_id
    assert memory_manager.active_memory_facts(db_path) == ["Prefiere respuestas detalladas en español"]


def test_rejected_memory_proposal_does_not_change_original(tmp_path):
    import memory_manager

    db_path = str(tmp_path / "reject.sqlite3")
    memory_manager.initialize(db_path)
    original_id = memory_manager.create_memory(db_path, "Vive en una ciudad", status="active")
    proposal_id = memory_manager.create_memory_proposal(db_path, original_id, "Vive en Madrid")
    assert memory_manager.decide_memory_proposal(db_path, proposal_id, "rejected") is None
    assert memory_manager.active_memory_facts(db_path) == ["Vive en una ciudad"]


def test_contextual_memory_retrieval_prefers_relevant_memories(tmp_path):
    import memory_manager

    db_path = str(tmp_path / "context.sqlite3")
    memory_manager.initialize(db_path)
    memory_manager.create_memory(db_path, "Trabaja en el proyecto Raphael con Python", category="project", status="active")
    memory_manager.create_memory(db_path, "Prefiere comida italiana", category="personal", status="active")
    selected = memory_manager.retrieve_relevant_memories(db_path, "Necesito modificar el proyecto Raphael usando Python")
    assert selected == ["Trabaja en el proyecto Raphael con Python"]


def test_memory_proposal_api_requires_authentication(client):
    response = client.get("/api/memory-proposals")
    assert response.status_code == 401

def test_memory_temporal_relationship_and_audit_schema(tmp_path):
    import sqlite3
    import memory_manager
    db_path = str(tmp_path / "temporal.sqlite3")
    memory_manager.initialize(db_path)
    first = memory_manager.create_memory(db_path, "Proyecto Raphael usa Python", category="project", status="active")
    second = memory_manager.create_memory(db_path, "Proyecto Raphael usa Flask", category="project", status="active")
    memory_manager.add_relationship(db_path, first, second, "same_topic")
    links = memory_manager.list_relationships(db_path, first)
    assert links and links[0]["related_id"] == second
    history = memory_manager.memory_history(db_path, first)
    assert history and history[0]["action"] == "relationship_added"
    timeline = memory_manager.temporal_memories(db_path)
    assert any(item["id"] == first and item["valid_from"] for item in timeline)
    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"memory_relationships", "memory_audit", "memory_proposals"}.issubset(tables)

def test_memory_relationship_rejects_self_link_and_invalid_type(tmp_path):
    import memory_manager
    db_path = str(tmp_path / "relations.sqlite3")
    memory_manager.initialize(db_path)
    memory_id = memory_manager.create_memory(db_path, "Dato", status="active")
    with pytest.raises(ValueError):
        memory_manager.add_relationship(db_path, memory_id, memory_id, "related_to")
    with pytest.raises(ValueError):
        memory_manager.add_relationship(db_path, memory_id, memory_id + 1, "unknown")

def test_temporal_memory_api_requires_authentication(client):
    response = client.get("/api/memories/timeline")
    assert response.status_code == 401

def test_semantic_review_api_requires_authentication(client):
    response = client.post("/api/memories/semantic-review", json={"fact": "Dato nuevo"})
    assert response.status_code == 401



def test_memory_validity_windows_filter_future_and_expired_records(tmp_path):
    from datetime import datetime, timedelta, timezone
    import memory_manager

    db_path = str(tmp_path / "validity.sqlite3")
    memory_manager.initialize(db_path)
    now = datetime.now(timezone.utc)
    future = (now + timedelta(days=1)).isoformat()
    past = (now - timedelta(days=1)).isoformat()
    future_id = memory_manager.create_memory(
        db_path, "Proyecto futuro Raphael", status="active", valid_from=future
    )
    expired_id = memory_manager.create_memory(
        db_path, "Proyecto expirado Raphael", status="active", valid_until=past
    )
    current_id = memory_manager.create_memory(
        db_path, "Proyecto actual Raphael", status="active"
    )

    assert memory_manager.active_memory_facts(db_path) == ["Proyecto actual Raphael"]
    assert memory_manager.retrieve_relevant_memories(db_path, "Proyecto Raphael") == ["Proyecto actual Raphael"]
    visible_ids = {item["id"] for item in memory_manager.temporal_memories(db_path)}
    assert current_id in visible_ids
    assert future_id not in visible_ids
    assert expired_id not in visible_ids
    all_ids = {item["id"] for item in memory_manager.temporal_memories(db_path, include_expired=True)}
    assert {future_id, expired_id, current_id}.issubset(all_ids)


def test_approved_replacement_closes_old_validity_and_records_audit(tmp_path):
    import json
    import memory_manager

    db_path = str(tmp_path / "audit-replacement.sqlite3")
    memory_manager.initialize(db_path)
    old_id = memory_manager.create_memory(
        db_path, "Prefiere español", status="active"
    )
    proposal_id = memory_manager.create_memory_proposal(
        db_path, old_id, "Prefiere inglés", similarity=0.5
    )
    new_id = memory_manager.decide_memory_proposal(db_path, proposal_id, "approved")

    rows = {item["id"]: item for item in memory_manager.list_memories(db_path)}
    assert rows[old_id]["status"] == "pending"
    assert rows[old_id]["valid_until"] is not None
    assert rows[new_id]["valid_from"] is not None
    history_old = memory_manager.memory_history(db_path, old_id)
    history_new = memory_manager.memory_history(db_path, new_id)
    assert history_old[0]["action"] == "memory_superseded"
    assert history_new[0]["action"] == "memory_created_as_replacement"
    assert json.loads(history_old[0]["before_json"])["fact"] == "Prefiere español"


def test_relationship_audit_payload_is_valid_json(tmp_path):
    import json
    import memory_manager

    db_path = str(tmp_path / "relationship-json.sqlite3")
    memory_manager.initialize(db_path)
    first = memory_manager.create_memory(db_path, "Dato uno")
    second = memory_manager.create_memory(db_path, "Dato dos")
    memory_manager.add_relationship(db_path, first, second, "related_to")
    event = memory_manager.memory_history(db_path, first)[0]
    payload = json.loads(event["after_json"])
    assert payload == {"target_id": second, "relation": "related_to"}



def _login_owner(client):
    # Authenticate the test client directly so the shared login rate limiter
    # is tested only by the dedicated login tests, not consumed by fixtures.
    with client.session_transaction() as session:
        session["_user_id"] = "owner"
        session["_fresh"] = True


def test_memory_proposal_api_lifecycle_is_isolated(tmp_path, monkeypatch, client):
    import memory_manager

    db_path = str(tmp_path / "proposal-api.sqlite3")
    memory_manager.initialize(db_path)
    monkeypatch.setattr("app.DB_PATH", db_path)
    _login_owner(client)

    created = client.post(
        "/api/memories",
        json={"fact": "Prefiere respuestas en español", "category": "personal"},
    )
    assert created.status_code == 201
    memory_id = created.json["id"]

    proposal = client.post(
        f"/api/memories/{memory_id}/proposals",
        json={"proposed_fact": "Prefiere respuestas en inglés", "reason": "Preferencia actualizada"},
    )
    assert proposal.status_code == 201
    proposal_id = proposal.json["id"]

    pending = client.get("/api/memory-proposals")
    assert pending.status_code == 200
    assert len(pending.json["proposals"]) == 1

    decided = client.patch(
        f"/api/memory-proposals/{proposal_id}",
        json={"decision": "approved"},
    )
    assert decided.status_code == 200
    assert decided.json["new_memory_id"]

    active = client.get("/api/memories?status=active")
    assert [item["fact"] for item in active.json["memories"]] == [
        "Prefiere respuestas en inglés"
    ]
    previous = client.get(f"/api/memories/{memory_id}/history")
    assert previous.status_code == 200
    assert previous.json["history"][0]["action"] == "memory_superseded"


def test_memory_relationship_and_timeline_api(tmp_path, monkeypatch, client):
    import memory_manager

    db_path = str(tmp_path / "relationship-api.sqlite3")
    memory_manager.initialize(db_path)
    monkeypatch.setattr("app.DB_PATH", db_path)
    _login_owner(client)

    first = client.post("/api/memories", json={"fact": "Proyecto Raphael", "category": "project"})
    second = client.post("/api/memories", json={"fact": "Raphael usa Flask", "category": "project"})
    assert first.status_code == second.status_code == 201

    related = client.post(
        f"/api/memories/{first.json['id']}/relationships",
        json={"target_memory_id": second.json["id"], "relation": "supports"},
    )
    assert related.status_code == 201
    relationships = client.get(f"/api/memories/{first.json['id']}/relationships")
    assert relationships.status_code == 200
    assert relationships.json["relationships"][0]["relation"] == "supports"

    timeline = client.get("/api/memories/timeline")
    assert timeline.status_code == 200
    assert len(timeline.json["memories"]) == 2


def test_semantic_review_api_uses_mock_and_never_mutates_memories(tmp_path, monkeypatch, client):
    from types import SimpleNamespace
    import memory_manager
    import app as app_module

    db_path = str(tmp_path / "semantic-api.sqlite3")
    memory_manager.initialize(db_path)
    monkeypatch.setattr("app.DB_PATH", db_path)
    _login_owner(client)

    created = client.post(
        "/api/memories",
        json={"fact": "Prefiere respuestas breves en español", "category": "personal"},
    )
    assert created.status_code == 201
    memory_id = created.json["id"]

    class FakeMessages:
        def create(self, **kwargs):
            block = SimpleNamespace(
                type="text",
                text='{"results":[{"memory_id":%d,"relation":"updates","confidence":0.93,"reason":"Nueva preferencia"}]}' % memory_id,
            )
            return SimpleNamespace(content=[block])

    monkeypatch.setattr(
        app_module,
        "create_anthropic_client",
        lambda: SimpleNamespace(messages=FakeMessages()),
    )

    response = client.post(
        "/api/memories/semantic-review",
        json={"fact": "Prefiere respuestas detalladas en español"},
    )
    assert response.status_code == 200
    assert response.json["results"][0]["relation"] == "updates"
    assert memory_manager.active_memory_facts(db_path) == [
        "Prefiere respuestas breves en español"
    ]


def test_semantic_review_rejects_invalid_input_without_calling_claude(client, monkeypatch):
    import app as app_module

    def unexpected_call():
        raise AssertionError("Claude no debe invocarse para una entrada vacía")

    monkeypatch.setattr(app_module, "create_anthropic_client", unexpected_call)
    _login_owner(client)
    response = client.post("/api/memories/semantic-review", json={"fact": "   "})
    assert response.status_code == 400



def test_memory_patch_is_atomic_when_one_field_is_invalid(tmp_path):
    import memory_manager

    db_path = str(tmp_path / "atomic-update.sqlite3")
    memory_manager.initialize(db_path)
    memory_id = memory_manager.create_memory(
        db_path, "Preferencia original", status="active"
    )
    with pytest.raises(ValueError):
        memory_manager.update_memory(
            db_path, memory_id, fact="Preferencia parcialmente cambiada", status="invalid"
        )
    row = next(item for item in memory_manager.list_memories(db_path) if item["id"] == memory_id)
    assert row["fact"] == "Preferencia original"
    assert row["status"] == "active"
    history = memory_manager.memory_history(db_path, memory_id)
    assert [event["action"] for event in history] == ["memory_created"]


def test_memory_update_preserves_expiration_when_field_is_omitted(tmp_path):
    from datetime import datetime, timedelta, timezone
    import memory_manager

    db_path = str(tmp_path / "preserve-expiration.sqlite3")
    memory_manager.initialize(db_path)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    memory_id = memory_manager.create_memory(
        db_path, "Recordatorio temporal", status="active", expires_at=expires_at
    )
    memory_manager.update_memory(db_path, memory_id, fact="Recordatorio temporal actualizado")
    row = next(item for item in memory_manager.list_memories(db_path) if item["id"] == memory_id)
    assert row["expires_at"] == expires_at


def test_manual_memory_mutations_are_audited(tmp_path):
    import memory_manager

    db_path = str(tmp_path / "manual-audit.sqlite3")
    memory_manager.initialize(db_path)
    memory_id = memory_manager.create_memory(db_path, "Dato inicial", status="active")
    memory_manager.update_memory(db_path, memory_id, fact="Dato editado")
    memory_manager.set_memory_status(db_path, memory_id, "pending")
    history = memory_manager.memory_history(db_path, memory_id)
    assert [event["action"] for event in history[:2]] == ["memory_updated", "memory_updated"]
    memory_manager.delete_memory(db_path, memory_id)
    deleted = memory_manager.memory_history(db_path, memory_id)
    assert deleted[0]["action"] == "memory_deleted"
    assert deleted[0]["before_json"]


def test_memory_rejects_malformed_or_timezone_naive_timestamps(tmp_path):
    import memory_manager

    db_path = str(tmp_path / "invalid-time.sqlite3")
    memory_manager.initialize(db_path)
    with pytest.raises(ValueError):
        memory_manager.create_memory(db_path, "Fecha inválida", expires_at="mañana")
    with pytest.raises(ValueError):
        memory_manager.create_memory(db_path, "Fecha sin zona", valid_from="2026-11-18T10:00:00")



def test_chat_rejects_non_integer_conversation_ids(client, monkeypatch):
    _login_owner(client)
    monkeypatch.setattr("app.get_anthropic_api_key", lambda: "test-key")
    for invalid_id in (True, 1.5, "1", None):
        response = client.post(
            "/api/chat", json={"conversation_id": invalid_id, "message": "Hola"}
        )
        assert response.status_code == 400, invalid_id


def test_chat_rejects_invalid_json_and_message_shapes(client, monkeypatch):
    _login_owner(client)
    monkeypatch.setattr("app.get_anthropic_api_key", lambda: "test-key")
    invalid_json = client.post("/api/chat", data="{", content_type="application/json")
    assert invalid_json.status_code == 400
    invalid_message = client.post(
        "/api/chat", json={"conversation_id": 1, "message": ["not", "text"]}
    )
    assert invalid_message.status_code == 400


def test_chat_rejects_oversized_message_before_provider_call(client, monkeypatch):
    _login_owner(client)
    monkeypatch.setattr("app.get_anthropic_api_key", lambda: "test-key")
    response = client.post(
        "/api/chat", json={"conversation_id": 1, "message": "x" * 10001}
    )
    assert response.status_code == 413


def test_chat_rejects_unknown_conversation_before_provider_call(client, monkeypatch):
    _login_owner(client)
    monkeypatch.setattr("app.get_anthropic_api_key", lambda: "test-key")
    response = client.post(
        "/api/chat", json={"conversation_id": 999999, "message": "Hola"}
    )
    assert response.status_code == 404



def test_history_rejects_invalid_or_unknown_conversation(client, monkeypatch):
    _login_owner(client)
    invalid = client.get("/api/history?conversation_id=-1")
    missing = client.get("/api/history?conversation_id=999999")
    assert invalid.status_code == 400
    assert missing.status_code == 404


def test_memory_history_returns_not_found_for_unknown_memory(client, monkeypatch, tmp_path):
    import memory_manager

    db_path = str(tmp_path / "missing-memory-history.sqlite3")
    memory_manager.initialize(db_path)
    monkeypatch.setattr("app.DB_PATH", db_path)
    _login_owner(client)
    response = client.get("/api/memories/999999/history")
    assert response.status_code == 404


def test_memory_relationship_api_rejects_non_integer_target_id(tmp_path, monkeypatch, client):
    import memory_manager

    db_path = str(tmp_path / "invalid-target-id.sqlite3")
    memory_manager.initialize(db_path)
    monkeypatch.setattr("app.DB_PATH", db_path)
    _login_owner(client)
    created = client.post("/api/memories", json={"fact": "Relación de prueba"})
    memory_id = created.json["id"]
    for invalid_id in (True, 1.5, "2", None):
        response = client.post(
            f"/api/memories/{memory_id}/relationships",
            json={"target_memory_id": invalid_id, "relation": "related_to"},
        )
        assert response.status_code == 400, invalid_id


def test_memory_relationships_returns_not_found_for_unknown_memory(client, monkeypatch, tmp_path):
    import memory_manager

    db_path = str(tmp_path / "missing-memory-relations.sqlite3")
    memory_manager.initialize(db_path)
    monkeypatch.setattr("app.DB_PATH", db_path)
    _login_owner(client)
    response = client.get("/api/memories/999999/relationships")
    assert response.status_code == 404


def test_security_headers_disallow_inline_scripts(client):
    response = client.get("/login")
    csp = response.headers["Content-Security-Policy"]
    script_policy = next(part.strip() for part in csp.split(";") if part.strip().startswith("script-src"))
    assert "'unsafe-inline'" not in script_policy
    assert "default-src 'self'" in csp
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"

def test_memory_endpoints_reject_non_object_json(tmp_path, monkeypatch, client):
    import memory_manager

    db_path = str(tmp_path / "invalid-json.sqlite3")
    memory_manager.initialize(db_path)
    monkeypatch.setattr("app.DB_PATH", db_path)
    _login_owner(client)
    created = client.post("/api/memories", json={"fact": "Recuerdo de prueba"})
    assert created.status_code == 201
    memory_id = created.json["id"]
    cases = [
        ("/api/memories", "POST"),
        (f"/api/memories/{memory_id}", "PATCH"),
        (f"/api/memories/{memory_id}/proposals", "POST"),
        ("/api/memory-proposals/1", "PATCH"),
        (f"/api/memories/{memory_id}/relationships", "POST"),
        ("/api/memories/semantic-review", "POST"),
    ]
    for path, method in cases:
        response = client.open(path, method=method, json=["unexpected", "array"])
        assert response.status_code == 400, (path, response.status_code)
        assert response.json["error"] == "Se esperaba un objeto JSON válido."


def test_semantic_review_rejects_non_text_fact(client):
    _login_owner(client)
    response = client.post("/api/memories/semantic-review", json={"fact": ["texto"]})
    assert response.status_code == 400
    assert response.json["error"] == "El campo fact debe ser texto."

def test_login_succeeds_with_valid_csrf_token(client):
    import re

    app.config.update(WTF_CSRF_ENABLED=True, RATELIMIT_ENABLED=False)
    try:
        page = client.get("/login")
        assert page.status_code == 200
        match = re.search(rb'name="csrf_token" value="([^"]+)"', page.data)
        assert match, "El formulario de login debe incluir el token CSRF"
        limiter.storage.reset()
        response = client.post(
            "/login",
            data={
                "username": os.environ["RAPHAEL_USERNAME"],
                "password": "test-password",
                "csrf_token": match.group(1).decode(),
            },
        )
        assert response.status_code == 302
        assert client.get("/api/conversations").status_code == 200
    finally:
        app.config.update(WTF_CSRF_ENABLED=False, RATELIMIT_ENABLED=True)


def test_authenticated_logout_rejects_missing_csrf_token(client):
    app.config.update(WTF_CSRF_ENABLED=True)
    try:
        _login_owner(client)
        response = client.post("/logout")
        assert response.status_code == 400
        assert client.get("/api/conversations").status_code == 200
    finally:
        app.config.update(WTF_CSRF_ENABLED=False)


def test_csp_disallows_inline_styles_and_login_uses_external_css(client):
    response = client.get("/login")
    policy = response.headers["Content-Security-Policy"]
    assert "style-src 'self' https://fonts.googleapis.com" in policy
    assert "style-src 'self' 'unsafe-inline'" not in policy
    assert b"static/login.css" in response.data
    assert b"<style>" not in response.data

