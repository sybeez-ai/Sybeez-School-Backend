"""Pure-logic checks for parent messages and the reading buddy, plus the 0066 RLS contract."""

import re
from datetime import date
from pathlib import Path

from src.modules.alerts.catalog import DEFAULT_TEMPLATES, NOTICE_LANGUAGES
from src.modules.parent_messages.languages import LANGUAGES
from src.modules.parent_messages.schemas import PreferenceUpdate
from src.modules.parent_messages.service import preview_text, source_hash
from src.modules.reading_buddy import scoring

MIGRATION = Path(__file__).resolve().parents[2] / "src/db/migrations/versions/0066_parent_messages_reading_buddy.py"

PASSAGE = "The cat sat on the mat. It is a big red cat."


def test_every_chosen_language_has_a_whatsapp_template_for_notices():
    assert set(LANGUAGES) == set(NOTICE_LANGUAGES)
    for event in ("announcement", "progress_update"):
        have = {t.locale for t in DEFAULT_TEMPLATES if t.event_key == event}
        assert have == set(LANGUAGES), event


def test_preference_update_rejects_unknown_language():
    assert PreferenceUpdate(preferred_language="TA").preferred_language == "ta"
    try:
        PreferenceUpdate(preferred_language="xx")
    except ValueError:
        return
    raise AssertionError("unknown language accepted")


def test_hash_changes_with_text_and_preview_is_one_line():
    assert source_hash("a", "b") != source_hash("a", "c")
    assert "\n" not in preview_text("line one\n\nline two " * 40)
    assert len(preview_text("word " * 200)) <= 200


def test_perfect_reading_scores_three_stars():
    r = scoring.analyze(PASSAGE, "the cat sat on the mat it is a big red cat", 30)
    assert r["words_correct"] == r["words_total"]
    assert r["stars"] == 3 and r["accuracy_pct"] == 100.0


def test_skipped_and_misread_words_are_flagged():
    r = scoring.analyze(PASSAGE, "the cat sat on the map it is a red cat", 30)
    statuses = {w["word"]: w["status"] for w in r["word_results"]}
    assert statuses["mat."] == "misread"
    assert statuses["big"] == "skipped"
    assert r["words_skipped"] == 1 and r["words_misread"] == 1


def test_close_sounding_word_counts_as_correct():
    r = scoring.analyze("The elephants walked slowly.", "the elephant walked slowly", 20)
    assert r["words_correct"] == 4


def test_stopping_early_is_not_reached_not_skipped():
    r = scoring.analyze(PASSAGE, "the cat sat on", 10)
    assert r["words_skipped"] == 0
    assert [w["status"] for w in r["word_results"]][-1] == "not_reached"
    assert r["stars"] == 0  # under half read: encouraged, no stars


def test_silence_scores_nothing_but_does_not_crash():
    r = scoring.analyze(PASSAGE, "", 0)
    assert r["stars"] == 0 and r["accuracy_pct"] == 0.0 and r["wcpm"] is None


def test_stars_thresholds():
    assert scoring.stars_for(1.0, 0.95) == 3
    assert scoring.stars_for(1.0, 0.8) == 2
    assert scoring.stars_for(0.7, 0.5) == 1
    assert scoring.stars_for(0.4, 1.0) == 0


def test_streak_rules():
    d = date(2026, 10, 17)
    assert scoring.streak_after(None, d, 0) == 1
    assert scoring.streak_after(date(2026, 10, 16), d, 3) == 4
    assert scoring.streak_after(d, d, 3) == 3  # same day does not extend
    assert scoring.streak_after(date(2026, 10, 10), d, 5) == 1  # gap resets


def test_transcript_is_cleaned_and_capped():
    assert "\x00" not in scoring.clean_transcript("hi\x00there")
    assert len(scoring.clean_transcript("a" * 9000)) == scoring.MAX_TRANSCRIPT_CHARS


def test_migration_gives_every_new_table_forced_rls_and_explicit_policies():
    text = MIGRATION.read_text()
    tables = re.search(r"TABLES = \((.*?)\)", text, re.S).group(1)
    names = re.findall(r'"(\w+)"', tables)
    assert set(names) == {
        "parent_message_preferences", "message_translations", "student_progress_updates",
        "reading_passages", "reading_sessions", "reading_progress",
    }
    assert "FORCE ROW LEVEL SECURITY" in text and "ENABLE ROW LEVEL SECURITY" in text
    # sessions/progress: only the student writes; nobody but the bypass sweeper deletes.
    assert "{ROLE} = 'STUDENT' AND {STUDENT_OK}" in text
    assert '_policy(t, f"{t}_delete", "DELETE", BYPASS, None)' in text
    # students never read translations or progress updates addressed to parents.
    assert text.count("{ROLE} <> 'STUDENT'") >= 2
