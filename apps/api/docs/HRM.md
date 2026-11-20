# Sybeez School — HRM Module Guide

**Audience:** school admins, product/engineering, and new team members learning HRM only.  
**Scope:** School HRM portal (`/hrm`). Payroll and Finance are separate workspaces and are only mentioned where data is shared.

---

## 1. What HRM is

HRM (Human Resource Management) is the **people-operations workspace** for a school.

| Owns | Does not own |
|------|----------------|
| Staff directory (teachers as employees) | Student fee collection (Finance / Fees) |
| Staff leave requests & approvals | Full salary processing workflow (Payroll portal) |
| HR letters, files, attendance (portal UI) | General ledger / accounting (Finance) |
| Departments / designations (portal UI) | School academics admin (`/school-admin`) |
| Salary slip viewing (via shared HR APIs) | Bank payments & statutory filing (Payroll / Finance) |

**Portal URLs**

| Step | URL |
|------|-----|
| Login | `http://localhost:3000/hrm/login` |
| Home | `http://localhost:3000/hrm/dashboard` |

Sign in with a **school admin** account (same role as school admin; portal is `hrm`).

Demo (local seed):

- Email: `admin@greenvalley.edu`
- Password: `DevLogin123!`

---

## 2. Sidebar modules (what employees see)

| # | Sidebar item | Route | Purpose |
|---|--------------|-------|---------|
| 1 | Dashboard | `/hrm/dashboard` | Headcount, pending leave, salary-slip run summary |
| 2 | Employees | `/hrm/employees` | Staff directory (from `teachers` API) |
| 3 | People Attendance | `/hrm/attendance` | Mark / review staff attendance |
| 4 | Leave Tracker | `/hrm/leave` | Approve / reject staff leave |
| 5 | HR Letters | `/hrm/letters` | Offer / experience / other letters |
| 6 | Employee Files | `/hrm/files` | Document checklist per employee |
| 7 | Employee Self-Service | `/hrm/self-service` | Profile / request style workflows |
| 8 | Onboarding & Offboarding | `/hrm/onboarding` | Join / exit checklists |
| 9 | Departments & Designations | `/hrm/departments` | Org structure labels |
| 10 | Salary Slips | `/hrm/salary-slips` | View runs & payslips (shared HR payroll APIs) |
| 11 | Reports & Analytics | `/hrm/reports` | Counts from leave + teachers + runs |
| 12 | Settings | `/hrm/settings` | HRM preferences |
| 13 | Notifications | `/hrm/notifications` | HRM alerts |

---

## 3. Database — has the schema been created?

### Short answer

**Yes.** HRM-related tables exist in PostgreSQL via Alembic migrations. Models and migrations are in the Core API repo.

### Where migrations live

```
Sybeez-School-Backend/apps/api/alembic.ini
Sybeez-School-Backend/apps/api/src/db/migrations/versions/
```

### HRM-relevant migration files

| Revision | File | What it creates / changes |
|----------|------|---------------------------|
| `0008` | `0008_teacher_phase4.py` | Extends `teachers` (employee fields: code, phone, joining_date, qualification, photo, unique employee codes) |
| `0023` | `0023_hr_leave_payroll.py` | Creates `staff_leave_requests`, `payroll_runs`, `payroll_payslips` |
| `0024` | `0024_documents_extend.py` | Extends shared `documents` table (usable for files; not HRM-only) |

Teachers master table itself is part of the earlier school schema (user + school membership + `teachers` row). Phase 4 (`0008`) adds HR-oriented columns.

### Apply migrations (local)

```bash
cd Sybeez-School-Backend
# Postgres + Redis up (docker compose / Colima)
cd apps/api
source .venv/bin/activate   # or use project venv
alembic -c alembic.ini upgrade head
```

Or from backend root Makefile (when configured): `make migrate`.

---

## 4. How the database is structured (HRM)

### 4.1 Entity overview

```
schools
   │
   ├── users  ◄── school_memberships (role: SCHOOL_ADMIN | TEACHER | …)
   │
   ├── teachers                    # employee profile (1 user per school)
   │      │
   │      └── payroll_payslips     # salary slip lines
   │               │
   │               └── payroll_runs
   │
   └── staff_leave_requests        # staff leave (user_id → users)
```

### 4.2 Table: `teachers` (employee master for HRM)

**Model:** `src/db/models/teacher.py`  
**API:** `/api/v1/teachers`

| Column | Type | Notes |
|--------|------|--------|
| `id` | UUID PK | |
| `school_id` | UUID FK → schools | Tenant |
| `user_id` | UUID FK → users | Login identity |
| `teacher_code` | VARCHAR(64) | Optional unique per school |
| `employee_code` | VARCHAR(64) | Optional unique per school |
| `title` | VARCHAR(64) | |
| `phone` | VARCHAR(32) | |
| `date_of_birth` | DATE | |
| `joining_date` | DATE | |
| `qualification` | VARCHAR(255) | |
| `photo_url` | TEXT | |
| `status` | VARCHAR(32) | e.g. ACTIVE |
| `created_at` / `updated_at` | TIMESTAMPTZ | |

