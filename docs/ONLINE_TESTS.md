# Online MCQ tests

Single-answer MCQ tests that a teacher authors (by hand or as AI drafts), students take in a secure web test
mode, and the server grades. Code: `apps/api/src/modules/online_tests`, migration `0064`.

## Rules (v1)
* Teachers publish without admin approval. School switch (`online_test_school_settings.enabled`, default on).
* One attempt per student. The server owns the clock (`clock_timestamp()`); `deadline_at = min(start + duration, closes_at)`.
  Past the deadline (+5 s network grace) an attempt is auto-submitted lazily on any request and by the worker sweeper.
* Resume is allowed while the deadline has not passed; it never extends the clock. More than `max_resumes` flags the attempt.
* Violations (`FULLSCREEN_EXIT`, `TAB_HIDDEN`, `WINDOW_BLUR`, copy/paste/print attempts) are counted once per incident
  (1.5 s de-dupe). Warn twice, auto-submit on the third (`max_violations`, default 3; 0 = log only). Auto-submitted attempts
  are held (`review_status = PENDING`): the teacher can ACCEPT, REOPEN (extra minutes) or VOID.
* Students see results and correct answers only after the teacher releases them (`POST /online-tests/{id}/release-results`
  or `result_release = AFTER_CLOSE`). Held attempts stay hidden until accepted.
* AI generation returns drafts only (`POST /online-tests/{id}/ai-questions`, Core -> AI `/api/v1/ai/mcq/generate`).
  Drafts are not stored until the teacher saves them; AI questions must be reviewed (edited or confirmed) before publish.
  Per-school monthly cap (`ai_monthly_cap`, default 200) counts returned drafts.

## Data and RLS (all tables FORCE ROW LEVEL SECURITY; policies are explicit, see 0064)
| table | who can read | who can write |
| --- | --- | --- |
| `online_tests` | staff, class teachers, students/parents of the class (never drafts) | staff, class teachers |
| `online_test_questions` | staff, class teachers, a student who has an attempt | staff, class teachers |
| `online_test_question_keys` (answer key) | staff, class teachers only | staff, class teachers |
| `online_test_attempts` | owner student, linked parents, class teachers, staff | staff/teachers (review); API service scope (start, grade) |
| `online_test_answers` | owner student, teachers, staff (never parents) | owner student while the attempt is open |
| `online_test_events` | teachers, staff | insert only (students while open); no update/delete except RLS bypass |
| `online_test_school_settings` | whole school | staff |
| `online_test_ai_usage` | teachers, staff | teachers, staff |
| `online_test_audit_logs` | staff; teachers see their own | insert only |

Grading and the sweeper read the key inside `notification_fanout_scope` (a transaction-local RLS bypass) so a student
session never needs key access. Student API models have no field that can carry a key (`test_online_tests_unit.py`).

## Permissions
`online_tests.read|write` teacher, `online_tests.take` student, `online_tests.read` parent (read-only results),
`online_tests.manage` school admin (switch and AI cap). School admins hold all.

## Operations
* Worker calls `POST /internal/v1/online-tests/maintenance` every 30 s (internal token): finalises expired attempts, closes ended tests,
  auto-releases `AFTER_CLOSE` results, sends 24 h / 1 h reminders.
* Core needs `AI_SERVICE_URL` and `AI_SERVICE_TOKEN` (must equal the AI service's `CORE_INTERNAL_TOKEN`).
* Push: best-effort Expo sender (`push.py`), off in development. In-app notifications are the source of truth.
