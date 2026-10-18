# Sybeez School – 15-minute idle shutdown (full stop / auto wake)

When nobody has used the platform for **15 minutes**, everything stoppable is stopped; the next visit/login wakes it all.
Account 213433448375, region ap-south-1. Deployed with `./deploy.sh` (AWS CLI, idempotent, never deletes anything).

## What happens
* **Checker** – Lambda `sybeez-school-idle-controller`, run by EventBridge Scheduler `sybeez-school-idle-check` **every 5 min**.
* **Idle** = zero user API requests (`/api/v1/` lines in the app logs of the api + ai ECS services, which excludes `/health` and the worker's periodic `/internal/v1/*` calls) in the last 15 minutes and no jobs in SQS `sybeez-school-jobs` (visible/in-flight/delayed). ALB `RequestCount` and CloudFront requests are logged for information only (the worker polls `/internal/v1/*` through the ALB every ~30 s, so ALB counts never reach 0).
* **Never stops when**: a deploy is in progress (more than one ECS deployment / IN_PROGRESS rollout / running != desired), a one-off ECS task (migration/run-task) is running, within 30 min after a start (cool-down), RDS is not `available`, or the RDS-managed secret rotation is due (90 min before until 6 h after `NextRotationDate`; Oct 25 rotation: a stopped DB is woken 45 min before it).
* **Stop (idempotent, resumable)**: install ALB wake-page rule → record desired counts in SSM `/sybeez-school/idle-shutdown/original-counts` → ECS api/worker/ai desired=0 → wait drained → stop RDS `sybeez-school-db`. State in `/sybeez-school/idle-shutdown/state` (RUNNING/STOPPING/STOPPED/WAKING) is always reconciled with real RDS/ECS state; an interrupted stop is finished by the next pass.
* **Wake (idempotent, resumable)**: with the platform down, ALB listener rule priority 1 (`/*`) forwards to Lambda `sybeez-school-idle-wake-page`, which returns 503 + a friendly auto-refreshing "Sybeez is waking up (about 7–12 minutes)" page (JSON 503 for API/XHR calls) and triggers the controller: start RDS → wait available → restore ECS counts from SSM → wait for a healthy API target → delete the wake rule (the only thing this system ever deletes; it created it). Only requests from `*.sybeez.ai` origins/referers, with Authorization, or browser navigations wake it; `/health` and anonymous bots do not.
* RDS auto-restarts after 7 days stopped: a pass in state STOPPED that sees RDS/ECS up again re-stops it.
* Not stoppable (left running): ElastiCache `sybeez-school-cache-001` (Valkey), NAT gateways, ALB, CloudFront/S3.
* Logs: `/aws/lambda/sybeez-school-idle-controller` (JSON lines). Metrics: namespace `SybeezSchool/IdleShutdown` (ActivityRequests15m, Idle, StopBlocked, StopCompleted, WakeCompleted, DryRunWouldStop). SNS topic `sybeez-school-idle-shutdown` receives every stop/start (add subscribers if wanted).

## Controls (SSM Parameter Store, region ap-south-1)
| Parameter | Meaning |
|---|---|
| `/sybeez-school/idle-shutdown/enabled` | `true` (default) / `false` = **kill switch** |
| `/sybeez-school/idle-shutdown/dry-run` | `true` = only log "DRY_RUN_would_stop"; `false` = act |

## How to disable
```
aws ssm put-parameter --region ap-south-1 --name /sybeez-school/idle-shutdown/enabled --value false --type String --overwrite
```
With `enabled=false` the controller never stops anything, and if the platform is currently stopped it wakes it (RDS then ECS) and leaves it always-on. Hard off: `aws scheduler update-schedule` to DISABLED for `sybeez-school-idle-check` (then wake manually: start RDS, restore counts from `original-counts`, delete ALB rule priority 1 if present).
Manual wake any time: `aws lambda invoke --function-name sybeez-school-idle-controller --payload '{"action":"wake"}' --cli-binary-format raw-in-base64-out /dev/stdout`

## Caveats
* The frontend is static (S3/CloudFront) and stays up; while down its API calls get the 503 wake response (apps should show a retry state). The HTML wake page is shown for direct browser navigations to the API/ALB host.
* CI deploys while stopped will fail health checks; wake first (invoke the wake action) before deploying.
* Existing disabled schedules `sybeez-school-db-start/-stop` are untouched.