Unique: `(school_id, user_id)`.

> In Sybeez today, **staff in HRM = teachers table**. Non-teaching staff as a separate employee type is not a separate DB table yet.

### 4.3 Table: `staff_leave_requests`

**Model:** `src/db/models/hr.py` → `StaffLeaveRequest`  
**Migration:** `0023_hr_leave_payroll.py`

| Column | Type | Notes |
|--------|------|--------|
| `id` | UUID PK | |
| `school_id` | UUID FK → schools | |
| `user_id` | UUID FK → users | Requester (usually teacher user) |
| `start_date` / `end_date` | DATE | |
| `reason` | TEXT | |
| `status` | VARCHAR(32) | `PENDING` / `APPROVED` / `REJECTED` / cancelled flow |
| `decided_by` | UUID FK → users | Admin who decided |
| `decided_at` | TIMESTAMPTZ | |
| `decision_note` | TEXT | |
| `created_at` / `updated_at` | TIMESTAMPTZ | |

### 4.4 Tables: `payroll_runs` + `payroll_payslips`

Used by **HRM Salary Slips** and by the **Payroll portal** (same Core API `/hr/payroll/*`).

**`payroll_runs`**

| Column | Notes |
|--------|--------|
| `label`, `period_start`, `period_end` | Run identity |
| `status` | e.g. `DRAFT` |
| `created_by`, `finalized_by`, `finalized_at` | Audit |

**`payroll_payslips`**

| Column | Notes |
|--------|--------|
| `payroll_run_id` | FK → payroll_runs |
| `teacher_id` | FK → teachers |
| `gross_amount`, `deductions_amount`, `net_amount` | NUMERIC(12,2) |
| `status`, `paid_on`, `notes` | |

### 4.5 What is *not* in PostgreSQL yet (browser / local store)

These HRM sidebar areas store data in **browser `localStorage`** (keyed by school), not Alembic tables:

- People Attendance (HRM UI)
- HR Letters
- Employee Files metadata (beyond generic documents API)
- Onboarding & Offboarding checklists
- Departments & Designations
- Self-service request queue
- HRM Settings / Notifications (portal-local)

**Implication:** clearing browser storage resets those UI records. Employees / leave / salary slips remain in Postgres.

---

## 5. How the API works

### 5.1 Base

- Core API: `http://localhost:8000`
- Prefix: `/api/v1`
- Auth: Bearer access token from login
- OpenAPI: `http://localhost:8000/docs`

### 5.2 Auth for HRM

| Portal | Login page | Backend `portal` value | Allowed role |
|--------|------------|------------------------|--------------|
| HRM | `/hrm/login` | `hrm` | `school_admin` |

Login body includes `portal: "hrm"` so school admins land in the HRM workspace (not school-admin dashboard).

Teachers use `/login/teacher` for **their own** leave/payslip endpoints (`/leave/requests/me`, `/payroll/payslips/me`).

### 5.3 HR module router

**File:** `src/modules/hr/router.py`  
**Mount:** `/api/v1/hr`

#### Health

```http
GET /api/v1/hr/health
```

#### Staff leave

| Method | Path | Role | Description |
|--------|------|------|-------------|
| GET | `/hr/leave/requests` | school_admin | List all school leave (`?status=`) |
| GET | `/hr/leave/requests/me` | teacher | My leave requests |
| POST | `/hr/leave/requests` | teacher | Create leave |
| POST | `/hr/leave/requests/{id}/cancel` | teacher | Cancel own pending |
| POST | `/hr/leave/requests/{id}/decide` | school_admin | Approve / Reject |

**Decide body**

```json
{ "decision": "APPROVED", "decision_note": "optional" }
```

#### Salary slips (shared payroll APIs)

| Method | Path | Role | Description |
|--------|------|------|-------------|
| GET | `/hr/payroll/runs` | school_admin | List runs |
| POST | `/hr/payroll/runs` | school_admin | Create run |
| GET | `/hr/payroll/runs/{id}` | school_admin | Run + payslips |
| POST | `/hr/payroll/runs/{id}/payslips` | school_admin | Add payslip |
| PATCH | `/hr/payroll/payslips/{id}` | school_admin | Update payslip |
| GET | `/hr/payroll/payslips/me` | teacher | My slips |
| GET | `/hr/payroll/payslips/{id}/pdf` | admin or teacher | PDF download |

### 5.4 Employees (teachers) API

**File:** `src/modules/teachers/router.py`  
**Mount:** `/api/v1/teachers`

