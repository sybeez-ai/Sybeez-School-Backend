# Sybeez School payroll

School admins configure payroll themselves. Nothing in this module needs a developer to add a salary head, a holiday, a weekly off, a deduction, or a role.

Salary is calculated the day the period's staff attendance is locked, including loss of pay. The generated run waits in the approval queue. Payslips are published only after a different person approves, unless the school turns on `auto_publish` (default off).

## Merge order

This branch adds Alembic revision `0040` (`0040_payroll_production.py`, `down_revision = 0035`) and revision `0041` (`0041_payroll_daily.py`, `down_revision = 0040`). Land them on `HRM` after `0035`. `alembic upgrade head` on an empty Postgres 16 database applies `0001` through `0041`.

Finance PR #1 uses revision ids `0028`, `0029`, and `0030` for different files than HRM's `0028_hr_integrity`, `0029_hr_employees`, and `0030_hr_departments`. Do not reuse those ids. When finance merges, renumber its revisions so they are not `0028`–`0035` or `0040`, then point `0040` at the new head if finance must run first. If the `sybeez_definer` role exists at upgrade time, payroll `SECURITY DEFINER` functions are altered to that owner.

Production requires `S3_BUCKET`. The app stores object keys only and does not create AWS resources. Development writes under `apps/api/media` when the bucket is empty.

## Roles

Membership role or a row in `payroll_role_grants` (effective dates). School admin and platform admin always pass.

| Role | Can |
| --- | --- |
| Payroll admin | Settings, prepare, approve, grants |
| HR manager | Settings, prepare, attendance lock |
| Accountant | Prepare, read, audit |
| Approver | Approve or reject, cannot approve own submission when `require_different_approver` is on |
| Auditor | Read and audit only |
| Employee | Own profile, own declaration, own published payslips, own attendance |

Dashboard routes allow school admin, platform admin, payroll admin, HR, accountant, and auditor. An approver-only user does not.

## Defaults still open

- PF 12% of basic, wage ceiling ₹15,000 (`CAPPED`). A school can switch an employee to `FULL_BASIC`. EPS 8.33% stays on the capped wage. Enrolment is off until the admin opts the employee in.
- ESI 0.75% / 3.25%, monthly gross threshold ₹21,000. Enrolment is off until the admin opts the employee in. This is a monthly check, not the full ESI contribution-period rule.
- Professional tax seeds Karnataka, Maharashtra, and Tamil Nadu. Karnataka: nil below ₹25,000, otherwise ₹200 (₹300 in February). Maharashtra: men 0 / 175 / 200 with February ₹300; women exempt to ₹25,000. Tamil Nadu half-year slabs are spread as amount/6 and may not sum exactly to the half-year figure. Gender `OTHER` follows the male slabs. Admins replace slabs per state.
- New tax regime is the default (Budget 2025 slabs, standard deduction ₹75,000, rebate when taxable income is at most ₹12,00,000). Old regime is a per-employee opt-in. Cess 4%. Monthly TDS is annual/12. One-time arrears are taxed once.
- Pay date is the last working day (`LAST_WORKING_DAY`) or a fixed day. Rounding is `NEAREST_RUPEE` or `PAISE`. LOP base is `BASIC` or `GROSS`. Unmarked working day is 1 LOP, half day is 0.5, approved paid leave is 0. Optional and restricted holidays do not reduce working days. Zero working days pays the full month.
- Bank file is `GENERIC_NEFT` CSV. Settings also accept `HDFC`, `ICICI`, `SBI`, and `AXIS`. A bank response CSV marks each row paid (with a UTR) or failed. Retry puts only failed payments back to pending. The next bank file includes pending rows only.
- Shift default 09:00–16:00, grace 10 minutes, full day 360 minutes, half day 180. Geofence is off. Late and early are flags; they are not LOP by themselves.
- Bonus default off. Gratuity 15/26 × (basic + DA) × years, minimum 5 years, cap ₹20,00,000.
- Starter template "Standard" is BASIC ₹30,000, HRA 40% of basic, DA 0%. That is a starter, not a statutory rate.
- PF wage ceiling stays ₹15,000 here even if a later circular moves the statutory ceiling.
- Attendance late/early uses `schools.timezone`, falling back to `Asia/Kolkata` when the name is invalid. The platform default timezone is UTC until a school sets its own.

