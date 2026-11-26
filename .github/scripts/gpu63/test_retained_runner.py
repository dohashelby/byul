import json
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.modules.setdefault("boto3", types.SimpleNamespace())
import retained_runner as runner


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 26, 4, tzinfo=timezone.utc)
        self.calls = []
        self.locked = False
        self.owner = "owned-job"
        self.env = {"ACCOUNT": "123456789012", "REGION": "ap-northeast-2", "INSTANCE": "i-owned",
                    "JOB": "owned-job", "EXECUTION_ROLE": "limited-role", "STATE_PARAMETER": "/owned/state",
                    "LOCK_PARAMETER": "/owned/launch-lock", "SOURCE_MAIN": "a" * 40,
                    "HARNESS_COMMIT": "b" * 40, "WORKLOAD_SHA256": "c" * 64,
                    "DEADLINE": (self.now + timedelta(minutes=45)).isoformat()}
        self.event = {"action": "launch", **{key: self.env[key.upper()] for key in
                      ("job", "source_main", "harness_commit", "workload_sha256")}}
        self.watch = {"job": self.owner, "instance": "i-owned", "blocked": False,
                      "recorded_utc": self.now.isoformat(), "allow_until": (self.now + timedelta(minutes=3)).isoformat(),
                      "deadline": self.env["DEADLINE"]}

    def client(self, service, **kwargs):
        test = self

        class Client:
            def get_caller_identity(self):
                return {"Account": "123456789012", "Arn": "arn:aws:sts::123456789012:assumed-role/limited-role/runner"}

            def describe_instances(self, **args):
                return {"Reservations": [{"Instances": [{"InstanceId": "i-owned", "InstanceType": "g6.2xlarge",
                    "State": {"Name": "stopped"}, "Tags": [{"Key": "byul:gpu63-session", "Value": test.owner}]}]}]}

            def get_parameter(self, **args):
                return {"Parameter": {"Value": json.dumps(test.watch)}}

            def get_account_plan_state(self):
                return {"accountId": "123456789012", "accountPlanStatus": "ACTIVE",
                        "accountPlanRemainingCredits": {"amount": "100", "unit": "USD"}}

            def put_parameter(self, **args):
                if test.locked:
                    raise RuntimeError("ParameterAlreadyExists")
                test.locked = True
                test.calls.append(("lock", args))

            def start_instances(self, **args):
                test.calls.append(("start", args))
                return {"StartingInstances": []}

            def stop_instances(self, **args):
                test.calls.append(("stop", args))
                return {}

        return Client()

    def invoke(self):
        with patch.dict(os.environ, self.env, clear=True), patch.object(runner, "_now", return_value=self.now), \
                patch.object(runner.boto3, "client", side_effect=self.client, create=True):
            return runner.handler(self.event, None)

    def test_source_bound_fresh_job_locks_before_starting_exact_instance(self):
        self.invoke()
        self.assertEqual([item[0] for item in self.calls], ["lock", "start"])
        self.assertEqual(self.calls[1][1], {"InstanceIds": ["i-owned"]})
        self.assertFalse(self.calls[0][1]["Overwrite"])

    def test_repeated_launch_does_not_submit_another_start(self):
        self.invoke()
        with self.assertRaisesRegex(RuntimeError, "ParameterAlreadyExists"):
            self.invoke()
        self.assertEqual(sum(call[0] == "start" for call in self.calls), 1)

    def test_stale_watch_blocks_launch_before_lock(self):
        self.watch["recorded_utc"] = (self.now - timedelta(seconds=91)).isoformat()
        with self.assertRaisesRegex(ValueError, "FRESH_WATCH"):
            self.invoke()
        self.assertEqual(self.calls, [])

    def test_unowned_instance_cannot_be_started(self):
        self.owner = "someone-else"
        with self.assertRaisesRegex(ValueError, "RETAINED_OWNER"):
            self.invoke()
        self.assertEqual(self.calls, [])

    def test_other_workload_cannot_be_started(self):
        self.event["workload_sha256"] = "d" * 64
        with self.assertRaisesRegex(ValueError, "SOURCE_BOUND"):
            self.invoke()
        self.assertEqual(self.calls, [])

    def test_stop_remains_available_after_deadline_without_fresh_watch(self):
        self.event["action"] = "stop"
        self.env["DEADLINE"] = (self.now - timedelta(minutes=1)).isoformat()
        self.watch["blocked"] = True
        self.assertEqual(self.invoke()["action"], "stop")


if __name__ == "__main__":
    unittest.main()
