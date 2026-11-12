"""Personal learning path, computed only from the student's own completed practice attempts.

mastery = mean score of the last 5 attempts on a topic (a simple, explainable percentage). No invented numbers: a
topic with no attempts does not appear, and a student with no attempts is told to start with a first practice.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import PracticeAttempt, Subject

LAST_N = 5
WEAK, STRONG = 60.0, 80.0
REVISIT_AFTER = timedelta(days=7)


@dataclass
class Attempt:
    subject_id: UUID | None
    topic: str
    score: float
    at: datetime


def build_path(attempts: list[Attempt], names: dict[UUID, str], now: datetime) -> dict:
    groups: dict[tuple[UUID | None, str], list[Attempt]] = defaultdict(list)
    for a in attempts:
        groups[(a.subject_id, a.topic.strip().lower())].append(a)
    topics = []
    for (sub, _), items in groups.items():
        items.sort(key=lambda x: x.at, reverse=True)
        recent = items[:LAST_N]
        mastery = round(sum(x.score for x in recent) / len(recent), 1)
        last = items[0].at
        stale = now - last > REVISIT_AFTER
        if mastery < WEAK:
            status, action = "weak", "Practise this again. Take it slowly and ask your AI teacher when you are stuck."
        elif mastery < STRONG:
            status, action = "building", "Nearly there. One more practice set should lift it."
        elif stale:
            status, action = "revisit", "You knew this well. A quick revisit keeps it fresh."
        else:
            status, action = "strong", "Strong. Keep it up."
        topics.append({
            "subject_id": str(sub) if sub else None,
            "subject_name": names.get(sub) if sub else None,
            "topic": items[0].topic.strip(),
            "mastery": mastery,
            "attempts": len(items),
            "last_practised": last.date().isoformat(),
            "status": status,
            "next_step": action,
        })
    order = {"weak": 0, "building": 1, "revisit": 2, "strong": 3}
    topics.sort(key=lambda t: (order[t["status"]], t["mastery"]))
    steps = [t for t in topics if t["status"] != "strong"][:5]
    return {
        "has_data": bool(topics),
        "topics": topics,
        "next_steps": steps,
        "message": None if topics else "Do your first practice quiz and your path will appear here.",
        "method": f"Mastery is the average of your last {LAST_N} practice scores on each topic.",
    }


async def path_for_user(db: AsyncSession, user_id: UUID) -> dict:
    rows = (
        await db.execute(
            select(PracticeAttempt)
            .where(PracticeAttempt.student_user_id == user_id, PracticeAttempt.completed_at.is_not(None),
                   PracticeAttempt.score_pct.is_not(None), PracticeAttempt.topic.is_not(None))
            .order_by(PracticeAttempt.completed_at.desc())
            .limit(300)
        )
    ).scalars().all()
    attempts = [Attempt(r.subject_id, r.topic or "", float(r.score_pct or 0), r.completed_at) for r in rows if (r.topic or "").strip()]
    ids = {a.subject_id for a in attempts if a.subject_id}
    names = dict((await db.execute(select(Subject.id, Subject.name).where(Subject.id.in_(ids)))).all()) if ids else {}
    return build_path(attempts, names, datetime.now(timezone.utc))
