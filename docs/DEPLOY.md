# Backend production deploy

Pushes to `release` build the API and worker, run database migrations, and roll the ECS services. Pull requests lint and test. Do not push this branch to `Production` or `main`.

Region is `ap-south-1`. AWS credentials come from GitHub OIDC. There is no long-lived AWS access key in GitHub.

## GitHub Actions variables

Set these on the repository (Settings → Secrets and variables → Actions → Variables). The deploy job reads them with `vars.*`.

| Variable | Required | Purpose |
| --- | --- | --- |
| `AWS_BACKEND_ROLE_ARN` | yes | IAM role ARN assumed via OIDC. Trust this repo's `release` branch (and the workflow's `sts:AssumeRoleWithWebIdentity`). |
| `AWS_BACKEND_SUBNETS` | yes | Comma-separated subnet IDs for the one-off migration task, for example `subnet-aaa,subnet-bbb`. |
| `AWS_BACKEND_SECURITY_GROUP` | yes | Security group ID for that task. It must reach RDS and, when `JOBS_BACKEND=sqs`, the SQS endpoint. |
| `AWS_BACKEND_ASSIGN_PUBLIC_IP` | no | `ENABLED` or `DISABLED` (default `DISABLED`) on the migration task network configuration. |
| `AWS_BACKEND_MIGRATE_CONTAINER` | no | Container name inside task definition `sybeez-school-migrate`. Default `sybeez-school-migrate`. |
| `AWS_BACKEND_API_CONTAINER` | no | Container name inside the API service task definition. Default `sybeez-school-api`. |
| `AWS_BACKEND_WORKER_CONTAINER` | no | Container name inside the worker service task definition. Default `sybeez-school-worker`. |

## GitHub Actions secrets

Application secrets stay in AWS (Secrets Manager or SSM) and are injected by the ECS task definitions. Do not put database passwords, `JWT_SECRET`, or queue URLs in the workflow file.

`SUPERADMIN_INITIAL_PASSWORD` is the one GitHub Actions secret this workflow reads. The provision step passes it into a one-off API task and does not print it. When that secret is empty, the task reads Secrets Manager secret `sybeez-school/superadmin` instead.

The OIDC role needs at least:

- `ecr:GetAuthorizationToken`
- push to ECR repositories `sybeez-school-api` and `sybeez-school-worker`
- `ecs:DescribeTaskDefinition`, `ecs:RegisterTaskDefinition` for `sybeez-school-migrate`, `sybeez-school-api`, and `sybeez-school-worker`
- `ecs:RunTask` and `ecs:DescribeTasks` on cluster `sybeez-school-cluster`
- `ecs:UpdateService` and `ecs:DescribeServices` for services `sybeez-school-api` and `sybeez-school-worker`
- `iam:PassRole` on the task execution role and the task role

## What a push to `release` does

1. Lint, then `alembic upgrade head` on the empty Postgres 16 + pgvector service database, then tests. Redis 7 is up for the same job.
2. Assume `AWS_BACKEND_ROLE_ARN` in `ap-south-1`.
3. Build `infra/docker/api.Dockerfile` and `apps/worker/Dockerfile`. Push both to ECR tagged with the git SHA and `latest`.
4. Render task definition `sybeez-school-migrate` with the new API image, run it once on `sybeez-school-cluster`, and wait until it stops. The deploy fails if the container exit code is not 0. The workflow overrides the container command to `alembic upgrade head` (working directory `/app`). Before any other DDL, that command restamps a legacy finance database (see below) or exits non-zero if the stamp is ambiguous.
5. Render new task definitions for services `sybeez-school-api` and `sybeez-school-worker` from the latest family revisions, not from the revision the service rolled back to. `DATABASE_URL` and `DATABASE_URL_SYNC` on those two tasks are pinned to secret `sybeez-school/api-db`. The migrate task keeps its master secret. Container names come from `AWS_BACKEND_API_CONTAINER` and `AWS_BACKEND_WORKER_CONTAINER` (`api` and `worker`). Update the services, then raise the API desired count to at least 2 and the worker to at least 1 if either is lower. Wait until each service is stable. If it is not, the job prints the service events, the stopped task reason, and the container log.
6. Run `python -m src.modules.platform.provision_superadmin` once on the new API task definition. That sets `admin@sybeez.ai` as `SUPER_ADMIN` (local hash, Cognito sub cleared, and a permanent Cognito password when a pool is configured) and disables `admin@cybis.ai` in the database and the user pool. The password is not written to the logs. The deploy fails if that task exits non-zero. Startup bootstrap still does not replace an existing password.

Images are non-root. The API process is `uvicorn` with `--proxy-headers`. Both images health-check `GET /health`. `GET /` returns the same liveness payload, because the load balancer often checks `/`. That endpoint returns HTTP 200 with `"status": "degraded"` when Redis is down. It does not fail the container for a temporary Redis outage. Super-admin bootstrap runs after the process is listening; a database or Cognito error is logged and does not stop the process. If a service never reaches its minimum running count, the job prints recent events from that container's CloudWatch log group without listing task ARNs.

## Runtime configuration

Put these on the ECS task definitions, not in git.