| Method | Path | Role | Description |
|--------|------|------|-------------|
| GET | `/teachers` | school_admin | Directory (`q`, `status`, …) |
| POST | `/teachers` | school_admin | Create employee/teacher |
| GET | `/teachers/{id}` | admin / teacher | Detail |
| PATCH | `/teachers/{id}` | school_admin | Update |
| PATCH | `/teachers/{id}/status` | school_admin | Activate / deactivate |
| GET | `/teachers/me` | teacher | Own profile |

HRM **Employees** page calls `GET /api/v1/teachers`.

### 5.5 Frontend API clients

| Client | Path |
|--------|------|
| Leave + payroll | `apps/web/src/features/hr/api/hr.api.ts` |
| Teachers | `apps/web/src/features/school/api/teachers.api.ts` |
| Local HRM UI store | `apps/web/src/features/hrm/store/hrm.store.ts` |

### 5.6 Example flows

**Approve leave (admin)**

1. `POST /api/v1/auth/login` with `portal: "hrm"`
2. `GET /api/v1/hr/leave/requests?status=PENDING`
3. `POST /api/v1/hr/leave/requests/{id}/decide` with `APPROVED`

**List employees**

1. Login as HRM admin  
2. `GET /api/v1/teachers`

**Create salary slip (from HRM Salary Slips UI)**

1. `POST /api/v1/hr/payroll/runs`
2. `POST /api/v1/hr/payroll/runs/{id}/payslips` with `teacher_id`, amounts
3. Optional PDF: `GET /api/v1/hr/payroll/payslips/{id}/pdf`

---

## 6. Backend code map (HRM)

| Layer | Path |
|-------|------|
| Models | `src/db/models/hr.py`, `src/db/models/teacher.py` |
| Migrations | `src/db/migrations/versions/0008_*.py`, `0023_hr_leave_payroll.py` |
| HR service | `src/modules/hr/service.py` |
| HR schemas | `src/modules/hr/schemas.py` |
| HR router | `src/modules/hr/router.py` |
| Teachers | `src/modules/teachers/*` |
| Auth portals | `src/modules/auth/service.py` (`_PORTAL_ROLES["hrm"]`) |

---

## 7. Frontend code map (HRM portal)

| Layer | Path |
|-------|------|
| Routes | `apps/web/src/app/(hrm)/hrm/**` |
| Login | `apps/web/src/app/(hrm-auth)/hrm/login` |
| Shell | `apps/web/src/components/layout/hrm/HrmShell.tsx` |
| Nav | `apps/web/src/config/hrm-navigation.ts` |
| Features | `apps/web/src/features/hrm/components/*` |
| Route constants | `apps/web/src/config/routes.ts` → `routes.hrm` |

---

## 8. Data source by feature (cheat sheet)

| Feature | Postgres | localStorage | Notes |
|---------|----------|--------------|-------|
| Employees | Yes (`teachers`) | No | |
| Leave Tracker | Yes (`staff_leave_requests`) | No | |
| Salary Slips | Yes (`payroll_*`) | No | Same APIs as Payroll portal |
| Dashboard aggregates | Yes (API reads) | Partial | Mix of API + local |
| Attendance / Letters / Files / Onboarding / Departments / Self-service / Settings / Notifications | No dedicated HRM tables | Yes | Phase-2 persistence candidates |

---

## 9. Running HRM locally

1. **Postgres + Redis** (backend docker compose)  
2. **Migrate + seed** Core API  
3. **API** on `:8000` (`uvicorn` / `make api`)  
4. **Web** on `:3000`  
5. Open `/hrm/login` → dashboard  

Health checks:

```bash
curl -s http://localhost:8000/health
curl -s http://localhost:8000/api/v1/hr/health
```

---

## 10. Boundaries (important for the team)

1. **HRM** = people ops (directory, leave, letters, files, onboarding UI).  
2. **Payroll portal** (`/school/payroll`) = salary calculation, approval, payments — may reuse `/api/v1/hr/payroll/*` and `payroll_*` tables.  
3. **Finance** (`/finance`) = ledger & accounting; payroll *accounting* only, not HRM.  
4. Student **leave_requests** (attendance module) are **not** staff leave — different table/feature.

---

## 11. FAQ

**Q: Has the HRM database schema been created?**  
A: Yes — teachers HR fields (`0008`), staff leave + payslip tables (`0023`).

**Q: Are Alembic migration files available?**  
A: Yes — under `apps/api/src/db/migrations/versions/`, especially `0008_teacher_phase4.py` and `0023_hr_leave_payroll.py`.

**Q: Is every sidebar item backed by the database?**  
A: No. Employees, leave, and salary slips are. Attendance, letters, files, departments, onboarding, self-service, settings, and notifications currently use the browser HRM store.

**Q: Who can use HRM login?**  
A: School admins (`portal=hrm`). Teachers use the teacher portal for their own leave and payslips.

---

*Document version: aligned with codebase as of Oct 2026. HRM only.*
