"""Invoke one tagged retained instance through the limited Lambda role.

No EC2 creation, type changes, commands, termination or volume operations.
Launch is tied to one CI-checked workload digest and a single SSM launch lock.
"""
import json
import os
from datetime import datetime, timezone

import boto3

from retained_guard import _time, credit_amount


def _now():
    return datetime.now(timezone.utc)


def handler(event, context):
    required = {"action", "job", "source_main", "harness_commit", "workload_sha256"}
    if type(event) is not dict or set(event) != required or event["action"] not in {"launch", "stop"}:
        raise ValueError("EXACT_RETAINED_RUNNER_REQUEST_REQUIRED")
    for field in required - {"action"}:
        if event[field] != os.environ[field.upper()]:
            raise ValueError("SOURCE_BOUND_RETAINED_JOB_MISMATCH")
    now = _now()
    region, instance, job, account = [os.environ[key] for key in ("REGION", "INSTANCE", "JOB", "ACCOUNT")]
    caller = boto3.client("sts").get_caller_identity()
    expected = "arn:aws:sts::" + account + ":assumed-role/" + os.environ["EXECUTION_ROLE"] + "/"
    if caller["Account"] != account or not caller["Arn"].startswith(expected):
        raise ValueError("DEDICATED_EXECUTION_ROLE_REQUIRED")
    ec2 = boto3.client("ec2", region_name=region)
    result = ec2.describe_instances(InstanceIds=[instance])
    owned = [item for reservation in result["Reservations"] for item in reservation["Instances"]]
    if (len(owned) != 1 or owned[0]["InstanceId"] != instance or owned[0]["InstanceType"] != "g6.2xlarge"
            or {row["Key"]: row["Value"] for row in owned[0].get("Tags", [])}.get("byul:gpu63-session") != job):
        raise ValueError("EXACT_SINGLE_GPU_RETAINED_OWNER_REQUIRED")
    state = owned[0]["State"]["Name"]
    if event["action"] == "stop":
        reply = ec2.stop_instances(InstanceIds=[instance]) if state in {"pending", "running"} else {"state": state}
        return {"action": "stop", "job": job, "instance": instance, "reply": reply}
    if state != "stopped" or now >= _time(os.environ["DEADLINE"]):
        raise ValueError("RETAINED_LAUNCH_STATE_OR_DEADLINE_INVALID")
    ssm = boto3.client("ssm", region_name=region)
    watch = json.loads(ssm.get_parameter(Name=os.environ["STATE_PARAMETER"])["Parameter"]["Value"])
    age = (now - _time(watch["recorded_utc"])).total_seconds()
    if (watch["job"] != job or watch["instance"] != instance or watch["blocked"] is not False
            or not 0 <= age <= 90 or _time(watch["allow_until"]) <= now
            or _time(watch["deadline"]) != _time(os.environ["DEADLINE"])):
        raise ValueError("FRESH_WATCH_AND_LEASE_REQUIRED")
    credit_amount(boto3.client("freetier", region_name="us-east-1").get_account_plan_state(), account)
    # A response loss keeps this lock. Never retry a possibly submitted start.
    ssm.put_parameter(Name=os.environ["LOCK_PARAMETER"], Type="String", Overwrite=False,
                      Value=json.dumps({**event, "requested_utc": now.isoformat()}, sort_keys=True))
    reply = ec2.start_instances(InstanceIds=[instance])
    return {"action": "launch", "job": job, "instance": instance, "execution_role": caller["Arn"], "reply": reply}
