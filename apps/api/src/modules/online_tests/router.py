from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.database import get_db
from src.modules.access.deps import require_permission
from src.modules.online_tests import schemas as S
from src.modules.online_tests.service import OnlineTestService

router = APIRouter(prefix="/online-tests", tags=["online-tests"])
attempts_router = APIRouter(prefix="/online-test-attempts", tags=["online-tests"])

DB = Annotated[AsyncSession, Depends(get_db)]
Author = Annotated[CurrentUser, Depends(require_permission("online_tests.write"))]
Taker = Annotated[CurrentUser, Depends(require_permission("online_tests.take"))]
Viewer = Annotated[CurrentUser, Depends(require_permission("online_tests.read"))]
Manager = Annotated[CurrentUser, Depends(require_permission("online_tests.manage"))]
Anyone = Annotated[CurrentUser, Depends(require_permission("session.read"))]


# ── school switch (static paths first) ───────────────────────


@router.get("/config", response_model=S.SchoolSettingsOut)
async def get_config(db: DB, user: Anyone):
    return await OnlineTestService(db).get_settings_out(user)


@router.put("/config", response_model=S.SchoolSettingsOut)
async def update_config(body: S.SchoolSettingsIn, db: DB, user: Manager):
    return await OnlineTestService(db).update_settings(user, body)


# ── student ──────────────────────────────────────────────────


@router.get("/me", response_model=list[S.StudentTestOut])
async def my_tests(db: DB, user: Taker):
    return await OnlineTestService(db).list_for_student(user)


@router.post("/{test_id}/attempts", response_model=S.AttemptStartOut)
async def start_attempt(test_id: UUID, body: S.AttemptStartIn, db: DB, user: Taker):
    return await OnlineTestService(db).start_attempt(user, test_id, body)


# ── parent (read-only) ───────────────────────────────────────


@router.get("/children", response_model=list[S.ChildBriefOut])
async def parent_children(db: DB, user: Viewer):
    return await OnlineTestService(db).parent_children(user)


@router.get("/children/{student_id}", response_model=list[S.ChildTestOut])
async def parent_child_tests(student_id: UUID, db: DB, user: Viewer):
    return await OnlineTestService(db).parent_tests(user, student_id)


# ── teacher / school admin ───────────────────────────────────


@router.get("", response_model=list[S.OnlineTestOut])
async def list_tests(
    db: DB, user: Author, status: str | None = Query(None), class_id: UUID | None = Query(None)
):
    return await OnlineTestService(db).list_tests(user, status=status, class_id=class_id)


@router.post("", response_model=S.OnlineTestDetailOut, status_code=201)
async def create_test(body: S.OnlineTestCreate, db: DB, user: Author):
    return await OnlineTestService(db).create_test(user, body)


@router.get("/{test_id}", response_model=S.OnlineTestDetailOut)
async def get_test(test_id: UUID, db: DB, user: Author):
    return await OnlineTestService(db).get_test(user, test_id)


@router.patch("/{test_id}", response_model=S.OnlineTestDetailOut)
async def update_test(test_id: UUID, body: S.OnlineTestUpdate, db: DB, user: Author):
    return await OnlineTestService(db).update_test(user, test_id, body)


@router.delete("/{test_id}", status_code=204)
async def delete_test(test_id: UUID, db: DB, user: Author):
    await OnlineTestService(db).delete_test(user, test_id)
    return Response(status_code=204)


@router.post("/{test_id}/clone", response_model=S.OnlineTestDetailOut, status_code=201)
async def clone_test(test_id: UUID, db: DB, user: Author):
    return await OnlineTestService(db).clone_test(user, test_id)


@router.post("/{test_id}/publish", response_model=S.OnlineTestDetailOut)
async def publish_test(test_id: UUID, db: DB, user: Author):
    return await OnlineTestService(db).publish(user, test_id)


@router.post("/{test_id}/close", response_model=S.OnlineTestDetailOut)
async def close_test(test_id: UUID, db: DB, user: Author):
    return await OnlineTestService(db).close(user, test_id)


@router.post("/{test_id}/release-results", response_model=S.ReleaseOut)
async def release_results(test_id: UUID, db: DB, user: Author):
    return await OnlineTestService(db).release_results(user, test_id)


