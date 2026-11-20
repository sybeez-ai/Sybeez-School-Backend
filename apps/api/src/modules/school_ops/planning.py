"""Pure scheduling helpers for substitute cover (no database)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time


@dataclass(frozen=True)
class Slot:
    entry_id: str
    teacher_id: str | None
    class_id: str
    subject_id: str
    day: int
    starts: time
    ends: time


def overlaps(a_start: time, a_end: time, b_start: time, b_end: time) -> bool:
    return a_start < b_end and b_start < a_end


def rank_candidates(
    slot: Slot,
    teachers: list[str],
    busy: dict[str, list[Slot]],
    on_leave: set[str],
    covering: dict[str, list[Slot]],
    subject_teachers: set[str],
    class_teachers: set[str],
    cover_counts: dict[str, int],
) -> list[dict]:
    """Teachers free for this lesson, best first: knows the class, teaches the subject, fewest covers.

    Free means: not the absent teacher, not on approved leave that day, no own lesson overlapping
    and no cover already confirmed at that time.
    """
    out: list[dict] = []
    for tid in teachers:
        if tid == slot.teacher_id or tid in on_leave:
            continue
        clash = any(overlaps(slot.starts, slot.ends, b.starts, b.ends) for b in busy.get(tid, []) if b.day == slot.day)
        clash = clash or any(overlaps(slot.starts, slot.ends, c.starts, c.ends) for c in covering.get(tid, []))
        if clash:
            continue
        reasons = []
        score = 0
        if tid in subject_teachers:
            score += 2
            reasons.append("teaches this subject")
        if tid in class_teachers:
            score += 1
            reasons.append("knows this class")
        covers = cover_counts.get(tid, 0)
        out.append({"teacher_id": tid, "score": score, "covers_this_month": covers, "reasons": reasons})
    out.sort(key=lambda c: (-c["score"], c["covers_this_month"], c["teacher_id"]))
    return out