## Research

- https://greythr.freshdesk.com/support/solutions/articles/1060000049917-wings-plan-features
- https://www.greythr.com/payroll-software/
- https://www.greythr.com/blog/greythr-vs-keka/
- https://keka.com/payroll-software
- https://www.keka.com/payroll-software-features
- Karnataka professional tax amendment, Zoho Payroll PT rules, ezhrm 2026 PT guide, Tax Garden 2026, calcguru PT calculator

## Endpoint table

Prefix `/api/v1`. Errors are `{ "error": { "code", "message" } }`. Money is a decimal string. Dates are `YYYY-MM-DD`. Dashboard queries are school-scoped (RLS plus an explicit `school_id` filter). Omit `period_start` and `period_end` to use the current calendar month.

### Dashboard

| Method | Path | Who | Response |
| --- | --- | --- | --- |
| GET | `/payroll/dashboard?period_start&period_end` | dashboard roles | All sections below in one object |
| GET | `/payroll/dashboard/period` | dashboard roles | `{ period_start, period_end, processed: { headcount, gross, deductions, net }, pending: { headcount, gross, deductions, net, unassigned_headcount } }`. Processed means approved, published, paid, or locked. Pending means draft or waiting for approval, plus active employees with no payslip. |
| GET | `/payroll/dashboard/pipeline` | dashboard roles | `{ period_start, period_end, counts: { DRAFT, PENDING_APPROVAL, APPROVED, REJECTED, PUBLISHED, LOCKED, PAID, CANCELLED } }` |
| GET | `/payroll/dashboard/payslips` | dashboard roles | `{ generated, published, viewed, downloaded, by_status }`. A view or download is counted only when the employee opens their own payslip. |
| GET | `/payroll/dashboard/cost` | dashboard roles | `{ by_department, by_branch, by_designation }` each a list of `{ name, headcount, gross, deductions, net }`. Branch comes from the statutory profile `branch_name`; missing values are `Unassigned`. |
| GET | `/payroll/dashboard/components` | dashboard roles | `{ earnings, deductions, employer }` lists of `{ code, name, amount }` |
| GET | `/payroll/dashboard/trend?period_end` | dashboard roles | `{ months: [{ month, headcount, gross, deductions, net }] }` for 12 months ending at `period_end` |
| GET | `/payroll/dashboard/statutory` | dashboard roles | `{ dues: [{ code, label, due_on, employee_share, employer_share, amount }], calendar, note }`. Due dates: PF 15th, ESI 15th, PT 20th, TDS 7th of the following month. EPS is inside employer PF. |
| GET | `/payroll/dashboard/attendance` | dashboard roles | `{ working_days, lop_days, paid_days, employees_with_lop, marks: { PRESENT, ABSENT, HALF_DAY, LEAVE } }` |
| GET | `/payroll/dashboard/templates` | dashboard roles | `{ templates: [{ template_id, name, grade_code, is_active, assigned_employees }] }` |
| GET | `/payroll/dashboard/pending` | dashboard roles | `{ approvals: [{ run_id, label, period_start, period_end, submitted_at, headcount, net }], regularisations: [{ id, employee_id, employee_name, employee_code, attendance_date, reason, requested_check_in, requested_check_out }] }` |

### Settings and catalogue

