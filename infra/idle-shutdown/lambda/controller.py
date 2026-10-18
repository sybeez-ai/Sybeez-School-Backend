"""Sybeez School idle-shutdown controller.

Single idempotent state machine. State lives in SSM (/sybeez-school/idle-shutdown/state)
but is always reconciled against the real RDS / ECS state, so an interrupted stop or an
interrupted wake is finished on the next pass (every 5 minutes, or immediately on wake).

States: RUNNING -> STOPPING -> STOPPED -> WAKING -> RUNNING.   Nothing is ever deleted
except the temporary ALB "wake page" listener rule that this system itself creates.
"""
import json, os, time, datetime, logging
import boto3
from botocore.exceptions import ClientError

log = logging.getLogger(); log.setLevel(logging.INFO)

REGION = os.environ.get("AWS_REGION", "ap-south-1")
P = os.environ.get("PARAM_PREFIX", "/sybeez-school/idle-shutdown")
CLUSTER = os.environ.get("ECS_CLUSTER", "sybeez-school-cluster")
SERVICES = os.environ.get("ECS_SERVICES", "sybeez-school-api,sybeez-school-worker,sybeez-school-ai").split(",")
DB_ID = os.environ.get("DB_INSTANCE", "sybeez-school-db")
LISTENER_ARN = os.environ["LISTENER_ARN"]
WAKE_TG_ARN = os.environ["WAKE_TG_ARN"]
LB_DIM = os.environ["LB_DIM"]                      # app/sybeez-school-alb/974f...
TG_DIMS = json.loads(os.environ["TG_DIMS"])        # ["targetgroup/sybeez-school-api-tg/..", ...]
CF_DIST_IDS = [x for x in os.environ.get("CF_DIST_IDS", "").split(",") if x]
QUEUE_URL = os.environ.get("QUEUE_URL", "")
TOPIC_ARN = os.environ.get("TOPIC_ARN", "")
SECRET_IDS = [x for x in os.environ.get("ROTATION_SECRETS", "").split(",") if x]
WAKE_RULE_PRIORITY = int(os.environ.get("WAKE_RULE_PRIORITY", "1"))
IDLE_MINUTES = int(os.environ.get("IDLE_MINUTES", "15"))
COOLDOWN_MIN = int(os.environ.get("COOLDOWN_MINUTES", "30"))
METRIC_NS = "SybeezSchool/IdleShutdown"

ssm = boto3.client("ssm", region_name=REGION)
ecs = boto3.client("ecs", region_name=REGION)
rds = boto3.client("rds", region_name=REGION)
elb = boto3.client("elbv2", region_name=REGION)
cw = boto3.client("cloudwatch", region_name=REGION)
cw_use1 = boto3.client("cloudwatch", region_name="us-east-1")
sqs = boto3.client("sqs", region_name=REGION)
sns = boto3.client("sns", region_name=REGION)
sm = boto3.client("secretsmanager", region_name=REGION)


def now(): return datetime.datetime.now(datetime.timezone.utc)
def jlog(event, **kw):
    log.info(json.dumps({"event": event, **kw}, default=str))

