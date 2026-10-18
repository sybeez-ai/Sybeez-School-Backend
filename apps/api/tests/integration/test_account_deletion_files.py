"""Deleting an account removes the files behind the student's family documents, also under forced RLS."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from src.core import storage
from src.core.config import get_settings
from src.core.security import hash_password
from src.db.models import User
from tests.integration.online_tests_world import build_world
from tests.integration.test_online_tests_api import api  # noqa: F401

pytestmark = pytest.mark.asyncio
PW = "Delete-Me-Test-123!"


async def test_account_deletion_removes_family_document_files(api, db_session, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(storage, "_MEDIA_ROOT", tmp_path)
    monkeypatch.setattr(get_settings(), "s3_bucket", "")
    w = await build_world(db_session)
    stu = w.students[1]
    user = await db_session.get(User, stu.user_id)
    user.email = f"del-{uuid4().hex[:10]}@example.com"
    user.password_hash = hash_password(PW)
    key = storage.put_object(key=f"schools/{w.school_id}/family-docs/{stu.profile_id}/{uuid4()}.pdf", data=b"%PDF-1.4 x")
    await db_session.execute(
        text("""INSERT INTO family_documents (id, school_id, student_id, doc_type, title, file_key, file_name, mime_type,
                file_size, created_at, updated_at) VALUES (gen_random_uuid(), :s, :st, 'OTHER', 't', :k, 'a.pdf',
                'application/pdf', 10, now(), now())"""),
        {"s": w.school_id, "st": stu.profile_id, "k": key},
    )
    await db_session.commit()
    assert (tmp_path / key).is_file()
    r = await api.post("/api/v1/auth/delete-account", json={"confirm": "DELETE", "password": PW}, headers=stu.h)
    assert r.status_code == 200, r.text
    assert not (tmp_path / key).exists()
