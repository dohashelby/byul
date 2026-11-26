"""Short-lived watchdog for one explicitly tagged retained EC2 instance.

Stop only: this handler cannot terminate instances or delete retained storage.
The deployer supplies identifiers, a fixed deadline and the source-bound job.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3


def _now():
    return datetime.now(timezone.utc)


def _time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("UTC_TIME_REQUIRED")
    return parsed.astimezone(timezone.utc)


def lease_policy(until):
    return {"Version": "2012-10-17", "Statement": [{
        "Effect": "Deny", "Action": "ec2:StartInstances", "Resource": "*",
        "Condition": {"DateGreaterThanEquals": {"aws:CurrentTime": until.isoformat()}},
    }]}


def credit_amount(value, account):
    if (value.get("accountId") != account or value.get("accountPlanStatus") != "ACTIVE"
            or value.get("accountPlanRemainingCredits", {}).get("unit") != "USD"):
        raise ValueError("CREDIT_ACCOUNT_STATUS_OR_CURRENCY_INVALID")
    amount = Decimal(str(value["accountPlanRemainingCredits"]["amount"]))
    if not amount.is_finite() or amount <= Decimal("5"):
        raise ValueError("CREDIT_RESERVE_REACHED")
    return amount


def handler(event, context):
    now = _now()
    region, account, instance, job = [os.environ[key] for key in ("REGION", "ACCOUNT", "INSTANCE", "JOB")]
    deadline, started = [_time(os.environ[key]) for key in ("DEADLINE", "STARTED")]
    price, budget = [Decimal(os.environ[key]) for key in ("HOURLY_USD", "BUDGET_USD")]
    if (not Decimal("0") < budget <= Decimal("6") or not Decimal("0") < price
            or not timedelta(0) < deadline - started <= timedelta(minutes=45)):
        raise ValueError("FIXED_SESSION_LIMIT_INVALID")
    ec2 = boto3.client("ec2", region_name=region)
    iam = boto3.client("iam")
    ssm = boto3.client("ssm", region_name=region)
    response = ec2.describe_instances(InstanceIds=[instance])
    instances = [item for reservation in response["Reservations"] for item in reservation["Instances"]]
    if (len(instances) != 1 or instances[0]["InstanceId"] != instance
            or {item["Key"]: item["Value"] for item in instances[0].get("Tags", [])}.get("byul:gpu63-session") != job):
        raise ValueError("EXACT_RETAINED_SESSION_OWNER_UNVERIFIED")
    state = instances[0]["State"]["Name"]
    elapsed = max(Decimal("0"), Decimal(str((now - started).total_seconds())))
    estimated = elapsed * price / Decimal("3600")
    reason = "SESSION_DEADLINE" if now >= deadline else "SESSION_ESTIMATED_BUDGET" if estimated >= budget else None
    amount = None
    if reason is None:
        try:
            amount = credit_amount(boto3.client("freetier", region_name="us-east-1").get_account_plan_state(), account)
        except Exception:
            reason = "FRESH_CREDIT_UNVERIFIED_OR_RESERVE_REACHED"
    until = min(deadline, now + timedelta(minutes=3)) if reason is None else now - timedelta(seconds=1)
    errors = []
    try:
        iam.put_role_policy(RoleName=os.environ["EXECUTION_ROLE"], PolicyName="fresh-credit-lease",
                            PolicyDocument=json.dumps(lease_policy(until)))
    except Exception:
        errors.append("LEASE_UPDATE_FAILED")
        reason = reason or "LEASE_UPDATE_FAILED"
    stop_requested = False
    if reason is not None and state in {"pending", "running"}:
        ec2.stop_instances(InstanceIds=[instance])
        stop_requested = True
    record = {
        "schema": "byul-retained-gpu63-guard/1", "job": job, "instance": instance,
        "recorded_utc": now.isoformat(), "deadline": deadline.isoformat(),
        "blocked": reason is not None, "reason": reason, "errors": errors,
        "allow_until": until.isoformat(), "state": state, "stop_requested": stop_requested,
        "credit_usd": str(amount) if amount is not None else None,
        "estimated_ec2_usd": str(estimated), "retained_storage_deleted": False,
    }
    ssm.put_parameter(Name=os.environ["STATE_PARAMETER"], Type="String", Overwrite=True,
                      Value=json.dumps(record, sort_keys=True))
    return record