def get_param(name, default=None):
    try: return ssm.get_parameter(Name=f"{P}/{name}")["Parameter"]["Value"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "ParameterNotFound": return default
        raise
def put_param(name, value):
    ssm.put_parameter(Name=f"{P}/{name}", Value=str(value), Type="String", Overwrite=True)

def metric(name, value, unit="Count"):
    try:
        cw.put_metric_data(Namespace=METRIC_NS, MetricData=[{"MetricName": name, "Value": value, "Unit": unit}])
    except Exception as e: log.warning("metric failed %s", e)

def notify(subject, msg):
    jlog("notify", subject=subject, message=msg)
    if TOPIC_ARN:
        try: sns.publish(TopicArn=TOPIC_ARN, Subject=subject[:100], Message=msg)
        except Exception as e: log.warning("sns failed %s", e)

def enabled(): return get_param("enabled", "true").strip().lower() not in ("false", "0", "no", "off")
def dry_run(): return get_param("dry-run", "true").strip().lower() not in ("false", "0", "no", "off")

# ---------------------------------------------------------------- observation
def rds_status():
    return rds.describe_db_instances(DBInstanceIdentifier=DB_ID)["DBInstances"][0]["DBInstanceStatus"]

def ecs_state():
    out = {}
    for i in range(0, len(SERVICES), 10):
        for s in ecs.describe_services(cluster=CLUSTER, services=SERVICES[i:i+10])["services"]:
            out[s["serviceName"]] = s
    return out

def deploy_in_progress(svcs):
    reasons = []
    for n, s in svcs.items():
        deps = s.get("deployments", [])
        if len(deps) > 1 or any(d.get("rolloutState") == "IN_PROGRESS" for d in deps):
            reasons.append(f"{n}: deployment in progress")
        elif s["runningCount"] != s["desiredCount"] or s.get("pendingCount", 0):
            reasons.append(f"{n}: running {s['runningCount']} != desired {s['desiredCount']}")
    # one-off tasks (migrations, run-task): task group not "service:*"
    arns = []
    for page in ecs.get_paginator("list_tasks").paginate(cluster=CLUSTER): arns += page["taskArns"]
    for i in range(0, len(arns), 90):
        for t in ecs.describe_tasks(cluster=CLUSTER, tasks=arns[i:i+90])["tasks"]:
            if not t.get("group", "").startswith("service:"):
                reasons.append(f"one-off task running: {t['taskArn'].split('/')[-1]} group={t.get('group')}")
    return reasons

def _sum_metric(client, namespace, name, dims, start, end, stat="Sum"):
    r = client.get_metric_statistics(Namespace=namespace, MetricName=name, Dimensions=dims,
                                     StartTime=start, EndTime=end, Period=60, Statistics=[stat])
    return sum(p[stat] for p in r["Datapoints"])

def activity(window_min):
    """Real user activity over the window. ALB RequestCount counts only requests that went
    through the load balancer to targets (target health checks are NOT counted)."""
    end = now().replace(second=0, microsecond=0) - datetime.timedelta(minutes=1)
    start = end - datetime.timedelta(minutes=window_min)
    detail = {}
    for tg in TG_DIMS:
        detail["alb:" + tg.split("/")[1]] = _sum_metric(cw, "AWS/ApplicationELB", "RequestCount",
            [{"Name": "LoadBalancer", "Value": LB_DIM}, {"Name": "TargetGroup", "Value": tg}], start, end)
    for d in CF_DIST_IDS:
        detail["cloudfront:" + d] = _sum_metric(cw_use1, "AWS/CloudFront", "Requests",
            [{"Name": "DistributionId", "Value": d}, {"Name": "Region", "Value": "Global"}], start, end)
    if QUEUE_URL:
        a = sqs.get_queue_attributes(QueueUrl=QUEUE_URL, AttributeNames=["ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible", "ApproximateNumberOfMessagesDelayed"])["Attributes"]
        detail["sqs_pending_jobs"] = sum(int(v) for v in a.values())
    return sum(detail.values()), detail, start, end

def rotation_window():
    """Returns (block_stop, need_wake, why) for Secrets Manager rotation that needs the DB up."""
    t = now(); block = need = False; why = ""
    for sid in SECRET_IDS:
        try: s = sm.describe_secret(SecretId=sid)
        except ClientError: continue
        nxt = s.get("NextRotationDate")
        if not s.get("RotationEnabled") or not nxt: continue
        if nxt - datetime.timedelta(minutes=90) <= t <= nxt + datetime.timedelta(hours=6):
            block = True; why = f"rotation of {sid} due {nxt.isoformat()}"
        if nxt - datetime.timedelta(minutes=45) <= t <= nxt + datetime.timedelta(hours=6):
            need = True
    return block, need, why

# ---------------------------------------------------------------- wake-page rule
def wake_rule_arn():
    for r in elb.describe_rules(ListenerArn=LISTENER_ARN)["Rules"]:
        if r["Priority"] == str(WAKE_RULE_PRIORITY):
            return r["RuleArn"] if any(a.get("TargetGroupArn") == WAKE_TG_ARN for a in r["Actions"]) else "CONFLICT"
    return None

def ensure_wake_rule():
    a = wake_rule_arn()
    if a == "CONFLICT": raise RuntimeError("priority taken by a non-wake rule; refusing to touch it")
    if a: return
    elb.create_rule(ListenerArn=LISTENER_ARN, Priority=WAKE_RULE_PRIORITY,
        Conditions=[{"Field": "path-pattern", "Values": ["/*"]}],
        Actions=[{"Type": "forward", "TargetGroupArn": WAKE_TG_ARN}],
        Tags=[{"Key": "managed-by", "Value": "sybeez-idle-shutdown"}])
    jlog("wake_rule_installed")

def remove_wake_rule():
    a = wake_rule_arn()
    if a and a != "CONFLICT":
        elb.delete_rule(RuleArn=a); jlog("wake_rule_removed")

# ---------------------------------------------------------------- actions
def original_counts():
    try: return json.loads(get_param("original-counts", "{}"))
    except Exception: return {}

def record_counts(svcs):
    """Record current desired counts; never overwrite recorded counts with zeros."""
    oc = original_counts()
    for n, s in svcs.items():
        if s["desiredCount"] > 0: oc[n] = s["desiredCount"]
    for n in SERVICES: oc.setdefault(n, 1)
    put_param("original-counts", json.dumps(oc)); return oc

def wait_until(cond, deadline, every=15):
    while time.time() < deadline:
        if cond(): return True
        time.sleep(every)
    return cond()

def advance_stop(deadline):
    """Idempotent: finish a stop from any half-state."""
    ensure_wake_rule()                      # visitors get the wake page before anything goes down
    svcs = ecs_state(); oc = record_counts(svcs)
    for n, s in svcs.items():
        if s["desiredCount"] != 0:
            ecs.update_service(cluster=CLUSTER, service=n, desiredCount=0); jlog("ecs_scaled_to_0", service=n, was=s["desiredCount"])
    if not wait_until(lambda: all(s["runningCount"] == 0 and s.get("pendingCount", 0) == 0 for s in ecs_state().values()), deadline - 60):
        jlog("stop_waiting_for_ecs_drain"); return False
    st = rds_status()
    if st == "available":
        rds.stop_db_instance(DBInstanceIdentifier=DB_ID); jlog("rds_stop_requested")
    elif st not in ("stopping", "stopped"):
        jlog("rds_not_stoppable_yet", status=st); return False
    if not wait_until(lambda: rds_status() == "stopped", deadline, 20):
        return False
    return True

def advance_wake(deadline):
    """Idempotent: bring everything fully up from any half-state, in order RDS -> ECS."""
    ensure_wake_rule()
    while time.time() < deadline:
        st = rds_status()
        if st == "stopped":
            try: rds.start_db_instance(DBInstanceIdentifier=DB_ID); jlog("rds_start_requested")
            except ClientError as e: jlog("rds_start_error", err=str(e))
        elif st == "available":
            break
        time.sleep(20)
    if rds_status() != "available": return False
    oc = original_counts(); svcs = ecs_state()
    for n, s in svcs.items():
        want = int(oc.get(n, 1))
        if s["desiredCount"] < want:
            ecs.update_service(cluster=CLUSTER, service=n, desiredCount=want); jlog("ecs_restored", service=n, desired=want)
    def healthy():
        ss = ecs_state()
        return all(ss[n]["runningCount"] >= min(1, int(oc.get(n, 1))) for n in ss)
    if not wait_until(healthy, deadline - 5): return False
    # API must be answering through the ALB target group before we drop the wake page.
    tg_api = [t for t in TG_DIMS if "api" in t][0]
    tg_arn = [t["TargetGroupArn"] for t in elb.describe_target_groups()["TargetGroups"] if t["TargetGroupName"] == tg_api.split("/")[1]][0]
    def api_healthy():
        h = elb.describe_target_health(TargetGroupArn=tg_arn)["TargetHealthDescriptions"]
        return any(x["TargetHealth"]["State"] == "healthy" for x in h)
    if not wait_until(api_healthy, deadline - 2): return False
    remove_wake_rule()
    return True

# ---------------------------------------------------------------- main pass
def handler(event, context):
    deadline = time.time() + context.get_remaining_time_in_millis() / 1000 - 45
    action = (event or {}).get("action", "check")
    state = get_param("state", "RUNNING")
    en, dry = enabled(), dry_run()
    jlog("pass_start", action=action, state=state, enabled=en, dry_run=dry)
    metric("StateRunning", 1 if state == "RUNNING" else 0)

    # ---- resume half-states first (always, even with dry-run / kill switch)
    if action == "wake" and state in ("STOPPED", "STOPPING", "RUNNING") and state != "RUNNING":
        state = "WAKING"
    if state == "STOPPED" or state == "STOPPING":
        block, need_wake, why = rotation_window()
        if not en or need_wake or action == "wake":
            reason = "kill switch off" if not en else ("secret rotation needs DB: " + why if need_wake else "wake requested")
            put_param("state", "WAKING"); state = "WAKING"
            notify("Sybeez School: waking", f"Starting platform ({reason}).")
        elif state == "STOPPING":
            if dry_run(): return {"dry_run": True}
            ok = advance_stop(deadline)
            if ok: put_param("state", "STOPPED"); notify("Sybeez School: stopped", "RDS and ECS fully stopped (resumed interrupted stop).")
            return {"state": "STOPPED" if ok else "STOPPING"}
        else:  # STOPPED: make sure it really is fully down (RDS auto-restarts after 7 days)
            st = rds_status(); svcs = ecs_state()
            if st in ("available", "starting") or any(s["desiredCount"] > 0 for s in svcs.values()):
                if dry_run(): jlog("DRY_RUN_would_restop"); return {"dry_run": True}
                put_param("state", "STOPPING"); ok = advance_stop(deadline)
                if ok: put_param("state", "STOPPED"); jlog("restopped_after_drift", rds=st)
                return {"state": "STOPPED" if ok else "STOPPING"}
            ensure_wake_rule(); return {"state": "STOPPED"}
    if state == "WAKING":
        ok = advance_wake(deadline)
        if ok:
            put_param("state", "RUNNING"); put_param("last-start-epoch", int(time.time()))
            metric("WakeCompleted", 1)
            notify("Sybeez School: running", "RDS and ECS fully running; wake page removed.")
            return {"state": "RUNNING"}
        return {"state": "WAKING"}

    # ---- state RUNNING: reconcile drift (RDS down / ECS at 0 unexpectedly => bring fully up)
    st = rds_status(); svcs = ecs_state()
    zero = [n for n, s in svcs.items() if s["desiredCount"] == 0]
    if st in ("stopped", "stopping") or zero:
        jlog("drift_detected", rds=st, zero_services=zero)
        if dry_run(): jlog("DRY_RUN_would_wake_for_drift"); return {"dry_run": True}
        put_param("state", "WAKING"); return handler({"action": "check"}, context)
    # ---- idle decision
    if not en: jlog("kill_switch_off_no_action"); return {"state": "RUNNING", "enabled": False}
    total, detail, start, end = activity(IDLE_MINUTES)
    idle = total == 0
    metric("ActivityRequests15m", total); metric("Idle", 1 if idle else 0)
    jlog("activity", total=total, detail=detail, window=[start.isoformat(), end.isoformat()], idle=idle)
    if not idle: return {"state": "RUNNING", "idle": False}
    reasons = deploy_in_progress(svcs)
    last = int(get_param("last-start-epoch", "0") or 0)
    if time.time() - last < COOLDOWN_MIN * 60: reasons.append(f"cool-down: started {int((time.time()-last)/60)} min ago (<{COOLDOWN_MIN})")
    blk, _, why = rotation_window()
    if blk: reasons.append(why)
    if st != "available": reasons.append(f"rds status {st}")
    if reasons:
        jlog("idle_but_blocked", reasons=reasons); metric("StopBlocked", 1); return {"state": "RUNNING", "blocked": reasons}
    if dry_run():
        jlog("DRY_RUN_would_stop", counts={n: s["desiredCount"] for n, s in svcs.items()}, rds=st)
        metric("DryRunWouldStop", 1); return {"dry_run": True, "would_stop": True}
    put_param("state", "STOPPING")
    notify("Sybeez School: stopping", f"No activity for {IDLE_MINUTES} min. Scaling ECS to 0 and stopping RDS {DB_ID}.")
    ok = advance_stop(deadline)
    if ok:
        put_param("state", "STOPPED"); metric("StopCompleted", 1)
        notify("Sybeez School: stopped", "RDS and ECS fully stopped.")
    return {"state": "STOPPED" if ok else "STOPPING"}
