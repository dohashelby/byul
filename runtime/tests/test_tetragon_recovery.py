from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from serving_runtime.enforcement.recovery import RecoveryTracker,reassess_operations
from serving_runtime.enforcement.attribution import SCHEMA

AT=datetime(2026,11,6,tzinfo=timezone.utc)


class RecoveryTests(unittest.TestCase):
    def test_bare_saved_success_does_not_confirm_an_operations_check(self):
        self.assertEqual(reassess_operations({'status':'passed','facts':{}})['status'],'failed')
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.agents=[{"node":"node","pod_uid":"agent-1","restart_count":0}]
        self.policy={"node":"node","namespace":"serving","name":"policy","mode":"monitor", "spec_sha256":"a"*64}
        self.snapshot={"state":"ok","agents":self.agents,"policies":[{**self.policy,"state":"enabled"}]}
        self.write("baseline",{"schema_version":"runtime-tetragon-baseline/1.0","context":"kind-test","policies":[self.policy]})
        self.inputs(AT)
        (self.root/"events").write_text("")
        self.tracker=self.new_tracker()

    def write(self,name,value):
        (self.root/name).write_text(json.dumps(value))

    def new_tracker(self):
        return RecoveryTracker("kind-test",self.root/"recovery",baseline_path=self.root/"baseline",
            identities_path=self.root/"ids",heartbeat_path=self.root/"heartbeat",observations_path=self.root/"events")

    def inputs(self,at):
        self.write("ids",{"schema_version":SCHEMA,"context":"kind-test","generated_at":at.isoformat(),
            "state":"ok","agents":self.agents,"records":[{"node":"node","namespace":"serving","pod_name":"engine", "pod_uid":"uid",
                "container_name":"engine","container_id":"a"*64,"binary":"/bin/python","node_pid":77,"ns_pid":1,
                "pidns":123,"start_ticks":1000,"clock_ticks":100,"stable":True}],"policies":[]})
        self.write("heartbeat",{"schema":"runtime-observation-feed-heartbeat/1.0","kind":"tetragon",
            "kube_context":"kind-test","updated_at":at.isoformat(),"state":"running","processes":[{"running":True}]})

    def event(self,at,**changes):
        value={"source":"tetragon","data_origin":"real","observed_at":at.isoformat(),
               "identity":{"state":"confirmed","node":"node","context":"kind-test","sampled_at":at.isoformat()}}
        value.update(changes)
        with (self.root/"events").open("a") as output: output.write(json.dumps(value)+"\n")

    def healthy(self):
        self.assertEqual(self.tracker.update(self.snapshot,now=AT)["state"],"recovering")
        self.event(AT+timedelta(seconds=1))
        return self.tracker.update(self.snapshot,now=AT+timedelta(seconds=2))

    def test_agent_restart_requires_current_policies_identity_and_a_new_attributed_event(self):
        self.assertEqual(self.healthy()["state"],"healthy")
        self.agents[0]["restart_count"]=1
        result=self.tracker.update(self.snapshot,now=AT+timedelta(seconds=3))
        self.assertEqual(result["state"],"recovering")
        self.assertIsNotNone(result["open_gap"])
        self.inputs(AT+timedelta(seconds=4))
        result=self.tracker.update(self.snapshot,now=AT+timedelta(seconds=4))
        self.assertFalse(result["events_verified"])
        self.event(AT+timedelta(seconds=5))
        result=self.tracker.update(self.snapshot,now=AT+timedelta(seconds=6))
        self.assertEqual(result["state"],"healthy")
        self.assertIsNone(result["open_gap"])
        self.assertEqual(result["history"][-1]["backfill"],"unverified")

    def test_policy_mismatch_does_not_close_gap_on_an_event_seen_before_restore(self):
        self.assertEqual(self.healthy()["state"],"healthy")
        self.snapshot["policies"][0]["mode"]="enforce"
        self.event(AT+timedelta(seconds=3))
        result=self.tracker.update(self.snapshot,now=AT+timedelta(seconds=4))
        self.assertFalse(result["policies_verified"])
        self.snapshot["policies"][0]["mode"]="monitor"
        result=self.tracker.update(self.snapshot,now=AT+timedelta(seconds=5))
        self.assertEqual(result["state"],"recovering")
        self.event(AT+timedelta(seconds=6))
        self.assertEqual(self.tracker.update(self.snapshot,now=AT+timedelta(seconds=7))["state"],"healthy")

    def test_live_follow_process_and_old_or_synthetic_data_do_not_prove_recovery(self):
        self.tracker.update(self.snapshot,now=AT)
        self.event(AT+timedelta(seconds=1),data_origin="synthetic")
        result=self.tracker.update(self.snapshot,now=AT+timedelta(seconds=2))
        self.assertTrue(result["writer_verified"])
        self.assertFalse(result["events_verified"])
        self.event(AT+timedelta(seconds=3),identity={"state":"confirmed","node":"node","context":"other","sampled_at":AT.isoformat()})
        self.assertFalse(self.tracker.update(self.snapshot,now=AT+timedelta(seconds=4))["events_verified"])

    def test_stale_writer_opens_gap_and_state_survives_collector_restart(self):
        self.assertEqual(self.healthy()["state"],"healthy")
        result=self.tracker.update(self.snapshot,now=AT+timedelta(seconds=25))
        self.assertFalse(result["writer_verified"])
        self.assertIsNotNone(result["open_gap"])
        restarted=self.new_tracker()
        self.inputs(AT+timedelta(seconds=26))
        self.assertEqual(restarted.update(self.snapshot,now=AT+timedelta(seconds=26))["state"],"recovering")
        self.event(AT+timedelta(seconds=27))
        self.assertEqual(restarted.update(self.snapshot,now=AT+timedelta(seconds=28))["state"],"healthy")

    def test_missing_or_wrong_context_baseline_never_adopts_observed_state(self):
        (self.root/"baseline").unlink()
        self.assertEqual(self.tracker.update(self.snapshot,now=AT)["state"],"not_configured")
        self.assertTrue((self.root/"recovery").exists())

    def test_alive_writer_with_no_fresh_verified_pulse_is_not_healthy(self):
        self.assertEqual(self.healthy()["state"],"healthy")
        self.inputs(AT+timedelta(seconds=35))
        result=self.tracker.update(self.snapshot,now=AT+timedelta(seconds=35))
        self.assertTrue(result["writer_verified"])
        self.assertEqual(result["state"],"recovering")
        self.assertFalse(result["events_verified"])
        self.event(AT+timedelta(seconds=36))
        self.assertEqual(self.tracker.update(self.snapshot,now=AT+timedelta(seconds=37))["state"],"healthy")

    def test_failed_query_does_not_erase_epoch_or_claim_an_agent_restart(self):
        self.assertEqual(self.healthy()["state"],"healthy")
        failed={**self.snapshot,'state':'error','agents':[],'policies':[]}
        result=self.tracker.update(failed,now=AT+timedelta(seconds=3))
        self.assertEqual(result['epochs']['node'],['agent-1',0])
        self.assertNotIn('agent_epoch_changed',result['reasons'])


if __name__=="__main__": unittest.main()
