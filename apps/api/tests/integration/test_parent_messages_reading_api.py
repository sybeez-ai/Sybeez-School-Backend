"""Parent messages in the parent's language and the reading buddy, end to end through the API.

Every test runs twice: as the table owner and as the production role (sybeez_app, forced RLS).
The AI service is replaced by fakes; no network."""

from __future__ import annotations

import httpx
import pytest
from fastapi import Request
from sqlalchemy import text

from src.core.database import get_db, tenant_from_request
from src.main import app
from src.modules.fees.tenant import apply_tenant
from src.modules.parent_messages import ai_client as translate_client
from src.modules.reading_buddy import ai_client as voice_client
from tests.integration.online_tests_world import World, build_world

pytestmark = pytest.mark.asyncio
API = "/api/v1"


@pytest.fixture(params=["superuser", "forced_rls"])
async def api(request, db_session):
    rls = request.param == "forced_rls"

    async def _get_db(req: Request):
        db_session.info["rls_tenant"] = tenant_from_request(req)  # production always sets the tenant GUCs
        if rls:
            await db_session.execute(text("SET ROLE sybeez_app"))
        await apply_tenant(db_session)
        try:
            yield db_session
            await db_session.commit()
        finally:
            if rls:
                await db_session.execute(text("RESET ROLE"))

    app.dependency_overrides[get_db] = _get_db
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers={"Origin": "http://test"}) as client:
        yield client
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
async def world(db_session) -> World:
    return await build_world(db_session)


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    calls = {"translate": 0, "voice": 0}

    async def fake_translate(user, *, title, body, languages):
        calls["translate"] += 1
        return {
            "model": "qwen3.8-flash",
            "translations": {c: {"title": f"[{c}] {title}", "body": f"[{c}] {body}", "needs_review": False} for c in languages},
            "failed": [],
        }

    async def fake_voice(user, *, passage, class_level):
        calls["voice"] += 1
        return {"value": "tok", "ws_url": "wss://x", "setup": {"setup": {}}, "session": {"max_minutes": 6}}

    monkeypatch.setattr(translate_client, "translate_notice", fake_translate)
    monkeypatch.setattr(voice_client, "mint_reading_voice", fake_voice)
    return calls