| Name | Notes |
| --- | --- |
| `DATABASE_URL` | `postgresql+asyncpg://...`. On the API and worker this is `sybeez_app` with no usable password when `DB_IAM_AUTH=true`. On the migrate task this is the **master** user and must include the password. |
| `DB_IAM_AUTH` | `true` on the API and worker only. Also accepted as `DATABASE_IAM_AUTH`. Leave it unset on `sybeez-school-migrate`. |
| `DATABASE_HOST`, `DATABASE_PORT`, `DATABASE_USER`, `DATABASE_NAME` | Optional overrides. When empty, host, port, user, and database are parsed from `DATABASE_URL`. IAM connects as `sybeez_app`. |
| `DATABASE_SSL_MODE` | `require` when not using IAM. IAM always uses SSL, even if this is unset. |
| `AWS_REGION` | Region for the RDS auth token, SQS, and RDS IAM. Production is `ap-south-1`. |
| `REDIS_URL` | `redis://` or `rediss://` (TLS). `rediss://` verifies the server certificate. |
| `JOBS_BACKEND` | `redis` (default) or `sqs`. API enqueue and the worker both read this. |
| `SQS_QUEUE_URL` | Required when `JOBS_BACKEND=sqs`. Same queue for enqueue and the worker. |
| `JWT_SECRET`, `AI_SERVICE_TOKEN`, `CORE_INTERNAL_TOKEN` | Required in production, at least 32 characters, not a placeholder. |
| `AUTH_MODE` | `local` or `both` starts with the school JWT when Cognito is unset. A user pool and app client are required when `AUTH_MODE=cognito` or when either id is already set. `COGNITO_DOMAIN` is only the Hosted UI host, so a missing domain does not stop the process. Google sign-in returns a conflict until it is set. |
| `S3_BUCKET` | Required in production. The app stores object keys only. |
| `APP_ENV` | `production` turns on the checks above. |

`JOBS_BACKEND` defaults to `redis`, so a task definition that omits it keeps the current Redis list `sybeez:jobs`.

## Production database already at finance revision 0030

RDS `sybeez-school-db` was built from the finance branch (PR #1). Its `alembic_version` is `0030`, and that id is the **old finance** revision:

| Old id on that database | File that actually ran | New id on `release` |
| --- | --- | --- |
| `0028` | `finance_production` | `0041` |
| `0029` | `platform_rls` | `0042` |
| `0030` | `ai_shared_columns` | `0043` |

On `release`, `0028`–`0030` are HRM (`hr_integrity`, `hr_employees`, `hr_departments_designations`) and `0031`–`0040` are the rest of HRM and payroll. A plain reading of stamp `0030` would skip those and treat finance as already current. That is the wrong schema.

`alembic upgrade head` handles this before it reads the version for migration. The migrate task's command is that upgrade. It uses the master `DATABASE_URL` (a password). It does not use `DB_IAM_AUTH`.

1. Read `alembic_version`. Anything other than `0028`, `0029`, or `0030` is unchanged.
2. Classify that stamp from the schema, not from the number alone:

| Stamp | HRM object (leave the stamp) | Finance object (restamp) |
| --- | --- | --- |
| `0028` | constraint `uq_teachers_id_school_id` | table `finance_staff_scopes`, or column `fee_categories.is_80c_eligible` |
| `0029` | table `employees` | function `app.can_see` |
| `0030` | table `departments` | `app.can_see` or `finance_staff_scopes`, and no `departments` |

3. Finance match: in one transaction, `UPDATE alembic_version SET version_num = '0027' WHERE version_num = '<old>'`. `0027` is the last revision both histories share. The log line is `Detected legacy finance revision 0030` and `Restamped alembic_version to 0027`. The same process then applies HRM `0028` through payroll `0040` (not on this database yet) and re-applies `0041`–`0043`. Those finance revisions are idempotent (`IF NOT EXISTS`, `CREATE OR REPLACE`, `DROP POLICY IF EXISTS`, duplicate-object handlers). It then applies `0044`–`0046` (payroll daily, assistant, and import) and `0047`–`0050` (platform access, additive RLS helpers, staff sessions, and `app.can_see` role aliases), then `0053` (WhatsApp template alerts, school settings, opt-out, and delivery columns). `0051` and `0052` are unused and reserved for the hrm-integration branch. `0048` does not replace payroll or finance policies. `0050` replaces `app.can_see` only.
4. HRM match: the stamp stays, and upgrade continues at `0031`.
5. Neither marker, or both: the process raises `Refusing to upgrade`, does not change `alembic_version`, and exits non-zero. The workflow waits for the task to stop and fails the deploy when the container exit code is not 0. Fix the schema by hand before rerunning. Do not stamp past the HRM revisions to hide the mismatch.

Check production before the first release deploy:

```sql
SELECT version_num FROM alembic_version;
SELECT to_regclass('public.departments') AS hrm_departments;
SELECT to_regclass('public.finance_staff_scopes') AS finance_scopes;
SELECT 1 FROM pg_proc p
  JOIN pg_namespace n ON n.oid = p.pronamespace
  WHERE n.nspname = 'app' AND p.proname = 'can_see';
```

A finance `0030` has no `departments` and has `finance_staff_scopes` or `app.can_see`. An HRM `0030` has `departments` and does not have those finance objects yet.

Manual equivalent, only after that check matches the finance row. Do not run this on an HRM database:

```sql
UPDATE alembic_version SET version_num = '0027' WHERE version_num = '0030';
```

```bash
alembic upgrade head
```

The migrate task already does both. A second run sees `0053` and does not restamp.

## IAM database auth

`sybeez_app` has the `rds_iam` role, so Postgres rejects a password for that user. The API and the worker set `DB_IAM_AUTH=true`. Each new connection uses `boto3` `generate_db_auth_token` (region, host, port, user). The token lasts 15 minutes. The process mints a new one after 10 minutes, the SQLAlchemy `do_connect` event puts it on the connection, and the pool recycles connections after 10 minutes. SSL is required.

The migrate task is different. Its `DATABASE_URL` is the master user, including the password, and `DB_IAM_AUTH` is unset. If IAM is on and the URL has no password, `alembic upgrade head` exits before it changes the database. Alembic loads settings without the API's Cognito, S3, and JWT checks, and in production it connects with SSL even when `DATABASE_SSL_MODE` is unset on that task.
