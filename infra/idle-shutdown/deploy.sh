#!/usr/bin/env bash
# Deploy/update the Sybeez School idle-shutdown system (AWS CLI, idempotent). Account 213433448375, ap-south-1.
# Never deletes existing resources. Starts in DRY-RUN (dry-run=true) unless already set.
set -euo pipefail
export AWS_REGION=ap-south-1 AWS_PAGER=""
ACC=213433448375; HERE="$(cd "$(dirname "$0")" && pwd)"; PFX=/sybeez-school/idle-shutdown
CLUSTER=sybeez-school-cluster
LB_ARN=arn:aws:elasticloadbalancing:ap-south-1:$ACC:loadbalancer/app/sybeez-school-alb/974f6ba5cf3fd4a6
LISTENER=arn:aws:elasticloadbalancing:ap-south-1:$ACC:listener/app/sybeez-school-alb/974f6ba5cf3fd4a6/a98675c2edd1da64
API_TG=targetgroup/sybeez-school-api-tg/7a20e4f5afcec01c; AI_TG=targetgroup/sybeez-school-ai-tg/feaf650abcaba42d
QUEUE_URL=https://sqs.ap-south-1.amazonaws.com/$ACC/sybeez-school-jobs
CF_IDS=E33TU6EIQE3S89
SECRET_ID='rds!db-8bca51f6-7669-44d5-9c02-a1fda83be430'
CTRL=sybeez-school-idle-controller; WAKE=sybeez-school-idle-wake-page

TOPIC_ARN=$(aws sns create-topic --name sybeez-school-idle-shutdown --query TopicArn --output text)
TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
mkrole(){ aws iam get-role --role-name "$1" >/dev/null 2>&1 || aws iam create-role --role-name "$1" --assume-role-policy-document "$TRUST" >/dev/null
  aws iam attach-role-policy --role-name "$1" --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole; }
