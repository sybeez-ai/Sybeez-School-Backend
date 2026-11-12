from datetime import datetime, timedelta, timezone
from uuid import uuid4

from src.modules.learning_paths.service import Attempt, build_path

NOW = datetime(2026, 10, 18, tzinfo=timezone.utc)
SUB = uuid4()


def a(topic, score, days_ago):
    return Attempt(SUB, topic, score, NOW - timedelta(days=days_ago))


def test_empty_says_start():
    r = build_path([], {}, NOW)
    assert not r["has_data"] and "first practice" in r["message"] and r["next_steps"] == []


def test_orders_weak_first_and_uses_last_five_only():
    att = [a("Fractions", 20, 30)] + [a("Fractions", 90, d) for d in range(1, 6)] + [a("Algebra", 40, 2), a("Geometry", 70, 1)]
    r = build_path(att, {SUB: "Maths"}, NOW)
    assert [t["topic"] for t in r["topics"]] == ["Algebra", "Geometry", "Fractions"]
    frac = r["topics"][2]
    assert frac["mastery"] == 90.0 and frac["status"] == "strong" and frac["attempts"] == 6
    assert [s["topic"] for s in r["next_steps"]] == ["Algebra", "Geometry"]
    assert r["topics"][0]["subject_name"] == "Maths"


def test_strong_but_stale_is_revisit_and_case_insensitive_topics():
    r = build_path([a("Fractions", 95, 10), a("fractions ", 85, 12)], {}, NOW)
    assert len(r["topics"]) == 1 and r["topics"][0]["status"] == "revisit"
