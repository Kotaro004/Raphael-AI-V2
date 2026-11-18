import io
import os
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from werkzeug.datastructures import FileStorage
from werkzeug.security import generate_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["RAPHAEL_USERNAME"] = "owner"
os.environ["RAPHAEL_PASSWORD_HASH"] = generate_password_hash("test-password")

from attachment_processor import AttachmentError, prepare_attachments
from app import app, create_conversation


@pytest.fixture(autouse=True)
def disable_csrf_for_api_tests():
    previous = app.config.get("WTF_CSRF_ENABLED", True)
    app.config.update(WTF_CSRF_ENABLED=False, TESTING=True, SESSION_COOKIE_SECURE=False)
    yield
    app.config.update(WTF_CSRF_ENABLED=previous)


def _login(client):
    response = client.post("/login", data={
        "username": os.environ["RAPHAEL_USERNAME"],
        "password": "test-password",
    })
    assert response.status_code == 302


def test_text_attachment_is_extracted_without_disk_storage():
    uploaded = FileStorage(stream=io.BytesIO("Hola desde un archivo".encode()), filename="nota.txt")
    extracted, blocks, names = prepare_attachments([uploaded])
    assert "Hola desde un archivo" in extracted
    assert blocks == []
    assert names == ["nota.txt"]


def test_unsupported_extension_is_rejected():
    uploaded = FileStorage(stream=io.BytesIO(b"no"), filename="program.exe")
    with pytest.raises(AttachmentError, match="Formato no admitido"):
        prepare_attachments([uploaded])


def test_file_size_limit_is_enforced():
    uploaded = FileStorage(stream=io.BytesIO(b"x" * (8 * 1024 * 1024 + 1)), filename="large.txt")
    with pytest.raises(AttachmentError, match="8 MB"):
        prepare_attachments([uploaded])


def test_zip_path_traversal_is_rejected():
    archive_data = io.BytesIO()
    with zipfile.ZipFile(archive_data, "w") as archive:
        archive.writestr("../outside.txt", "no debe extraerse")
    uploaded = FileStorage(stream=io.BytesIO(archive_data.getvalue()), filename="bundle.zip")
    with pytest.raises(AttachmentError, match="ruta insegura"):
        prepare_attachments([uploaded])


def test_private_attachment_endpoint_requires_login():
    with app.test_client() as client:
        response = client.post("/api/chat-with-files", data={})
    assert response.status_code == 401


def test_attachment_endpoint_sends_file_content_to_existing_anthropic_client(monkeypatch):
    import app as core

    captured = {}
    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="Archivo revisado.")])

    monkeypatch.setattr(core, "get_anthropic_api_key", lambda: "test-key")
    monkeypatch.setattr(core, "create_anthropic_client", lambda: SimpleNamespace(messages=FakeMessages()))
    conversation_id = create_conversation("Prueba adjuntos")
    with app.test_client() as client:
        _login(client)
        response = client.post(
            "/api/chat-with-files",
            data={
                "conversation_id": str(conversation_id),
                "message": "Resume este archivo",
                "files": (io.BytesIO(b"Contenido de prueba"), "informe.txt"),
            },
            content_type="multipart/form-data",
        )
    assert response.status_code == 200
    assert response.json["reply"] == "Archivo revisado."
    assert response.json["attachments"] == ["informe.txt"]
    assert captured["messages"][-1]["content"][0]["type"] == "text"
    assert "Contenido de prueba" in captured["messages"][-1]["content"][0]["text"]
