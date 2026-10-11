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

The workflow does not read GitHub Actions secrets. Application secrets stay in AWS (Secrets Manager or SSM) and are injected by the ECS task definitions. Do not put database passwords, `JWT_SECRET`, or queue URLs in the workflow file.

The OIDC role needs at least:

- `ecr:GetAuthorizationToken`
- push to ECR repositories `sybeez-school-api` and `sybeez-school-worker`
- `ecs:DescribeTaskDefinition`, `ecs:RegisterTaskDefinition` for `sybeez-school-migrate`, `sybeez-school-api`, and `sybeez-school-worker`
- `ecs:RunTask` and `ecs:DescribeTasks` on cluster `sybeez-school-cluster`
- `ecs:UpdateService` and `ecs:DescribeServices` for services `sybeez-school-api` and `sybeez-school-worker`
- `iam:PassRole` on the task execution role and the task role

## What a push to `release` does

1. Lint and tests, with Postgres 16 + pgvector and Redis.
2. Assume `AWS_BACKEND_ROLE_ARN` in `ap-south-1`.
3. Build `infra/docker/api.Dockerfile` and `apps/worker/Dockerfile`. Push both to ECR tagged with the git SHA and `latest`.
4. Render task definition `sybeez-school-migrate` with the new API image, run it once on `sybeez-school-cluster`, and wait until it stops. The deploy fails if the container exit code is not 0. The task definition's command must be `alembic upgrade head` (working directory `/app`).
5. Render new task definitions for services `sybeez-school-api` and `sybeez-school-worker` with those images, update the services, and wait until they are stable.

Images are non-root. The API process is `uvicorn` with `--proxy-headers`. Both images health-check `GET /health`. That endpoint returns HTTP 200 with `"status": "degraded"` when Redis is down. It does not fail the container for a temporary Redis outage.

## Runtime configuration

Put these on the ECS task definitions, not in git.

| Name | Notes |
| --- | --- |
| `DATABASE_URL` | `postgresql+asyncpg://...` for the API. Production should use `sybeez_app` (`NOBYPASSRLS`) and `DATABASE_SSL_MODE=require`, or `DATABASE_IAM_AUTH=true` with `DATABASE_HOST`, `DATABASE_USER`, and `AWS_REGION`. |
| `REDIS_URL` | `redis://` or `rediss://` (TLS). `rediss://` verifies the server certificate. |
| `JOBS_BACKEND` | `redis` (default) or `sqs`. API enqueue and the worker both read this. |
| `SQS_QUEUE_URL` | Required when `JOBS_BACKEND=sqs`. Same queue for enqueue and the worker. |
| `AWS_REGION` | Region for the SQS client and RDS IAM auth. Production cluster is `ap-south-1`. |
| `JWT_SECRET`, `AI_SERVICE_TOKEN`, `CORE_INTERNAL_TOKEN` | Required in production, at least 32 characters, not a placeholder. |
| `S3_BUCKET` | Required in production. The app stores object keys only. |
| `APP_ENV` | `production` turns on the checks above. |

`JOBS_BACKEND` defaults to `redis`, so a task definition that omits it keeps the current Redis list `sybeez:jobs`.