| Method | Path | Request | Response |
| --- | --- | --- | --- |
| GET | `/payroll/settings` |  | Settings object, including shift, geofence, LOP, PT state, rounding, pay date, approval, payslip template, colours |
| PUT | `/payroll/settings` | settings fields plus `reason` | Same shape. Writes a version row. |
| GET | `/payroll/settings/versions` |  | `[{ id, effective_from, snapshot, reason, changed_by, created_at }]` |
| POST | `/payroll/settings/logo` | multipart file, max 1 MB | `{ id, logo_object_key }` |
| GET | `/payroll/payslip-templates` |  | `{ templates: [{ code: CLASSIC\|COMPACT\|STATUTORY, name }] }` |
| GET | `/payroll/components` |  | Component rows |
| POST | `/payroll/components` | `{ code, name, kind: EARNING\|DEDUCTION, calc_type: FIXED\|PERCENT\|FORMULA, amount, percent, percent_of, formula, taxable, pf_wage, esi_wage, prorate, display_order, effective_from, reason }` | Component |
| POST | `/payroll/components/{id}/retire` | `{ reason }` | Component |
| GET | `/payroll/salary-templates` |  | `[{ template, lines }]` |
| POST | `/payroll/salary-templates` | `{ name, grade_code, lines: [{ component_id, amount, percent }], reason }` | `{ template, lines }` |
| GET | `/payroll/salary-assignments?employee_id` |  | Assignments for the school, with `template_name`. An employee with no payroll role sees only their own. |
| POST | `/payroll/salary-assignments` | `{ employee_id, template_id, effective_from, overrides, arrears_amount, reason }` | Assignment. Closes the previous open assignment. |
| GET | `/payroll/employees/{employee_id}/profile` |  | Statutory profile or `{ employee_id }` |
| PUT | `/payroll/employees/{employee_id}/profile` | `{ pan, uan, pf_number, esi_number, gender, tax_regime, metro, pt_state, branch_name, pf_enrolled, pf_wage_mode: CAPPED\|FULL_BASIC, pf_employee_rate, pf_employer_rate, pf_effective_from, pf_effective_to, vpf_rate, vpf_amount, esi_enrolled, esi_effective_from, esi_effective_to, reason }` | Profile |
| PUT | `/payroll/employees/{employee_id}/bank` | `{ account_holder, account_number, ifsc, bank_name, reason }` | Bank with `account_last4` |
| GET | `/payroll/me/profile` |  | `{ employee_id, profile, bank }` bank masked |
| GET | `/payroll/weekly-offs` |  | Rules. `weekday` 0 Monday … 6 Sunday. `occurrence` ALL, 1–5, LAST. Empty `branch_name` is the whole school. |
| PUT | `/payroll/weekly-offs` | `{ rules: [{ weekday, occurrence, branch_name }], reason }` | Replaced rules |
| GET | `/payroll/holidays?year` |  | Holidays. `kind` NATIONAL, STATE, SCHOOL, OPTIONAL, RESTRICTED. |
| POST | `/payroll/holidays` | `{ holiday_date, name, kind, branch_name, reason }` | Holiday |
| DELETE | `/payroll/holidays/{id}?reason` |  | 204 |
| GET | `/payroll/pt-slabs?state` |  | School slabs, or the platform seed when the school has none |
| PUT | `/payroll/pt-slabs/{state}` | `{ slabs: [{ min_amount, max_amount, amount, february_amount, gender, frequency, basis }], reason }` | Slabs |
| GET | `/payroll/loans?employee_id` |  | Loans |
| POST | `/payroll/loans` | `{ employee_id, kind: LOAN\|ADVANCE, principal, emi, annual_rate, start_date, reason }` | Loan |
| POST | `/payroll/loans/{id}/pause` | `{ paused_until, reason }` | Loan |
| GET | `/payroll/arrears?employee_id&status` |  | Arrears. Staff see only their own. |
| POST | `/payroll/arrears` | `{ employee_id, amount, reason }` | Arrear |
| GET | `/payroll/reimbursements?employee_id&status` |  | Reimbursements. Staff see only their own. |
| POST | `/payroll/reimbursements` | `{ employee_id, for_self, amount, note }` | Reimbursement. Staff omit `employee_id` or set `for_self`. |
| POST | `/payroll/reimbursements/{id}/decide` | `{ approve, reason }` | Reimbursement |
| GET | `/payroll/me/declaration?financial_year` |  | The caller's saved declaration. 404 when none is saved. Omit the year to get the latest. |
| PUT | `/payroll/me/declaration` | `{ financial_year, regime, section_80c, section_80d, other_chapter_vi, annual_rent }` | Declaration |
| GET | `/payroll/declarations?financial_year&employee_id&status` |  | Declarations in this school. Payroll read roles only. |
| PUT | `/payroll/declarations` | same, plus `employee_id` | Declaration |
| POST | `/payroll/declarations/{id}/proofs` | query `section`, `amount`, `note`; optional file | Proof |
| POST | `/payroll/declarations/{id}/verify` | `{ reason }` | Declaration |
| GET | `/payroll/roles` |  | Grants |
| POST | `/payroll/roles` | `{ user_id, role, effective_from, effective_to, reason }` | Grant. Roles: PAYROLL_ADMIN, HR_MANAGER, ACCOUNTANT, PAYROLL_APPROVER, AUDITOR |
| DELETE | `/payroll/roles/{id}?reason` |  | 204 |
| GET | `/payroll/audit?limit` |  | Audit rows: actor, action, entity, before, after, reason |
| GET | `/payroll/compliance` |  | Events |
| POST | `/payroll/compliance` | `{ title, due_on, kind, notes, reason }` | Event |
| POST | `/payroll/preview` | `{ employee_id, period_start, period_end }` | Computed pay, not saved |
| POST | `/payroll/runs/generate` | `{ period_start, period_end, reason }` | `{ run, created, skipped }` |
| GET | `/payroll/runs` |  | Runs |
| GET | `/payroll/runs/{id}` |  | `{ run, payslips: [{ payslip, lines }] }` |
| POST | `/payroll/runs/{id}/submit` | `{ reason }` | Run. Maker-checker: submitter cannot approve. |
| POST | `/payroll/runs/{id}/approve` | `{ reason }` | Run. Publishes payslips and notifies staff. |
| POST | `/payroll/runs/{id}/reject` | `{ reason }` required | Run |
| POST | `/payroll/runs/{id}/lock` | `{ reason }` | Run. This locks the payroll run, not attendance. |
| GET | `/payroll/runs/{id}/register` |  | Salary register rows |
| GET | `/payroll/runs/{id}/pf-challan` |  | PF challan rows |
| GET | `/payroll/runs/{id}/esi-challan` |  | ESI challan rows |
| GET | `/payroll/runs/{id}/pt-report` |  | Professional tax CSV for the run |
| GET | `/payroll/runs/{id}/tds-24q` |  | Salary TDS CSV for the run (section 192, the salary part of Form 24Q) |
| GET | `/payroll/runs/{id}/bank-file` |  | CSV attachment |
| PATCH | `/payroll/payslips/{id}/payment` | `{ status: PENDING\|EXPORTED\|PAID\|FAILED, utr, failure_reason, reason }` | Payment. `PAID` sets the payslip paid date. |
| GET | `/payroll/payslips/me?year` |  | Own published or paid payslips |
| GET | `/payroll/payslips/{id}/pdf?intent=view\|download` |  | PDF. `intent=view` increments `view_count` for the owner. `download` increments `download_count`. |
| GET | `/payroll/notifications?limit` |  | The caller's payslip notifications for this school only: `{ id, title, body, category, link_path, is_read, created_at, payslip_id }` |
| GET | `/payroll/form16/me?financial_year` |  | Form 16 data, `YYYY-YYYY` |
| GET | `/payroll/form16/me/pdf?financial_year` |  | Form 16 PDF |
| GET | `/payroll/form16/{employee_id}?financial_year` |  | Same data for one employee |
| GET | `/payroll/form16/{employee_id}/pdf?financial_year` |  | Form 16 PDF for one employee |
| POST | `/payroll/settlements` | `{ employee_id, last_working_day, leave_encashment_days, notice_recovery, reason }` | Full and final settlement |

