# Sybeez School Backend

FastAPI backend for the Sybeez School Platform, aligned with `Sybeez-School-Frontend`.

## Services

| Service | Port | Role |
|---------|------|------|
| `apps/api` | 8000 | Core school platform API |
| `apps/ai` | 8001 | AI Teacher / RAG service |
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

| Role | Email | Password | Portal |
|------|-------|----------|--------|
| Platform Admin | `admin@sybeez.com` | `ChangeMe123!` | `/platform` |
| School Admin (Green Valley) | `admin@greenvalley.edu` | `SchoolAdmin123!` | `/school` |

```bash
cd apps/api && PYTHONPATH=. python -m src.db.seed
```

Database: `sybeez_school` (dedicated local DB).

## Platform flow

1. Platform admin logs in
2. `POST /api/v1/platform/schools` creates a school (+ optional admin password)
3. School admin logs in → `/school` portal
4. Or invite via `POST /api/v1/platform/schools/{id}/invitations` then accept
