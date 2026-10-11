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

## Super admin

The platform super admin is `SUPERADMIN_EMAIL` (default `admin@sybeez.ai`). The API bootstraps that account on startup when a password is available. Set `SUPERADMIN_INITIAL_PASSWORD` only for local development, or store it in AWS Secrets Manager as secret `sybeez-school/superadmin` (JSON key `SUPERADMIN_INITIAL_PASSWORD`). The password is not committed.

`/api/v1/platform/*` accepts only that super admin. Set `AUTH_PROVIDER=cognito` plus `COGNITO_USER_POOL_ID` and `COGNITO_CLIENT_ID` to create the super admin in Cognito instead of storing a local password hash. Cognito and Secrets Manager need `boto3` in the API image.

The super admin can create or invite internal staff and grant portals (`platform`, `finance`, `payroll`, `hrm`, `school_admin`) plus extra permission codes. Staff pick a school with `X-School-Id`. The `platform` portal does not open `/platform` routes.

Sessions use `httpOnly` `SameSite` cookies (`sybeez_access`, `sybeez_refresh`). School admin, finance, HR, payroll, platform staff, and the super admin have an absolute 24 hour refresh cap. Teacher, student, and parent refresh tokens rotate for 30 days. Override hours with `SESSION_TTL_HOURS`. `COOKIE_SECURE=auto` sets `Secure` when `APP_ENV=production`.

## Demo seed

Demo data does not load in production. It runs only when `SEED_DEMO_DATA=true`, and it reads `DEMO_PLATFORM_PASSWORD` and `DEMO_SCHOOL_ADMIN_PASSWORD` from the environment.

```bash
SEED_DEMO_DATA=true DEMO_PLATFORM_PASSWORD=... DEMO_SCHOOL_ADMIN_PASSWORD=... make seed
```

Database: `sybeez_school` (dedicated local DB).

## Platform flow

1. Platform admin logs in
2. `POST /api/v1/platform/schools` creates a school (+ optional admin password)
3. School admin logs in → `/school` portal
4. Or invite via `POST /api/v1/platform/schools/{id}/invitations` then accept