mkrole $CTRL-role; mkrole $WAKE-role
cat > /tmp/ctrl-policy.json <<POL
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":["ssm:GetParameter","ssm:PutParameter","ssm:DeleteParameter"],"Resource":"arn:aws:ssm:$AWS_REGION:$ACC:parameter$PFX/*"},
 {"Effect":"Allow","Action":["rds:DescribeDBInstances"],"Resource":"*"},
 {"Effect":"Allow","Action":["rds:StopDBInstance","rds:StartDBInstance"],"Resource":"arn:aws:rds:$AWS_REGION:$ACC:db:sybeez-school-db"},
 {"Effect":"Allow","Action":["ecs:DescribeServices","ecs:ListTasks","ecs:DescribeTasks"],"Resource":"*"},
 {"Effect":"Allow","Action":"ecs:UpdateService","Resource":"arn:aws:ecs:$AWS_REGION:$ACC:service/$CLUSTER/sybeez-school-*"},
 {"Effect":"Allow","Action":["elasticloadbalancing:DescribeRules","elasticloadbalancing:DescribeTargetGroups","elasticloadbalancing:DescribeTargetHealth","elasticloadbalancing:CreateRule","elasticloadbalancing:DeleteRule","elasticloadbalancing:AddTags"],"Resource":"*"},
 {"Effect":"Allow","Action":"logs:FilterLogEvents","Resource":["arn:aws:logs:$AWS_REGION:$ACC:log-group:/ecs/sybeez-school-api:*","arn:aws:logs:$AWS_REGION:$ACC:log-group:/ecs/sybeez-school-ai:*"]},
 {"Effect":"Allow","Action":["cloudwatch:GetMetricStatistics","cloudwatch:PutMetricData"],"Resource":"*"},
 {"Effect":"Allow","Action":"sqs:GetQueueAttributes","Resource":"arn:aws:sqs:$AWS_REGION:$ACC:sybeez-school-jobs"},
 {"Effect":"Allow","Action":"sns:Publish","Resource":"$TOPIC_ARN"},
 {"Effect":"Allow","Action":"secretsmanager:DescribeSecret","Resource":"*"}]}
POL
aws iam put-role-policy --role-name $CTRL-role --policy-name idle-controller --policy-document file:///tmp/ctrl-policy.json
cat > /tmp/wake-policy.json <<POL
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":["ssm:GetParameter","ssm:PutParameter"],"Resource":"arn:aws:ssm:$AWS_REGION:$ACC:parameter$PFX/state"},
 {"Effect":"Allow","Action":"lambda:InvokeFunction","Resource":"arn:aws:lambda:$AWS_REGION:$ACC:function:$CTRL"}]}
POL
aws iam put-role-policy --role-name $WAKE-role --policy-name idle-wake --policy-document file:///tmp/wake-policy.json

# SSM parameters (create only if missing; never overwrite operator choices)
mkparam(){ aws ssm get-parameter --name "$PFX/$1" >/dev/null 2>&1 || aws ssm put-parameter --name "$PFX/$1" --value "$2" --type String >/dev/null; }
mkparam enabled true; mkparam dry-run true; mkparam state RUNNING; mkparam last-start-epoch 0

(cd "$HERE/lambda" && zip -q -j /tmp/controller.zip controller.py && zip -q -j /tmp/wake.zip wake_page.py)
sleep 10  # IAM propagation on first create
# Wake-page lambda target group first
WAKE_ARN=$(aws lambda get-function --function-name $WAKE --query Configuration.FunctionArn --output text 2>/dev/null || true)
CTRL_ENV_FILE=/tmp/ctrl-env.json
VPC=vpc-05e991edca68801ca
if [ -z "$WAKE_ARN" ] || [ "$WAKE_ARN" = None ]; then
  WAKE_ARN=$(aws lambda create-function --function-name $WAKE --runtime python3.12 --handler wake_page.handler --timeout 10 --memory-size 128 \
    --role arn:aws:iam::$ACC:role/$WAKE-role --zip-file fileb:///tmp/wake.zip \
    --environment "Variables={CONTROLLER_FN=$CTRL,PARAM_PREFIX=$PFX}" --query FunctionArn --output text)
else aws lambda update-function-code --function-name $WAKE --zip-file fileb:///tmp/wake.zip >/dev/null; fi
aws lambda add-permission --function-name $WAKE --statement-id alb --action lambda:InvokeFunction --principal elasticloadbalancing.amazonaws.com --source-arn "arn:aws:elasticloadbalancing:$AWS_REGION:$ACC:targetgroup/sybeez-school-wake-tg/*" >/dev/null 2>&1 || true
WAKE_TG=$(aws elbv2 describe-target-groups --names sybeez-school-wake-tg --query 'TargetGroups[0].TargetGroupArn' --output text 2>/dev/null || true)
if [ -z "$WAKE_TG" ] || [ "$WAKE_TG" = None ]; then
  WAKE_TG=$(aws elbv2 create-target-group --name sybeez-school-wake-tg --target-type lambda --query 'TargetGroups[0].TargetGroupArn' --output text)
  aws elbv2 register-targets --target-group-arn "$WAKE_TG" --targets Id=$WAKE_ARN
fi
cat > $CTRL_ENV_FILE <<ENV
{"Variables":{"PARAM_PREFIX":"$PFX","ECS_CLUSTER":"$CLUSTER","DB_INSTANCE":"sybeez-school-db","LISTENER_ARN":"$LISTENER","WAKE_TG_ARN":"$WAKE_TG",
"LB_DIM":"app/sybeez-school-alb/974f6ba5cf3fd4a6","TG_DIMS":"[\"$API_TG\",\"$AI_TG\"]","CF_DIST_IDS":"$CF_IDS","QUEUE_URL":"$QUEUE_URL","TOPIC_ARN":"$TOPIC_ARN",
"ROTATION_SECRETS":"$SECRET_ID","IDLE_MINUTES":"15","COOLDOWN_MINUTES":"30"}}
ENV
if aws lambda get-function --function-name $CTRL >/dev/null 2>&1; then
  aws lambda update-function-code --function-name $CTRL --zip-file fileb:///tmp/controller.zip >/dev/null
  aws lambda wait function-updated --function-name $CTRL
  aws lambda update-function-configuration --function-name $CTRL --environment file://$CTRL_ENV_FILE >/dev/null
else
  aws lambda create-function --function-name $CTRL --runtime python3.12 --handler controller.handler --timeout 900 --memory-size 256 \
    --role arn:aws:iam::$ACC:role/$CTRL-role --zip-file fileb:///tmp/controller.zip --environment file://$CTRL_ENV_FILE >/dev/null
  aws lambda wait function-active --function-name $CTRL
fi
# one pass at a time; stale queued checks are dropped
aws lambda put-function-concurrency --function-name $CTRL --reserved-concurrent-executions 1 >/dev/null 2>&1 || echo "WARN: could not set reserved concurrency (account limit); passes are idempotent anyway"
aws lambda put-function-event-invoke-config --function-name $CTRL --maximum-retry-attempts 0 --maximum-event-age-in-seconds 300 >/dev/null

# Scheduler every 5 minutes
SROLE=$CTRL-scheduler-role
STRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"scheduler.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam get-role --role-name $SROLE >/dev/null 2>&1 || aws iam create-role --role-name $SROLE --assume-role-policy-document "$STRUST" >/dev/null
aws iam put-role-policy --role-name $SROLE --policy-name invoke --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"lambda:InvokeFunction\",\"Resource\":\"arn:aws:lambda:$AWS_REGION:$ACC:function:$CTRL\"}]}"
sleep 8
SARGS=(--name sybeez-school-idle-check --schedule-expression "rate(5 minutes)" --flexible-time-window Mode=OFF --state ENABLED
  --target "{\"Arn\":\"arn:aws:lambda:$AWS_REGION:$ACC:function:$CTRL\",\"RoleArn\":\"arn:aws:iam::$ACC:role/$SROLE\",\"Input\":\"{\\\"action\\\":\\\"check\\\"}\",\"RetryPolicy\":{\"MaximumRetryAttempts\":0}}")
aws scheduler create-schedule "${SARGS[@]}" >/dev/null 2>&1 || aws scheduler update-schedule "${SARGS[@]}" >/dev/null
echo "Deployed. dry-run=$(aws ssm get-parameter --name $PFX/dry-run --query Parameter.Value --output text) enabled=$(aws ssm get-parameter --name $PFX/enabled --query Parameter.Value --output text)"
