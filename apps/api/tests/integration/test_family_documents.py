"""Family documents: teachers are blocked by RLS and by the API; family sees only their own child's."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.integration.online_tests_world import build_world
from tests.integration.test_online_tests_api import api  # noqa: F401
from tests.integration.test_parent_messages_reading_rls import as_, count, denied, owner

pytestmark = pytest.mark.asyncio
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
API = "/api/v1/family-documents"


async def _insert(db, w, student):
    await owner(db)
    await db.execute(
        text("""INSERT INTO family_documents (id, school_id, student_id, doc_type, title, file_key, file_name, mime_type,
                file_size, created_at, updated_at) VALUES (:i, :s, :st, 'PROGRESS_CARD', 'Term 1', 'k', 'a.pdf',
                'application/pdf', 10, now(), now())"""),
        {"i": uuid4(), "s": w.school_id, "st": student.profile_id},
    )


async def test_rls_teachers_blocked_family_scoped(db_session):
    w = await build_world(db_session)
    await _insert(db_session, w, w.students[0])
    for who, expected in ((w.admin, 1), (w.teacher, 0), (w.teacher2, 0), (w.students[0], 1), (w.students[1], 0),
                          (w.parent, 1), (w.parent_other, 0), (w.other_admin, 0)):
        await as_(db_session, who)
        assert await count(db_session, "SELECT count(*) FROM family_documents") == expected, who.role
    await as_(db_session, w.teacher)
    await denied(db_session, """INSERT INTO family_documents (id, school_id, student_id, doc_type, title, file_key,
        file_name, mime_type, file_size, created_at, updated_at) VALUES (gen_random_uuid(), :s, :st, 'OTHER', 't', 'k',
        'a', 'application/pdf', 1, now(), now())""", s=w.school_id, st=w.students[0].profile_id)
    await as_(db_session, w.parent)
    await denied(db_session, """INSERT INTO family_documents (id, school_id, student_id, doc_type, title, file_key,
        file_name, mime_type, file_size, created_at, updated_at) VALUES (gen_random_uuid(), :s, :st, 'OTHER', 't', 'k',
        'a', 'application/pdf', 1, now(), now())""", s=w.school_id, st=w.students[0].profile_id)


async def test_api_upload_view_and_teacher_denied(api, db_session):  # noqa: F811
    w = await build_world(db_session)
    await db_session.commit()
    files = {"file": ("card.pdf", PDF, "application/pdf")}
    data = {"student_id": str(w.students[0].profile_id), "doc_type": "PROGRESS_CARD", "title": "Term 1"}
    assert (await api.post(API, data=data, files=files, headers=w.teacher.h)).status_code == 403
    assert (await api.post(API, data=data, files=files, headers=w.parent.h)).status_code == 403
    bad = await api.post(API, data=data, files={"file": ("x.pdf", b"not a pdf", "application/pdf")}, headers=w.admin.h)
    assert bad.status_code in (400, 422)
    r = await api.post(API, data=data, files=files, headers=w.admin.h)
    assert r.status_code == 201, r.text
    doc = r.json()
    assert "file_key" not in doc
    assert (await api.get(API, headers=w.teacher.h)).status_code == 403
    assert (await api.get(f"{API}/{doc['id']}/download", headers=w.teacher.h)).status_code == 403
    lst = await api.get(API, headers=w.parent.h)
    assert [d["id"] for d in lst.json()] == [doc["id"]]
    dl = await api.get(f"{API}/{doc['id']}/download", headers=w.parent.h)
    assert dl.status_code == 200 and dl.content == PDF and dl.headers["cache-control"] == "private, no-store"
    assert (await api.get(f"{API}/{doc['id']}/download", headers=w.students[0].h)).status_code == 200
    assert (await api.get(f"{API}/{doc['id']}/download", headers=w.parent_other.h)).status_code == 404
    assert (await api.get(API, headers=w.parent_other.h)).json() == []
    assert (await api.delete(f"{API}/{doc['id']}", headers=w.admin.h)).status_code == 204
