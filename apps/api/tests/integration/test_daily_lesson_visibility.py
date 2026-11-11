"""A lesson for one section is invisible to the other section."""

from __future__ import annotations

import base64
from datetime import date
from uuid import uuid4

import pytest

from src.core.config import get_settings
from tests.integration.online_tests_world import build_world
from tests.integration.test_online_tests_api import api  # noqa: F401

pytestmark = pytest.mark.asyncio
API = "/api/v1/daily-lessons"


async def test_other_section_cannot_read_the_days_lesson(api, db_session):  # noqa: F811
    world = await build_world(db_session)
    await db_session.commit()
    body = {
        "class_id": str(world.class_id),
        "subject_id": str(world.subject_id),
        "taught_on": date.today().isoformat(),
        "chapter": "Light",
        "page_from": 12,
        "page_to": 14,
        "concept": "Reflection",
        "taught_text": "We drew a ray diagram for a plane mirror.",
        "homework_text": "Question 3 on page 14.",
    }
    created = await api.post(API, json=body, headers=world.teacher.h)
    assert created.status_code == 201, created.text
    lesson_id = created.json()["id"]

    for who in (world.teacher, world.admin, world.students[0], world.parent):
        got = await api.get(f"{API}/{lesson_id}", headers=who.h)
        assert got.status_code == 200, (who.role, got.text)
        assert got.json()["concept"] == "Reflection"
        assert got.json()["homework_text"].startswith("Question 3")

    for who in (world.teacher2, world.student_other_class, world.parent_other, world.other_admin):
        hidden = await api.get(f"{API}/{lesson_id}", headers=who.h)
        assert hidden.status_code in {403, 404}, (who.role, hidden.status_code, hidden.text)
        listing = await api.get(API, headers=who.h, params={"class_id": str(world.class_id)})
        assert listing.status_code in {200, 403}
        if listing.status_code == 200:
            assert listing.json() == [], who.role

    denied = await api.post(API, json=body, headers=world.teacher2.h)
    assert denied.status_code == 403, denied.text


PNG = base64.b64encode(
    bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
        "0000000c49444154789c63f8cfc00000000300010005fe02fe0000000049454e44ae426082"
    )
).decode()


async def test_service_stores_board_and_memory_teacher_of_section_reads(api, db_session):  # noqa: F811
    world = await build_world(db_session)
    await db_session.commit()
    token = {"X-Internal-Token": get_settings().ai_service_token}
    student_id = world.students[0].profile_id
    picture = {
        "school_id": str(world.school_id),
        "student_id": str(student_id),
        "class_id": str(world.class_id),
        "taught_on": date.today().isoformat(),
        "topic": "Reflection",
        "content_base64": PNG,
        "scene_spec": {"objects": [{"kind": "mirror"}]},
    }
    missing = await api.post("/internal/v1/ai-teacher/board-pictures", json=picture)
    assert missing.status_code == 401
    stored = await api.post("/internal/v1/ai-teacher/board-pictures", json=picture, headers=token)
    assert stored.status_code == 201, stored.text
    assert stored.json()["scene_spec"]["objects"][0]["kind"] == "mirror"
    assert "file_key" not in stored.json()

    fact = await api.post(
        "/internal/v1/ai-teacher/memory/student",
        json={"school_id": str(world.school_id), "student_id": str(student_id), "kind": "doubt", "content": "Angle of incidence", "concept": "Reflection"},
        headers=token,
    )
    assert fact.status_code == 201, fact.text
    klass = await api.post(
        "/internal/v1/ai-teacher/memory/class",
        json={"school_id": str(world.school_id), "class_id": str(world.class_id), "kind": "common_doubt", "content": "Which ray bounces", "concept": "Reflection"},
        headers=token,
    )
    assert klass.status_code == 201, klass.text
    session = await api.put(
        "/internal/v1/ai-teacher/memory/session",
        json={
            "school_id": str(world.school_id),
            "session_id": str(uuid4()),
            "student_id": str(student_id),
            "class_id": str(world.class_id),
            "summary": "Drew one mirror",
            "facts": {"style": "diagrams"},
        },
        headers=token,
    )
    assert session.status_code == 200, session.text

    pictures = await api.get("/api/v1/ai-teacher/board-pictures", headers=world.teacher.h, params={"class_id": str(world.class_id)})
    assert pictures.status_code == 200 and len(pictures.json()) == 1
    downloaded = await api.get(f"/api/v1/ai-teacher/board-pictures/{stored.json()['id']}", headers=world.teacher.h)
    assert downloaded.status_code == 200 and downloaded.content.startswith(b"\x89PNG")
    memory = await api.get(f"/api/v1/ai-teacher/memory/students/{student_id}", headers=world.teacher.h)
    assert memory.status_code == 200 and memory.json()[0]["kind"] == "doubt"
    other = await api.get("/api/v1/ai-teacher/board-pictures", headers=world.teacher2.h, params={"class_id": str(world.class_id)})
    assert other.status_code == 403, other.text
