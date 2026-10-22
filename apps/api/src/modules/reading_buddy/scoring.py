"""Deterministic reading scoring: align what the child said (live transcript) to the passage.

Pure functions, no I/O. The transcript comes from speech recognition, so it is approximate: words
that sound close (edit-distance ratio >= 0.8) count as read correctly, and only words the child
clearly replaced or left out are flagged. Words after the point where the child stopped are
"not reached", not "skipped".
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_WORD = re.compile(r"[a-z0-9']+")
CLOSE = 0.8
MAX_TRANSCRIPT_CHARS = 4000
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean_transcript(text: str) -> str:
    return _CTRL.sub(" ", text or "").strip()[:MAX_TRANSCRIPT_CHARS]


def words(text: str) -> list[str]:
    return _WORD.findall((text or "").lower().replace("’", "'"))


def stars_for(completion: float, accuracy: float) -> int:
    if completion < 0.5:
        return 0
    if completion >= 0.9 and accuracy >= 0.9:
        return 3
    if accuracy >= 0.75:
        return 2
    return 1


def analyze(passage: str, transcript: str, duration_seconds: int | None) -> dict:
    # Keep original display words (with their order) alongside normalised ones.
    shown = passage.split()
    ref = [(w, words(w)) for w in shown]
    ref = [(disp, norm[0]) for disp, norm in ref if norm]  # one normalised token per display word
    said = words(transcript)
    ref_norm = [n for _, n in ref]
    total = len(ref_norm)
    status = ["skipped"] * total
    matcher = SequenceMatcher(a=ref_norm, b=said, autojunk=False)
    last_hit = -1
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for i in range(i1, i2):
                status[i] = "correct"
                last_hit = max(last_hit, i)
        elif tag == "replace":
            heard = said[j1:j2]
            for k, i in enumerate(range(i1, i2)):
                if k < len(heard):
                    close = SequenceMatcher(a=ref_norm[i], b=heard[k]).ratio() >= CLOSE
                    status[i] = "correct" if close else "misread"
                    last_hit = max(last_hit, i)
                else:
                    status[i] = "skipped"
        # "delete": passage words with no counterpart stay "skipped"; "insert": extra spoken words ignored
    reached = last_hit + 1
    for i in range(reached, total):
        status[i] = "not_reached"
    correct = status.count("correct")
    misread = status.count("misread")
    skipped = status.count("skipped")
    attempted = max(reached, 0)
    accuracy = (correct / attempted) if attempted else 0.0
    completion = (reached / total) if total else 0.0
    secs = duration_seconds or 0
    wcpm = round(correct / (secs / 60), 1) if secs >= 10 else None
    return {
        "words_total": total,
        "words_correct": correct,
        "words_misread": misread,
        "words_skipped": skipped,
        "reached": reached,
        "completion": round(completion, 3),
        "accuracy_pct": round(accuracy * 100, 1),
        "wcpm": wcpm,
        "stars": stars_for(completion, accuracy),
        "word_results": [{"word": d, "status": s} for (d, _), s in zip(ref, status, strict=True)],
    }


def encouragement(stars: int, completion: float) -> str:
    if stars >= 3:
        return "Wow, you read so clearly! Three shining stars for you!"
    if stars == 2:
        return "Great reading! You earned two stars. Try the tricky words once more next time."
    if stars == 1:
        return "Nice job! You earned a star. Every time you read, you get stronger."
    if completion < 0.5:
        return "Good try! Let's read a little more next time. You can do it!"
    return "Good try! Keep practising and the stars will come."


def streak_after(last_read_on, today, current: int) -> int:
    """Consecutive reading days. Reading twice in a day does not extend the streak."""
    if last_read_on is None:
        return 1
    delta = (today - last_read_on).days
    if delta == 0:
        return max(current, 1)
    if delta == 1:
        return current + 1
    return 1