@router.post("/{test_id}/questions", response_model=S.OnlineTestDetailOut, status_code=201)
async def add_question(test_id: UUID, body: S.QuestionIn, db: DB, user: Author):
    item = S.QuestionBulkItem(**body.model_dump())
    return await OnlineTestService(db).add_questions(user, test_id, [item], None)


@router.post("/{test_id}/questions/bulk", response_model=S.OnlineTestDetailOut, status_code=201)
async def add_questions_bulk(test_id: UUID, body: S.QuestionBulkIn, db: DB, user: Author):
    return await OnlineTestService(db).add_questions(user, test_id, body.questions, body.ai_generation_id)


@router.post("/{test_id}/questions/reorder", response_model=S.OnlineTestDetailOut)
async def reorder_questions(test_id: UUID, body: S.ReorderIn, db: DB, user: Author):
    return await OnlineTestService(db).reorder(user, test_id, body.question_ids)


@router.post("/{test_id}/questions/confirm-ai", response_model=S.OnlineTestDetailOut)
async def confirm_ai_questions(test_id: UUID, db: DB, user: Author):
    return await OnlineTestService(db).confirm_ai_questions(user, test_id)


@router.patch("/{test_id}/questions/{question_id}", response_model=S.OnlineTestDetailOut)
async def update_question(test_id: UUID, question_id: UUID, body: S.QuestionUpdate, db: DB, user: Author):
    return await OnlineTestService(db).update_question(user, test_id, question_id, body)


@router.delete("/{test_id}/questions/{question_id}", response_model=S.OnlineTestDetailOut)
async def delete_question(test_id: UUID, question_id: UUID, db: DB, user: Author):
    return await OnlineTestService(db).delete_question(user, test_id, question_id)


@router.post("/{test_id}/ai-questions", response_model=S.AiGenerateOut)
async def ai_questions(test_id: UUID, body: S.AiGenerateIn, db: DB, user: Author):
    return await OnlineTestService(db).ai_generate(user, test_id, body)


@router.get("/{test_id}/attempts", response_model=list[S.AttemptRowOut])
async def list_attempts(test_id: UUID, db: DB, user: Author):
    return await OnlineTestService(db).attempts_overview(user, test_id)


@router.get("/{test_id}/analytics", response_model=S.AnalyticsOut)
async def analytics(test_id: UUID, db: DB, user: Author):
    return await OnlineTestService(db).analytics(user, test_id)


@router.get("/{test_id}/results.csv")
async def results_csv(test_id: UUID, db: DB, user: Author):
    body = await OnlineTestService(db).results_csv(user, test_id)
    return Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="online-test-{test_id}.csv"'},
    )


# ── attempts ─────────────────────────────────────────────────


@attempts_router.put("/{attempt_id}/answers/{question_id}", response_model=S.AnswerSavedOut)
async def save_answer(attempt_id: UUID, question_id: UUID, body: S.AnswerIn, db: DB, user: Taker):
    return await OnlineTestService(db).save_answer(user, attempt_id, question_id, body)


@attempts_router.post("/{attempt_id}/events", response_model=S.AttemptStateOut)
async def record_events(attempt_id: UUID, body: S.EventsIn, db: DB, user: Taker):
    return await OnlineTestService(db).record_events(user, attempt_id, body)


@attempts_router.post("/{attempt_id}/heartbeat", response_model=S.AttemptStateOut)
async def heartbeat(attempt_id: UUID, db: DB, user: Taker):
    return await OnlineTestService(db).heartbeat(user, attempt_id)


@attempts_router.post("/{attempt_id}/submit", response_model=S.SubmitOut)
async def submit(attempt_id: UUID, db: DB, user: Taker):
    return await OnlineTestService(db).submit(user, attempt_id)


@attempts_router.get("/{attempt_id}/result", response_model=S.StudentResultOut)
async def my_result(attempt_id: UUID, db: DB, user: Taker):
    return await OnlineTestService(db).student_result(user, attempt_id)


@attempts_router.get("/{attempt_id}", response_model=S.AttemptDetailOut)
async def attempt_detail(attempt_id: UUID, db: DB, user: Author):
    return await OnlineTestService(db).attempt_detail(user, attempt_id)


@attempts_router.post("/{attempt_id}/review", response_model=S.AttemptDetailOut)
async def review_attempt(attempt_id: UUID, body: S.ReviewIn, db: DB, user: Author):
    return await OnlineTestService(db).review_attempt(user, attempt_id, body)
