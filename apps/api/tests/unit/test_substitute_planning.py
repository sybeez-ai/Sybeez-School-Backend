from datetime import time

from src.modules.school_ops.planning import Slot, overlaps, rank_candidates


def slot(eid, tid, day, a, b, subj="m"):
    return Slot(eid, tid, "c1", subj, day, time(*a), time(*b))


def test_overlap_edges():
    assert overlaps(time(9), time(10), time(9, 30), time(10, 30))
    assert not overlaps(time(9), time(10), time(10), time(11))


def test_free_teachers_ranked_and_clashes_removed():
    target = slot("e1", "absent", 0, (9, 0), (9, 45))
    busy = {"t_busy": [slot("e2", "t_busy", 0, (9, 30), (10, 15))], "t_free": [slot("e3", "t_free", 0, (11, 0), (11, 45))]}
    out = rank_candidates(
        target,
        ["absent", "t_busy", "t_free", "t_leave", "t_subject", "t_covering"],
        busy,
        {"t_leave"},
        {"t_covering": [slot("e4", None, 0, (9, 0), (9, 45))]},
        {"t_subject"},
        {"t_subject"},
        {"t_free": 3},
    )
    assert [c["teacher_id"] for c in out] == ["t_subject", "t_free"]
    assert "teaches this subject" in out[0]["reasons"]


def test_other_day_lessons_do_not_block():
    target = slot("e1", "absent", 0, (9, 0), (9, 45))
    out = rank_candidates(target, ["t"], {"t": [slot("e2", "t", 1, (9, 0), (9, 45))]}, set(), {}, set(), set(), {})
    assert [c["teacher_id"] for c in out] == ["t"]
