# Platform Admin portal

SaaS control plane for Sybeez operators. Isolated from school-admin / teacher / student / parent portals.

## Architecture

```text
Browser (/platform/*)
  → Sybeez Backend (/api/v1/platform/*)
    → DB (schools, plans, settings, audit)
    → Cognito (admin / employee auth)
    → AI Service (via backend only — never from browser)
```

## Frontend routes

| Route | Purpose |
|-------|---------|
| `/platform/dashboard` | SaaS metrics, growth, attention |
| `/platform/schools` | List / search / filter |
| `/platform/schools/new` | 5-step create wizard |
| `/platform/schools/:id` | Detail tabs (overview…settings) |
| `/platform/admins` | Global school-admin directory |
| `/platform/admins/:id` | Admin memberships |
| `/platform/team` | Platform employees |
| `/platform/team/invite` | Invite teammate |
| `/platform/team/:id` | Member detail / role |
| `/platform/plans` | Plan cards + create |
| `/platform/plans/:id` | Edit / archive plan |
| `/platform/activity` | Audit log + detail |
| `/platform/settings` | Platform settings |
| `/platform/ai/*` | AI overview / models / providers / usage / settings |

## Key APIs

- `GET /api/v1/platform/dashboard`
- `GET /api/v1/platform/search?q=`
- `GET|POST /api/v1/platform/schools`
- `POST .../schools/{id}/suspend|reactivate|archive`
- `GET /api/v1/platform/admins` · `GET .../admins/{id}`
- `GET|POST|PATCH /api/v1/platform/employees`
- `GET|POST|PATCH /api/v1/platform/plans` · `POST .../archive`
- `GET|PATCH /api/v1/platform/settings`
- `GET|PATCH /api/v1/platform/ai/settings` · models/providers/usage
- `GET /api/v1/platform/activity`

## RBAC

Platform roles: `SUPER_ADMIN`, `PLATFORM_ADMIN` / `ADMIN`, `SUPPORT`, `BILLING`, `OPERATIONS`, `ANALYST`, `USER`.

Sensitive mutations require admin-or-above. Final `SUPER_ADMIN` cannot be deactivated via API.

## Migrations

- `0016` — `platform_plans`, `platform_settings`, `platform_ai_settings`, `platform_ai_usage_daily`, school profile fields

## Run

```bash
# Backend
cd Sybeez-School-Backend/apps/api
alembic upgrade head
uvicorn src.main:app --reload --port 8000

# Frontend
cd Sybeez-School-Frontend/apps/web
npm run dev

# Tests
cd Sybeez-School-Backend/apps/api
python -m pytest tests/unit/test_platform_saas.py -q
```

## Limitations

- Payment provider / invoice automation is interface-level (subscription rows + plan changes); no live PSP charges yet.
- Support impersonation is intentionally not implemented (unsafe without time-boxed audited sessions).
- Academic year defaults on create are UI-only hints until school academic APIs accept them.
- AI provider credentials stay in AI Service / server env — platform UI never returns API keys.
