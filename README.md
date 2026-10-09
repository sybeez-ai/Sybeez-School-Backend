# Sybeez School Backend

FastAPI backend for the Sybeez School Platform, aligned with `Sybeez-School-Frontend`.

## Services

| Service | Port | Role |
|---------|------|------|
| `apps/api` | 8000 | Core school platform API |
| `apps/ai` | 8001 | Legacy AI service (dev only). Production AI runs from `Sybeez-School-AI-Service`. |
| `apps/worker` | — | Background jobs (Redis) |

## Roles

- `PLATFORM_ADMIN` — Sybeez company side (create schools, assign school admins)
- `SCHOOL_ADMIN` — school tenant admin
- `TEACHER` / `STUDENT` / `PARENT` — school-scoped roles

All school-owned resources enforce `school_id` server-side.

## Quick start

```bash
cp .env.example .env
docker compose -f docker-compose.dev.yml up -d
make migrate
make seed
make api
```

API docs: http://localhost:8000/docs

## Seeded credentials
Development only. Seeding creates known-password accounts and is blocked when `APP_ENV=production`.

| Role | Email | Password | Portal |
|------|-------|----------|--------|
| Platform | `platform.dev@sybeez.ai` | `DevLogin123!` | `/login/platform` |
| School Admin | `admin@greenvalley.edu` | `DevLogin123!` | `/login/school` |
| Teacher | `teacher@greenvalley.edu` | `DevLogin123!` | `/login/teacher` |
| Student | `student@greenvalley.edu` | `DevLogin123!` | `/login/student` |
| Parent | `parent@greenvalley.edu` | `DevLogin123!` | `/login/parent` |

All five portal pages and the `/login` hub show a **Dev login** button in development. Password for every Dev login account is `DevLogin123!`.

Each portal login page shows a **Dev login** button in development that signs in with these credentials through the real auth API. Manual email/password login uses the same path.

```bash
cd apps/api && PYTHONPATH=. python -m src.db.seed
```

Database: `sybeez_school` (dedicated local DB).

## Platform flow

1. Platform admin logs in
2. `POST /api/v1/platform/schools` creates a school (+ optional admin password)
3. School admin logs in → `/school` portal
4. Or invite via `POST /api/v1/platform/schools/{id}/invitations` then accept
