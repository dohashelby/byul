import importlib.util
import json
import os
from pathlib import Path
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

fake_boto = types.SimpleNamespace()
sys.modules["boto3"] = fake_boto
spec = importlib.util.spec_from_file_location("retained_guard", Path(__file__).with_name("retained_guard.py"))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 26, 4, tzinfo=timezone.utc)
        self.calls = []
        self.owner = "job-one"
        self.instance_state = "running"
        self.credit_error = False
        self.lease_error = False
        self.credits = {"accountId": "123456789012", "accountPlanStatus": "ACTIVE",
                        "accountPlanRemainingCredits": {"amount": "100", "unit": "USD"}}
        self.env = {"REGION": "ap-northeast-2", "ACCOUNT": "123456789012", "INSTANCE": "i-owned",
                    "JOB": "job-one", "STARTED": self.now.isoformat(),
                    "DEADLINE": (self.now + timedelta(minutes=45)).isoformat(),
                    "HOURLY_USD": "1.20208", "BUDGET_USD": "6", "EXECUTION_ROLE": "execution-owned",
                    "STATE_PARAMETER": "/byul/owned-job/state", "RULE": "owned-watch-rule"}

    def client(self, service, **kwargs):
        test = self

        class Client:
            def describe_instances(self, **args):
                test.calls.append((service, "describe", args))
                return {"Reservations": [{"Instances": [{"InstanceId": "i-owned", "State": {"Name": test.instance_state},
                    "Tags": [{"Key": "byul:gpu63-session", "Value": test.owner}]}]}]}

            def get_account_plan_state(self):
                if test.credit_error:
                    raise RuntimeError("billing unavailable")
                return test.credits

            def put_role_policy(self, **args):
                test.calls.append((service, "lease", args))
                if test.lease_error:
                    raise RuntimeError("IAM unavailable")

            def stop_instances(self, **args):
                test.calls.append((service, "stop", args))

            def put_parameter(self, **args):
                test.calls.append((service, "state", args))

            def disable_rule(self, **args):
                test.calls.append((service, "disable", args))

        return Client()

    def invoke(self, now=None):
        with patch.dict(os.environ, self.env, clear=True), patch.object(guard, "_now", return_value=now or self.now), \
                patch.object(fake_boto, "client", side_effect=self.client, create=True):
            return guard.handler({}, None)

    def test_fresh_credit_only_renews_short_lease(self):
        result = self.invoke()
        self.assertFalse(result["blocked"])
        self.assertEqual(guard._time(result["allow_until"]), self.now + timedelta(minutes=3))
        self.assertFalse(any(call[1] == "stop" for call in self.calls))

    def test_deadline_stops_exact_owned_instance_even_if_credit_api_is_down(self):
        self.credit_error = True
        result = self.invoke(self.now + timedelta(minutes=45))
        self.assertEqual(result["reason"], "SESSION_DEADLINE")
        self.assertIn(("ec2", "stop", {"InstanceIds": ["i-owned"]}), self.calls)

    def test_fresh_credit_failure_stops_owned_instance(self):
        self.credit_error = True
        result = self.invoke()
        self.assertTrue(result["blocked"])
        self.assertTrue(result["stop_requested"])
        self.assertLess(guard._time(result["allow_until"]), self.now)

    def test_other_job_is_never_stopped_or_given_a_lease(self):
        self.owner = "another-job"
        with self.assertRaisesRegex(ValueError, "OWNER_UNVERIFIED"):
            self.invoke()
        self.assertFalse(any(call[1] in {"stop", "lease"} for call in self.calls))

    def test_five_dollar_reserve_and_wrong_account_or_currency_fail_closed(self):
        for update in ("reserve", "account", "currency", "inactive"):
            with self.subTest(update=update):
                original = json.loads(json.dumps(self.credits))
                if update == "reserve":
                    self.credits["accountPlanRemainingCredits"]["amount"] = "5"
                elif update == "account":
                    self.credits["accountId"] = "000000000000"
                elif update == "currency":
                    self.credits["accountPlanRemainingCredits"]["unit"] = "EUR"
                else:
                    self.credits["accountPlanStatus"] = "INACTIVE"
                self.assertTrue(self.invoke()["stop_requested"])
                self.credits = original

    def test_lease_update_failure_still_stops_the_instance(self):
        self.lease_error = True
        result = self.invoke()
        self.assertEqual(result["reason"], "LEASE_UPDATE_FAILED")
        self.assertTrue(result["stop_requested"])

    def test_session_longer_than_45_minutes_cannot_be_activated(self):
        self.env["DEADLINE"] = (self.now + timedelta(minutes=46)).isoformat()
        with self.assertRaisesRegex(ValueError, "FIXED_SESSION_LIMIT_INVALID"):
            self.invoke()
        self.assertEqual(self.calls, [])

    def test_expired_stopped_session_disables_only_its_watch_rule(self):
        self.instance_state = "stopped"
        self.invoke(self.now + timedelta(minutes=45))
        self.assertIn(("events", "disable", {"Name": "owned-watch-rule"}), self.calls)
        self.assertFalse(any(call[1] == "stop" for call in self.calls))


if __name__ == "__main__":
    unittest.main()