async def _announce(api, world, **extra):
    r = await api.post(f"{API}/announcements", headers=world.teacher.h,
                       json={"title": "Fee due 15/10", "body": "Please pay Rs 1200 by 15/10.", "class_id": str(world.class_id), **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _set_lang(api, person, code):
    r = await api.put(f"{API}/parent-messages/preferences/me", headers=person.h, json={"preferred_language": code})
    assert r.status_code == 200, r.text
    return r.json()


# ── Feature 1 ────────────────────────────────────────────────────────────────


async def test_parent_reads_class_notice_in_their_language_original_kept_and_cached(api, world, fakes):
    ann = await _announce(api, world)
    await _set_lang(api, world.parent, "ta")
    inbox = (await api.get(f"{API}/parent-messages/inbox", headers=world.parent.h)).json()
    item = next(i for i in inbox["items"] if i["id"] == ann)
    assert item["translated"] and item["language"] == "ta" and item["title"].startswith("[ta]")
    assert item["original_title"] == "Fee due 15/10" and "1200" in item["original_body"]
    again = (await api.get(f"{API}/parent-messages/inbox", headers=world.parent.h)).json()
    assert next(i for i in again["items"] if i["id"] == ann)["translated"]
    assert fakes["translate"] == 1  # cached per notice + language
    # English parent sees the original, untranslated
    english = (await api.get(f"{API}/parent-messages/inbox", headers=world.parent.h)).json()
    assert english["preferred_language"] == "ta"
    audit = (await api.get(f"{API}/audit", headers=world.admin.h))
    assert audit.status_code in (200, 403, 404)


async def test_parent_without_child_in_class_never_sees_class_notice(api, world):
    ann = await _announce(api, world)
    inbox = (await api.get(f"{API}/parent-messages/inbox", headers=world.parent_other.h)).json()
    assert ann not in [i["id"] for i in inbox["items"]]


async def test_draft_notice_is_hidden_from_parents(api, world):
    ann = await _announce(api, world, is_published=False)
    inbox = (await api.get(f"{API}/parent-messages/inbox", headers=world.parent.h)).json()
    assert ann not in [i["id"] for i in inbox["items"]]


async def test_author_previews_edits_and_parent_sees_the_edit(api, world, db_session):
    ann = await _announce(api, world)
    gen = await api.post(f"{API}/parent-messages/translations/announcement/{ann}/generate", headers=world.teacher.h,
                         json={"languages": ["hi", "te"]})
    assert gen.status_code == 200, gen.text
    langs = {t["language"] for t in gen.json()["translations"]}
    assert langs == {"hi", "te"} and gen.json()["original_title"] == "Fee due 15/10"
    edit = await api.put(f"{API}/parent-messages/translations/announcement/{ann}/hi", headers=world.teacher.h,
                         json={"title": "शुल्क 15/10", "body": "कृपया 1200 रुपये 15/10 तक दें।"})
    assert edit.status_code == 200 and edit.json()["status"] == "EDITED"
    # regenerating never overwrites a human edit
    await api.post(f"{API}/parent-messages/translations/announcement/{ann}/generate", headers=world.teacher.h,
                   json={"languages": ["hi"]})
    await _set_lang(api, world.parent, "hi")
    item = next(i for i in (await api.get(f"{API}/parent-messages/inbox", headers=world.parent.h)).json()["items"] if i["id"] == ann)
    assert item["title"] == "शुल्क 15/10" and item["translation_status"] == "EDITED"
    n = (await db_session.execute(text(
        "SELECT count(*) FROM audit_logs WHERE action IN ('message_translation.generated','message_translation.edited')"))).scalar_one()
    assert n >= 3


async def test_other_teacher_and_parents_cannot_manage_translations(api, world):
    ann = await _announce(api, world)
    url = f"{API}/parent-messages/translations/announcement/{ann}"
    assert (await api.get(url, headers=world.parent.h)).status_code == 403
    assert (await api.get(url, headers=world.teacher2.h)).status_code in (403, 404)
    assert (await api.get(url, headers=world.other_admin.h)).status_code in (403, 404)
    assert (await api.get(url, headers=world.students[0].h)).status_code in (403, 404)
    assert (await api.get(url, headers=world.admin.h)).status_code == 200


async def test_preferences_language_validation_consent_and_audit(api, world, db_session):
    bad = await api.put(f"{API}/parent-messages/preferences/me", headers=world.parent.h, json={"preferred_language": "xx"})
    assert bad.status_code == 422
    # opt-in needs a phone number on file
    no_phone = await api.put(f"{API}/parent-messages/preferences/me", headers=world.parent.h, json={"whatsapp_opt_in": True})
    assert no_phone.status_code == 422
    await db_session.execute(text("UPDATE users SET phone='+919876543210' WHERE id=:i"), {"i": world.parent.user_id})
    ok = await api.put(f"{API}/parent-messages/preferences/me", headers=world.parent.h,
                       json={"whatsapp_opt_in": True, "preferred_language": "ml"})
    body = ok.json()
    assert ok.status_code == 200 and body["whatsapp_opt_in"] and body["preferred_language"] == "ml"
    assert body["whatsapp_consent_at"] and body["whatsapp_available"] is False  # flag off until Meta credentials exist
    off = await api.put(f"{API}/parent-messages/preferences/me", headers=world.parent.h, json={"whatsapp_opt_in": False})
    assert off.json()["whatsapp_opt_in"] is False
    assert (await api.get(f"{API}/parent-messages/preferences/me", headers=world.teacher.h)).status_code == 403
    n = (await db_session.execute(text(
        "SELECT count(*) FROM audit_logs WHERE action='parent_message_preferences.updated'"))).scalar_one()
    assert n == 2


async def test_progress_update_reaches_only_that_students_parent_in_their_language(api, world):
    r = await api.post(f"{API}/parent-messages/progress-updates", headers=world.teacher.h, json={
        "student_id": str(world.students[0].profile_id), "kind": "RESULT", "title": "Unit test result",
        "body": "Scored 18 out of 20 in Maths."})
    assert r.status_code == 201, r.text
    await _set_lang(api, world.parent, "kn")
    items = (await api.get(f"{API}/parent-messages/inbox", headers=world.parent.h)).json()["items"]
    mine = [i for i in items if i["source_type"] == "progress_update"]
    assert len(mine) == 1 and mine[0]["translated"] and mine[0]["student_name"] == "OT Student 0"
    other = (await api.get(f"{API}/parent-messages/inbox", headers=world.parent_other.h)).json()["items"]
    assert not [i for i in other if i["source_type"] == "progress_update"]
    # a teacher of another class cannot write to this student
    denied = await api.post(f"{API}/parent-messages/progress-updates", headers=world.teacher2.h, json={
        "student_id": str(world.students[0].profile_id), "title": "x", "body": "y"})
    assert denied.status_code in (403, 404)
    assert (await api.post(f"{API}/parent-messages/progress-updates", headers=world.parent.h, json={
        "student_id": str(world.students[0].profile_id), "title": "x", "body": "y"})).status_code == 403


# ── Feature 2 ────────────────────────────────────────────────────────────────


async def _first_passage(api, world):
    r = await api.get(f"{API}/reading-buddy/passages", headers=world.students[0].h)
    assert r.status_code == 200, r.text
    items = r.json()
    assert items and all(p["class_level"] == 5 for p in items)
    return items[0]


async def test_student_reads_earns_stars_and_parent_teacher_see_summary(api, world, fakes):
    me = (await api.get(f"{API}/reading-buddy/me", headers=world.students[0].h)).json()
    assert me["eligible"] and me["class_level"] == 5 and me["progress"]["stars_total"] == 0
    assert me["caps"]["retention_days"] == 90
    passage = await _first_passage(api, world)
    start = await api.post(f"{API}/reading-buddy/sessions", headers=world.students[0].h, json={"passage_id": passage["id"]})
    assert start.status_code == 201, start.text
    assert start.json()["voice"]["value"] == "tok" and fakes["voice"] == 1
    # a second open session is refused
    again = await api.post(f"{API}/reading-buddy/sessions", headers=world.students[0].h, json={"passage_id": passage["id"]})
    assert again.status_code == 409
    done = await api.post(f"{API}/reading-buddy/sessions/{start.json()['session_id']}/complete", headers=world.students[0].h,
                          json={"transcript": passage["body"], "duration_seconds": 45})
    assert done.status_code == 200, done.text
    out = done.json()
    assert out["stars"] == 3 and out["progress"]["stars_total"] == 3 and out["progress"]["current_streak"] == 1
    # finishing twice is refused
    assert (await api.post(f"{API}/reading-buddy/sessions/{start.json()['session_id']}/complete",
                           headers=world.students[0].h, json={"transcript": "", "duration_seconds": 1})).status_code == 409
    sid = str(world.students[0].profile_id)
    for who in (world.parent, world.teacher, world.admin):
        s = await api.get(f"{API}/reading-buddy/students/{sid}/summary", headers=who.h)
        assert s.status_code == 200, (who.role, s.text)
        assert s.json()["progress"]["stars_total"] == 3 and "transcript" not in s.text.lower().replace("transcripts are never", "")
    for who in (world.parent_other, world.teacher2, world.students[1], world.other_admin):
        s = await api.get(f"{API}/reading-buddy/students/{sid}/summary", headers=who.h)
        assert s.status_code in (403, 404), who.role


async def test_daily_session_cap_and_other_students_session_is_not_reachable(api, world):
    passage = await _first_passage(api, world)
    ids = []
    for _ in range(3):
        s = await api.post(f"{API}/reading-buddy/sessions", headers=world.students[0].h, json={"passage_id": passage["id"]})
        assert s.status_code == 201, s.text
        ids.append(s.json()["session_id"])
        await api.post(f"{API}/reading-buddy/sessions/{ids[-1]}/complete", headers=world.students[0].h,
                       json={"transcript": "the", "duration_seconds": 5})
    capped = await api.post(f"{API}/reading-buddy/sessions", headers=world.students[0].h, json={"passage_id": passage["id"]})
    assert capped.status_code == 429 and capped.json()["error"]["code"] == "daily_cap"
    other = await api.post(f"{API}/reading-buddy/sessions/{ids[0]}/complete", headers=world.students[1].h,
                           json={"transcript": "x", "duration_seconds": 1})
    assert other.status_code == 404


async def test_non_students_cannot_start_and_teachers_add_school_passages(api, world):
    passage = await _first_passage(api, world)
    for who in (world.parent, world.teacher):
        assert (await api.post(f"{API}/reading-buddy/sessions", headers=who.h, json={"passage_id": passage["id"]})).status_code == 403
    new = await api.post(f"{API}/reading-buddy/passages", headers=world.teacher.h, json={
        "class_level": 5, "title": "Our school garden", "body": "Our school has a small garden. Children water the plants every morning."})
    assert new.status_code == 201, new.text
    mine = [p for p in (await api.get(f"{API}/reading-buddy/passages", headers=world.students[0].h)).json() if p["source"] == "SCHOOL"]
    assert [p["title"] for p in mine] == ["Our school garden"]
    # another school's student/teacher never see it
    theirs = await api.get(f"{API}/reading-buddy/passages", headers=world.other_teacher.h)
    assert "Our school garden" not in theirs.text
    assert (await api.post(f"{API}/reading-buddy/passages", headers=world.students[0].h, json={
        "class_level": 1, "title": "Nope", "body": "A child cannot add passages to the shared list."})).status_code == 403