### Staff self-attendance

Existing `/hr/attendance` daily, history, summary, mark, bulk, patch, and delete routes are unchanged. Shift and geofence live on payroll settings.

| Method | Path | Request | Response |
| --- | --- | --- | --- |
| POST | `/hr/attendance/check-in` | `{ lat, lng, manual, reason }` | `{ session, already_open }` |
| POST | `/hr/attendance/check-out` | `{ lat, lng }` | Session. Writes the daily attendance mark. |
| POST | `/hr/attendance/manual` | `{ employee_id, check_in_at, check_out_at, reason, lat, lng }` | Session |
| GET | `/hr/attendance/sessions/me?date_from&date_to` |  | Own sessions |
| GET | `/hr/attendance/sessions?date_from&date_to&employee_id` |  | School sessions |
| POST | `/hr/attendance/sessions/{id}/correct` | `{ check_in_at, check_out_at, reason }` | Session |
| POST | `/hr/attendance/regularisations` | `{ check_in_at, check_out_at, reason }` | Request |
| GET | `/hr/attendance/regularisations/me` |  | Own requests |
| GET | `/hr/attendance/regularisations` |  | School requests |
| POST | `/hr/attendance/regularisations/{id}/decide` | `{ approve, note }` | Request |
| POST | `/hr/attendance/periods/lock` | `{ period_start, period_end, reason }` | `{ lock_id, run_id, created, skipped }`. Requires a mark on every working day. Enqueues `payroll_generate` and also generates in the request so a stopped worker still produces the run. |

