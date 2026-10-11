from src.modules.homework.router import build_work_router

router = build_work_router(
    prefix="/assignments",
    tag="assignments",
    read="assignments.read",
    write="assignments.write",
    submit="assignments.submit",
    grade="assignments.grade",
)
