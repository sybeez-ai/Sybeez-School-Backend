"""Content library: board tags, filters, role limits and tenant isolation."""

from __future__ import annotations

import pytest

from tests.integration.online_tests_world import build_world
from tests.integration.test_online_tests_api import api  # noqa: F401

pytestmark = pytest.mark.asyncio
API = "/api/v1/content-library"


def _item(**kw):
    base = {"title": "Light notes", "kind": "NOTE", "board": "cbse", "body": "Reflection and refraction", "tags": ["Light", " light ", "physics"]}
    base.update(kw)
    return base


async def test_tagging_filters_and_roles(api, db_session):  # noqa: F811
    w = await build_world(db_session)
    await db_session.commit()
    r = await api.post(API, json=_item(), headers=w.teacher.h)
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["board"] == "CBSE" and item["tags"] == ["light", "physics"]
    assert (await api.post(API, json=_item(title="ICSE q", kind="QUESTION_BANK", board="ICSE", tags=[]), headers=w.admin.h)).status_code == 201
    assert (await api.post(API, json=_item(kind="VIDEO", url="javascript:alert(1)"), headers=w.admin.h)).status_code in (400, 422)
    assert (await api.post(API, json=_item(kind="LINK"), headers=w.admin.h)).status_code in (400, 422)
    assert (await api.post(API, json=_item(board="MARS"), headers=w.admin.h)).status_code in (400, 422)
    # filters
    assert [i["id"] for i in (await api.get(f"{API}?board=CBSE", headers=w.students[0].h)).json()] == [item["id"]]
    assert len((await api.get(f"{API}?board=icse", headers=w.teacher.h)).json()) == 1
    assert [i["id"] for i in (await api.get(f"{API}?tag=Physics", headers=w.teacher.h)).json()] == [item["id"]]
    assert len((await api.get(API, headers=w.admin.h)).json()) == 2
    # students read only; parents blocked
    assert (await api.post(API, json=_item(), headers=w.students[0].h)).status_code == 403
    assert (await api.put(f"{API}/{item['id']}", json=_item(), headers=w.students[0].h)).status_code == 403
    assert (await api.delete(f"{API}/{item['id']}", headers=w.students[0].h)).status_code == 403
    assert (await api.get(API, headers=w.parent.h)).status_code == 403
    # update retags
    up = await api.put(f"{API}/{item['id']}", json=_item(board="STATE", title="Light (state)"), headers=w.teacher.h)
    assert up.status_code == 200 and up.json()["board"] == "STATE"


async def test_other_school_sees_and_changes_nothing(api, db_session):  # noqa: F811
    w = await build_world(db_session)
    await db_session.commit()
    item = (await api.post(API, json=_item(), headers=w.teacher.h)).json()
    assert (await api.get(API, headers=w.other_admin.h)).json() == []
    assert (await api.put(f"{API}/{item['id']}", json=_item(title="hijack"), headers=w.other_admin.h)).status_code == 404
    assert (await api.delete(f"{API}/{item['id']}", headers=w.other_admin.h)).status_code == 404
    assert (await api.delete(f"{API}/{item['id']}", headers=w.admin.h)).status_code == 204
