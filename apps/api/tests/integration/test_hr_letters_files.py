"""Phase 6: HR letters and private employee documents (real FastAPI + PostgreSQL)."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from src.db.models import Employee, EmployeeDocument, HrLetter
from src.modules.hr_files import storage

API = "/api/v1/hr"


@pytest.fixture(autouse=True)
def _private_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "STORAGE_ROOT", tmp_path / "hr-files")


async def _employee_id(api, actor, admin):
    rows = (await api.get(f"{API}/employees", headers=admin.headers)).json()
    return next(r["id"] for r in rows if r.get("teacher_id") == str(actor.teacher_id))


async def _non_teaching(api, admin, name="Office Clerk"):
    r = await api.post(
        f"{API}/employees",
        headers=admin.headers,
        json={
            "staff_type": "NON_TEACHING",
            "full_name": name,
            "employee_code": f"NT-{uuid4().hex[:5]}",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _pdf(name="contract.pdf", data=b"%PDF-1.4 test"):
    return {"file": (name, data, "application/pdf")}


# ── letters ─────────────────────────────────────────────────────────────────


async def test_letter_draft_from_template_edit_issue_pdf(api, hr_world):
    w = hr_world
    emp = await _employee_id(api, w.teacher_a, w.admin_a)

    tpl = await api.get(
        f"{API}/letters/template",
        params={"employee_id": emp, "letter_type": "EXPERIENCE"},
        headers=w.admin_a.headers,
    )
    assert tpl.status_code == 200
    assert "This is to certify" in tpl.json()["body"]

    r = await api.post(
        f"{API}/letters",
        headers=w.admin_a.headers,
        json={"employee_id": emp, "letter_type": "OFFER"},
    )
    assert r.status_code == 201, r.text
    letter = r.json()
    assert letter["status"] == "DRAFT" and letter["employee_name"] == "IT TEACHER"
    assert (
        "pleased to offer" in letter["body"]
        and letter["created_by_name"] == "IT SCHOOL_ADMIN"
    )

    r = await api.patch(
        f"{API}/letters/{letter['id']}",
        headers=w.admin_a.headers,
        json={"title": "Offer - Maths", "body": "Terms"},
    )
    assert r.status_code == 200 and r.json()["title"] == "Offer - Maths"

    r = await api.post(
        f"{API}/letters/{letter['id']}/issue", headers=w.admin_a.headers, json={}
    )
    assert r.status_code == 200, r.text
    issued = r.json()
    assert (
        issued["status"] == "ISSUED"
        and issued["issue_date"]
        and issued["issued_by_name"] == "IT SCHOOL_ADMIN"
    )
    assert issued["reference_number"].startswith("HR/") and issued[
        "reference_number"
    ].endswith("/0001")

    # issued content is frozen
    assert (
        await api.patch(
            f"{API}/letters/{letter['id']}",
            headers=w.admin_a.headers,
            json={"body": "x"},
        )
    ).status_code == 422
    assert (
        await api.delete(f"{API}/letters/{letter['id']}", headers=w.admin_a.headers)
    ).status_code == 422
    assert (
        await api.post(
            f"{API}/letters/{letter['id']}/issue", headers=w.admin_a.headers, json={}
        )
    ).status_code == 422

    pdf = await api.get(f"{API}/letters/{letter['id']}/pdf", headers=w.admin_a.headers)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert pdf.headers["content-type"] == "application/pdf"

    # second issued letter gets the next number
    r2 = await api.post(
        f"{API}/letters",
        headers=w.admin_a.headers,
        json={"employee_id": emp, "letter_type": "APPOINTMENT"},
    )
    issued2 = (
        await api.post(
            f"{API}/letters/{r2.json()['id']}/issue", headers=w.admin_a.headers, json={}
        )
    ).json()
    assert issued2["reference_number"].endswith("/0002")


async def test_letter_revoke_and_delete_draft(api, hr_world):
    w = hr_world
    emp = await _employee_id(api, w.teacher_a, w.admin_a)
    draft = (
        await api.post(
            f"{API}/letters",
            headers=w.admin_a.headers,
            json={"employee_id": emp, "letter_type": "OTHER"},
        )
    ).json()
    assert (
        await api.post(
            f"{API}/letters/{draft['id']}/revoke",
            headers=w.admin_a.headers,
            json={"reason": "x"},
        )
    ).status_code == 422
    assert (
        await api.delete(f"{API}/letters/{draft['id']}", headers=w.admin_a.headers)
    ).status_code == 204
    assert (
        await api.get(f"{API}/letters/{draft['id']}", headers=w.admin_a.headers)
    ).status_code == 404

    other = (
        await api.post(
            f"{API}/letters",
            headers=w.admin_a.headers,
            json={"employee_id": emp, "letter_type": "OFFER"},
        )
    ).json()
    await api.post(
        f"{API}/letters/{other['id']}/issue",
        headers=w.admin_a.headers,
        json={"issue_date": "2026-09-01"},
    )
    assert (
        await api.post(
            f"{API}/letters/{other['id']}/revoke",
            headers=w.admin_a.headers,
            json={"reason": " "},
        )
    ).status_code == 422
    r = await api.post(
        f"{API}/letters/{other['id']}/revoke",
        headers=w.admin_a.headers,
        json={"reason": "Issued in error"},
    )
    assert r.status_code == 200
    body = r.json()
    assert (
        body["status"] == "REVOKED"
        and body["revoke_reason"] == "Issued in error"
        and body["revoked_by_name"]
    )
    assert body["issue_date"] == "2026-09-01"


async def test_letter_validation_and_filters(api, hr_world):
    w = hr_world
    emp = await _employee_id(api, w.teacher_a, w.admin_a)
    bad = [
        {"employee_id": str(uuid4()), "letter_type": "OFFER"},
        {"employee_id": emp, "letter_type": "PROMOTION"},
        {"employee_id": emp, "letter_type": "OFFER", "title": "x" * 201},
    ]
    codes = [
        (
            await api.post(f"{API}/letters", headers=w.admin_a.headers, json=b)
        ).status_code
        for b in bad
    ]
    assert codes == [404, 422, 422]

    b_emp = await _employee_id(api, w.teacher_b, w.admin_b)
    r = await api.post(
        f"{API}/letters",
        headers=w.admin_a.headers,
        json={"employee_id": b_emp, "letter_type": "OFFER"},
    )
    assert r.status_code == 404

    nt = await _non_teaching(api, w.admin_a, "Lab Assistant")
    await api.post(
        f"{API}/letters",
        headers=w.admin_a.headers,
        json={"employee_id": nt, "letter_type": "APPOINTMENT"},
    )
    await api.post(
        f"{API}/letters",
        headers=w.admin_a.headers,
        json={"employee_id": emp, "letter_type": "OFFER"},
    )

    by_emp = (
        await api.get(
            f"{API}/letters", params={"employee_id": nt}, headers=w.admin_a.headers
        )
    ).json()
    assert [x["employee_name"] for x in by_emp] == ["Lab Assistant"]
    by_type = (
        await api.get(
            f"{API}/letters", params={"letter_type": "offer"}, headers=w.admin_a.headers
        )
    ).json()
    assert {x["letter_type"] for x in by_type} == {"OFFER"}
    by_q = (
        await api.get(f"{API}/letters", params={"q": "lab"}, headers=w.admin_a.headers)
    ).json()
    assert len(by_q) == 1
    assert (
        await api.get(
            f"{API}/letters", params={"status": "SENT"}, headers=w.admin_a.headers
        )
    ).status_code == 422


async def test_letter_access_matrix(api, hr_world):
    w = hr_world
    emp = await _employee_id(api, w.teacher_a, w.admin_a)
    draft = (
        await api.post(
            f"{API}/letters",
            headers=w.admin_a.headers,
            json={"employee_id": emp, "letter_type": "OFFER"},
        )
    ).json()
    lid = draft["id"]

    # the employee cannot see drafts
    assert (
        await api.get(f"{API}/me/letters", headers=w.teacher_a.headers)
    ).json() == []
    assert (
        await api.get(f"{API}/letters/{lid}/pdf", headers=w.teacher_a.headers)
    ).status_code == 404

    await api.post(f"{API}/letters/{lid}/issue", headers=w.admin_a.headers, json={})
    mine = (await api.get(f"{API}/me/letters", headers=w.teacher_a.headers)).json()
    assert [x["id"] for x in mine] == [lid]
    assert (
        await api.get(f"{API}/letters/{lid}/pdf", headers=w.teacher_a.headers)
    ).status_code == 200

    # another employee, another school, students, no token
    assert (
        await api.get(f"{API}/me/letters", headers=w.teacher_a2.headers)
    ).json() == []
    assert (
        await api.get(f"{API}/letters/{lid}/pdf", headers=w.teacher_a2.headers)
    ).status_code == 404
    assert (
        await api.get(f"{API}/letters/{lid}/pdf", headers=w.teacher_b.headers)
    ).status_code == 404
    assert (
        await api.get(f"{API}/letters/{lid}", headers=w.admin_b.headers)
    ).status_code == 404
    assert (
        await api.get(f"{API}/letters/{lid}/pdf", headers=w.admin_b.headers)
    ).status_code == 404
    assert (
        await api.post(
            f"{API}/letters/{lid}/revoke",
            headers=w.admin_b.headers,
            json={"reason": "x"},
        )
    ).status_code == 404
    assert lid not in [
        x["id"]
        for x in (await api.get(f"{API}/letters", headers=w.admin_b.headers)).json()
    ]
    assert (
        await api.get(f"{API}/letters", headers=w.teacher_a.headers)
    ).status_code == 403
    assert (
        await api.get(f"{API}/letters/{lid}", headers=w.teacher_a.headers)
    ).status_code == 403
    assert (
        await api.get(f"{API}/letters/{lid}/pdf", headers=w.student_a.headers)
    ).status_code == 403
    assert (
        await api.get(f"{API}/me/letters", headers=w.student_a.headers)
    ).status_code == 403
    assert (
        await api.get(f"{API}/me/letters", headers=w.admin_a.headers)
    ).status_code == 403
    assert (await api.get(f"{API}/letters/{lid}/pdf")).status_code == 401


async def test_letter_db_constraints(db_session, hr_world, api):
    w = hr_world
    emp = await _employee_id(api, w.teacher_a, w.admin_a)
    b_emp = await _employee_id(api, w.teacher_b, w.admin_b)
    from sqlalchemy.exc import IntegrityError

    for kwargs in (
        {"employee_id": b_emp},  # employee of another school
        {"employee_id": emp, "status": "ISSUED"},  # issued without a date
        {"employee_id": emp, "letter_type": "BOGUS"},
    ):
        with pytest.raises(IntegrityError):
            async with db_session.begin_nested():
                db_session.add(
                    HrLetter(
                        **{
                            "school_id": w.admin_a.school_id,
                            "letter_type": "OFFER",
                            "title": "t",
                            "body": "b",
                            "status": "DRAFT",
                            **kwargs,
                        }
                    )
                )
                await db_session.flush()


# ── employee files ─────────────────────────────────────────────────────────


async def test_file_upload_list_update_replace_archive_delete(
    api, hr_world, db_session
):
    w = hr_world
    emp = await _employee_id(api, w.teacher_a, w.admin_a)
    r = await api.post(
        f"{API}/files",
        headers=w.admin_a.headers,
        data={"employee_id": emp, "category": "CONTRACT", "notes": " signed "},
        files=_pdf("Signed contract.pdf"),
    )
    assert r.status_code == 201, r.text
    doc = r.json()
    assert (
        doc["title"] == "Signed contract"
        and doc["notes"] == "signed"
        and doc["file_size"] == 13
    )
    assert (
        doc["mime_type"] == "application/pdf"
        and doc["uploaded_by_name"] == "IT SCHOOL_ADMIN"
    )
    assert (
        "storage_key" not in doc
        and "url" not in doc
        and doc["shared_with_employee"] is False
    )

    row = (
        await db_session.execute(
            select(EmployeeDocument).where(EmployeeDocument.id == doc["id"])
        )
    ).scalar_one()
    assert storage.path_for(row.storage_key).read_bytes() == b"%PDF-1.4 test"
    assert row.storage_key.startswith(f"{w.admin_a.school_id}/")

    dl = await api.get(f"{API}/files/{doc['id']}/download", headers=w.admin_a.headers)
    assert dl.status_code == 200 and dl.content == b"%PDF-1.4 test"
    assert (
        "attachment" in dl.headers["content-disposition"]
        and dl.headers["x-content-type-options"] == "nosniff"
    )

    listed = (
        await api.get(
            f"{API}/files", params={"employee_id": emp}, headers=w.admin_a.headers
        )
    ).json()
    assert [x["id"] for x in listed] == [doc["id"]]
    assert (
        await api.get(
            f"{API}/files", params={"category": "BANK"}, headers=w.admin_a.headers
        )
    ).json() == []
    assert (
        len(
            (
                await api.get(
                    f"{API}/files", params={"q": "signed"}, headers=w.admin_a.headers
                )
            ).json()
        )
        == 1
    )

    r = await api.patch(
        f"{API}/files/{doc['id']}",
        headers=w.admin_a.headers,
        json={"title": "Contract 2026", "category": "OTHER"},
    )
    assert (
        r.status_code == 200
        and r.json()["title"] == "Contract 2026"
        and r.json()["category"] == "OTHER"
    )
    assert (
        await api.patch(
            f"{API}/files/{doc['id']}",
            headers=w.admin_a.headers,
            json={"category": "SECRET"},
        )
    ).status_code == 422

    old_key = row.storage_key
    r = await api.put(
        f"{API}/files/{doc['id']}/file",
        headers=w.admin_a.headers,
        files={"file": ("v2.png", b"\x89PNG..", "image/png")},
    )
    assert (
        r.status_code == 200
        and r.json()["file_name"] == "v2.png"
        and r.json()["mime_type"] == "image/png"
    )
    assert not storage.path_for(old_key).exists()
    dl = await api.get(f"{API}/files/{doc['id']}/download", headers=w.admin_a.headers)
    assert dl.content == b"\x89PNG.."

    assert (
        await api.delete(f"{API}/files/{doc['id']}", headers=w.admin_a.headers)
    ).status_code == 422
    r = await api.post(f"{API}/files/{doc['id']}/archive", headers=w.admin_a.headers)
    assert (
        r.status_code == 200
        and r.json()["status"] == "ARCHIVED"
        and r.json()["archived_at"]
    )
    assert (await api.get(f"{API}/files", headers=w.admin_a.headers)).json() == []
    assert (
        len(
            (
                await api.get(
                    f"{API}/files",
                    params={"status": "ARCHIVED"},
                    headers=w.admin_a.headers,
                )
            ).json()
        )
        == 1
    )
    assert (
        await api.post(f"{API}/files/{doc['id']}/archive", headers=w.admin_a.headers)
    ).status_code == 422
    r = await api.post(f"{API}/files/{doc['id']}/restore", headers=w.admin_a.headers)
    assert r.json()["status"] == "ACTIVE"
    await api.post(f"{API}/files/{doc['id']}/archive", headers=w.admin_a.headers)
    db_session.expire_all()
    key = (
        await db_session.execute(
            select(EmployeeDocument.storage_key).where(EmployeeDocument.id == doc["id"])
        )
    ).scalar_one()
    assert (
        await api.delete(f"{API}/files/{doc['id']}", headers=w.admin_a.headers)
    ).status_code == 204
    assert not storage.path_for(key).exists()
    assert (
        await api.get(f"{API}/files/{doc['id']}", headers=w.admin_a.headers)
    ).status_code == 404


async def test_file_upload_validation(api, hr_world):
    w = hr_world
    emp = await _employee_id(api, w.teacher_a, w.admin_a)
    b_emp = await _employee_id(api, w.teacher_b, w.admin_b)
    cases = [
        ({"employee_id": emp, "category": "CONTRACT"}, _pdf("run.exe"), 422),
        ({"employee_id": emp, "category": "CONTRACT"}, _pdf("empty.pdf", b""), 422),
        ({"employee_id": emp, "category": "PAYSLIP"}, _pdf(), 422),
        ({"employee_id": str(uuid4()), "category": "CONTRACT"}, _pdf(), 404),
        ({"employee_id": b_emp, "category": "CONTRACT"}, _pdf(), 404),
    ]
    for data, files, code in cases:
        r = await api.post(
            f"{API}/files", headers=w.admin_a.headers, data=data, files=files
        )
        assert r.status_code == code, (data, r.text)
    # path components in the client file name are discarded
    r = await api.post(
        f"{API}/files",
        headers=w.admin_a.headers,
        data={"employee_id": emp, "category": "ID_PROOF"},
        files=_pdf("../../etc/passwd.pdf"),
    )
    assert r.status_code == 201 and r.json()["file_name"] == "passwd.pdf"


async def test_file_access_matrix(api, hr_world):
    w = hr_world
    emp = await _employee_id(api, w.teacher_a, w.admin_a)
    private = (
        await api.post(
            f"{API}/files",
            headers=w.admin_a.headers,
            data={"employee_id": emp, "category": "MEDICAL"},
            files=_pdf(),
        )
    ).json()
    shared = (
        await api.post(
            f"{API}/files",
            headers=w.admin_a.headers,
            data={
                "employee_id": emp,
                "category": "CONTRACT",
                "shared_with_employee": "true",
            },
            files=_pdf(),
        )
    ).json()

    mine = (await api.get(f"{API}/me/files", headers=w.teacher_a.headers)).json()
    assert [x["id"] for x in mine] == [shared["id"]]
    assert (
        await api.get(
            f"{API}/files/{shared['id']}/download", headers=w.teacher_a.headers
        )
    ).status_code == 200
    assert (
        await api.get(
            f"{API}/files/{private['id']}/download", headers=w.teacher_a.headers
        )
    ).status_code == 404

    for actor in (w.teacher_a2, w.teacher_b, w.admin_b):
        for d in (private, shared):
            assert (
                await api.get(f"{API}/files/{d['id']}/download", headers=actor.headers)
            ).status_code == 404
    assert (await api.get(f"{API}/me/files", headers=w.teacher_a2.headers)).json() == []
    assert (
        await api.get(f"{API}/files/{private['id']}", headers=w.admin_b.headers)
    ).status_code == 404
    assert (
        await api.patch(
            f"{API}/files/{private['id']}",
            headers=w.admin_b.headers,
            json={"title": "x"},
        )
    ).status_code == 404
    assert (
        await api.post(
            f"{API}/files/{private['id']}/archive", headers=w.admin_b.headers
        )
    ).status_code == 404
    assert (await api.get(f"{API}/files", headers=w.admin_b.headers)).json() == []
    for path in ("/files", f"/files/{private['id']}", "/files/checklist"):
        assert (
            await api.get(f"{API}{path}", headers=w.teacher_a.headers)
        ).status_code == 403
    assert (
        await api.get(
            f"{API}/files/{shared['id']}/download", headers=w.student_a.headers
        )
    ).status_code == 403
    assert (
        await api.get(f"{API}/me/files", headers=w.student_a.headers)
    ).status_code == 403
    assert (await api.get(f"{API}/files/{shared['id']}/download")).status_code == 401
    r = await api.post(
        f"{API}/files",
        headers=w.teacher_a.headers,
        data={"employee_id": emp, "category": "OTHER"},
        files=_pdf(),
    )
    assert r.status_code == 403

    # archiving or un-sharing removes the employee's access
    await api.patch(
        f"{API}/files/{shared['id']}",
        headers=w.admin_a.headers,
        json={"shared_with_employee": False},
    )
    assert (
        await api.get(
            f"{API}/files/{shared['id']}/download", headers=w.teacher_a.headers
        )
    ).status_code == 404


async def test_file_checklist(api, hr_world, db_session):
    w = hr_world
    emp = await _employee_id(api, w.teacher_a, w.admin_a)
    for cat in ("ID_PROOF", "CONTRACT", "QUALIFICATION", "BANK"):
        await api.post(
            f"{API}/files",
            headers=w.admin_a.headers,
            data={"employee_id": emp, "category": cat},
            files=_pdf(),
        )
    nt = await _non_teaching(api, w.admin_a)
    await api.post(
        f"{API}/files",
        headers=w.admin_a.headers,
        data={"employee_id": nt, "category": "BANK"},
        files=_pdf(),
    )

    c = (await api.get(f"{API}/files/checklist", headers=w.admin_a.headers)).json()
    assert c["required"] == ["ID_PROOF", "CONTRACT", "QUALIFICATION", "BANK"]
    rows = {r["employee_id"]: r for r in c["rows"]}
    assert rows[emp]["missing"] == [] and rows[emp]["document_count"] == 4
    assert rows[nt]["missing"] == ["ID_PROOF", "CONTRACT", "QUALIFICATION"] and rows[
        nt
    ]["present"] == ["BANK"]
    assert c["complete"] == 1 and c["incomplete"] == len(rows) - 1

    # inactive staff drop out of the checklist
    emp_row = (
        await db_session.execute(select(Employee).where(Employee.id == nt))
    ).scalar_one()
    emp_row.status = "INACTIVE"
    await db_session.flush()
    c = (await api.get(f"{API}/files/checklist", headers=w.admin_a.headers)).json()
    assert nt not in {r["employee_id"] for r in c["rows"]}