### Internal

`POST /internal/v1/payroll/generate` with `X-Internal-Token`. Body `{ school_id, period_start, period_end, locked_by, source }`. Response `{ run_id, created, skipped }`. The worker job type is `payroll_generate`.

`GET /api/v1/schools/me/branches` returns `{ branches: [{ name }] }` for the signed-in school. Names come from weekly offs, holidays, and statutory profiles. Empty branch names are the whole school and are left out.

The HRM login portal (`portal=hrm`) accepts school admin, HR manager, and payroll approver.

## Daily payroll

List routes accept `q`, `limit` (1–500), and `offset`. The JSON stays a list. `X-Total-Count` is the count after search, before the page slice. Omit `limit` to return every matching row from `offset`.

Generating a period is idempotent: a second generate for the same dates returns the open run and `created: false`. Rejected and cancelled runs do not block a new generate.

| Method | Path | Request | Response |
| --- | --- | --- | --- |
| POST | `/payroll/runs/{id}/recalculate` | `{ reason }` | Rebuilds a draft or rejected run. Restores arrears, claims, and loan EMI, then calculates again. Sets the run back to draft. |
| POST | `/payroll/runs/{id}/cancel` | `{ reason }` required | Cancels a draft, rejected, or submitted run. Payslips become `CANCELLED` and inputs are restored. |
| POST | `/payroll/runs/{id}/lock` | `{ reason }` | Only when the run is `PAID`. A locked run rejects payment changes, bank responses, and retries. |
| GET | `/payroll/runs/{id}/payslips.zip` |  | Zip of payslip PDFs for the run. |
| GET | `/payroll/runs/{id}/pf-ecr` |  | EPFO ECR text, fields separated by `#~#`. |
| GET | `/payroll/runs/{id}/register.csv` |  | Salary register CSV |
| GET | `/payroll/runs/{id}/register.xlsx` |  | Salary register workbook |
| POST | `/payroll/runs/{id}/bank-response` | multipart CSV `account_number,status,utr,reason` | `{ updated, errors: [{ row, field, message }] }`. `SUCCESS` counts as paid. A paid row needs a UTR. |
| POST | `/payroll/runs/{id}/payments/retry` | `{ reason }` required | `{ retried }`. Only `FAILED` payments return to `PENDING`. |
| POST | `/payroll/salary-revisions` | salary assignment body plus `compute_arrears` (default true) | Closes the open assignment and, when the new pay is higher and the date is in a past month, creates arrears for the completed months. |
| GET | `/payroll/loans/{id}/schedule` |  | `{ loan_id, balance, emi, annual_rate, status, schedule }` |
| POST | `/payroll/loans/{id}/prepay` | `{ amount, reason }` | Reduces the balance. A prepayment that clears the balance closes the loan. |
| POST | `/payroll/loans/{id}/close` | `{ reason }` | Sets the balance to zero and status `CLOSED`. |
| POST | `/payroll/imports/employees` | multipart CSV `employee_code,full_name,email,phone,pan` | `{ created, updated, errors }`. New rows are non-teaching staff. Invalid rows are skipped and listed. |
| POST | `/payroll/imports/banks` | multipart CSV `employee_code,account_holder,account_number,ifsc,bank_name` | `{ updated, errors }` |
| GET | `/payroll/exports/{kind}?file_format=csv\|xlsx` | `kind` is `loans`, `arrears`, or `reimbursements` | File download |

Each payslip stores `lop_detail`: `[{ date, status, source, lop_days }]`. `source` is `UNMARKED_WORKING_DAY`, `ABSENT`, `HALF_DAY`, or `UNPAID_LEAVE`.

`payroll_loan_events` records EMI, prepayment, closure, and reversal. Migration `0041` forces row level security on that table, same as the other payroll tables. Tables outside payroll are unchanged: the rest of the API does not set `app.school_id` on every request, so forcing RLS there would block those routes.

Payslip template codes remain `CLASSIC`, `COMPACT`, and `STATUTORY` on payroll settings.
