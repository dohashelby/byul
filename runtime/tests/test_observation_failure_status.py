"""Source loss and save failures stay visible through collection and recovery."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from unittest.mock import patch

from serving_runtime.mvp.collectors import Collector
from serving_runtime.mvp.observation_feed import HEARTBEAT_SCHEMA, ObservationFeed


def setup_feed(tmp_path):
    path = tmp_path / "feed.jsonl"
    fixture = Path(__file__).parent / "fixtures" / "enforcement" / "hubble_flows.synthetic.jsonl"
    if not fixture.exists():
        fixture = next((Path(__file__).parent / "fixtures").rglob("hubble_flows.synthetic.jsonl"))
    now = datetime.now(timezone.utc)
    flow = json.loads(fixture.read_text().splitlines()[0])
    flow["flow"]["time"] = now.isoformat()
    path.write_text(json.dumps(flow) + "\n")
    heartbeat = tmp_path / "heartbeat.json"
    document = dict(schema=HEARTBEAT_SCHEMA, kind="hubble", feed_path=str(path),
                    started_at=(now - timedelta(seconds=1)).isoformat(), updated_at=now.isoformat(),
                    state="running", processes=[dict(name="hubble", running=True, returncode=None)])
    heartbeat.write_text(json.dumps(document))
    feed = ObservationFeed(kind="hubble", path=path, data_dir=tmp_path / "data", data_origin="real",
                           environment="lab", run_id="failure-test", heartbeat_path=heartbeat)
    collector = Collector("observation_feed_hubble", feed.collect)
    return feed, collector, heartbeat, document


def test_open_gap_replaces_old_healthy_metadata_without_success(tmp_path):
    feed, collector, heartbeat, doc = setup_feed(tmp_path)
    collector.collect()
    old_success = collector.snapshot()["last_success_at"]
    gap = dict(gap_id="g1", opened_at=doc["updated_at"], attempts=3)
    doc.update(state="reconnecting", open_gap=gap)
    doc["processes"][0]["running"] = False
    heartbeat.write_text(json.dumps(doc))
    collector.collect()
    status = collector.snapshot()
    assert status["state"] == "error"
    assert status["last_success_at"] == old_success
    assert status["last_value"]["writer"]["state"] == "reconnecting"
    assert status["last_value"]["writer"]["open_gap"]["gap_id"] == "g1"
    doc.update(state="running", open_gap=None, last_gap={**gap, "closed_at": doc["updated_at"], "backfill": "requested_unverified"})
    doc["processes"][0]["running"] = True
    heartbeat.write_text(json.dumps(doc))
    collector.collect()
    status = collector.snapshot()
    assert status["state"] == "ok"
    assert status["last_value"]["writer"]["open_gap"] is None
    assert status["last_value"]["writer"]["last_gap"]["backfill"] == "requested_unverified"


def test_record_save_failure_retries_same_event_and_keeps_history(tmp_path):
    feed, collector, _, _ = setup_feed(tmp_path)
    with patch.object(feed.store, "append", side_effect=OSError("test save failed")):
        collector.collect()
    failed = collector.snapshot()
    assert failed["state"] == "error" and failed["last_success_at"] is None
    value = failed["last_value"]
    assert value["storage_errors_total"] == 1
    assert value["last_storage_operation"] == "event_record"
    assert value["last_storage_error_class"] == "OSError"
    assert value["last_storage_error_at"]
    assert feed._offset == 0
    collector.collect()
    recovered = collector.snapshot()
    assert recovered["state"] == "ok"
    assert recovered["last_value"]["storage_errors_total"] == 1
    assert len(feed.store.read_all()[0]) == 1
    restored = ObservationFeed(kind="hubble", path=feed.path, data_dir=feed.state_path.parent,
                               data_origin="real", environment="lab", run_id="failure-test", heartbeat_path=feed.heartbeat_path)
    assert restored.collect()[0]["storage_errors_total"] == 1


def test_checkpoint_failure_retries_without_new_events_or_duplicates(tmp_path):
    feed, collector, _, _ = setup_feed(tmp_path)
    with patch.object(feed, "_write_checkpoint", side_effect=OSError("disk full")):
        collector.collect()
    failed = collector.snapshot()
    assert failed["state"] == "error"
    assert failed["last_value"]["checkpoint_pending"] is True
    assert failed["last_value"]["last_storage_operation"] == "checkpoint"
    assert len(feed.store.read_all()[0]) == 1
    collector.collect()
    recovered = collector.snapshot()
    assert recovered["state"] == "ok"
    assert recovered["last_value"]["checkpoint_pending"] is False
    assert recovered["last_value"]["storage_errors_total"] == 1
    assert feed.state_path.exists()
    assert len(feed.store.read_all()[0]) == 1


def test_reconnecting_drop_survives_writer_counter_reset_and_gateway_restart(tmp_path):
    feed, collector, heartbeat, doc = setup_feed(tmp_path)
    # The same heartbeat and fixture can represent a Tetragon writer with no
    # new supported events; the loss counter is independently reported.
    feed.kind = "tetragon"
    feed.state_path = feed.state_path.with_name("observation_feed_tetragon.json")
    feed.path.write_text("")
    doc.update(kind="tetragon", dropped_lines_total=0)
    heartbeat.write_text(json.dumps(doc))
    collector.source = "observation_feed_tetragon"
    collector.collect()
    doc.update(state="reconnecting", dropped_lines_total=10,
               open_gap=dict(gap_id="dropped-gap", opened_at=doc["updated_at"], attempts=1))
    doc["processes"][0]["running"] = False
    heartbeat.write_text(json.dumps(doc))
    collector.collect()
    value = collector.snapshot()["last_value"]
    assert value["dropped_lines_total"] == 10
    assert value["redaction_dropped_lines_total"] == 10
    assert value["redaction_loss_detected"] is True
    assert value["last_redaction_drop_at"]
    restored = ObservationFeed(kind="tetragon", path=feed.path, data_dir=feed.state_path.parent,
                               data_origin="real", environment="lab", run_id="failure-test", heartbeat_path=heartbeat)
    doc.update(state="running", dropped_lines_total=0, open_gap=None)
    doc["processes"][0]["running"] = True
    heartbeat.write_text(json.dumps(doc))
    value, _ = restored.collect()
    assert value["dropped_lines_total"] == 0
    assert value["redaction_dropped_lines_total"] == 10
    assert value["redaction_loss_detected"] is True
    assert value["last_redaction_drop_at"]


def test_drop_totals_separate_writer_instances_even_when_counter_increases(tmp_path):
    feed, collector, heartbeat, doc = setup_feed(tmp_path)
    feed.kind = "tetragon"
    feed.state_path = feed.state_path.with_name("observation_feed_tetragon.json")
    feed.path.write_text("")
    doc.update(kind="tetragon", dropped_lines_total=10)
    heartbeat.write_text(json.dumps(doc))
    collector.collect()
    assert collector.snapshot()["last_value"]["redaction_dropped_lines_total"] == 10
    # Save/load the Gateway to verify that writer identity survives checkpoints.
    restored = ObservationFeed(kind="tetragon", path=feed.path, data_dir=feed.state_path.parent,
                               data_origin="real", environment="lab", run_id="failure-test", heartbeat_path=heartbeat)
    doc.update(started_at=datetime.now(timezone.utc).isoformat(), dropped_lines_total=12)
    heartbeat.write_text(json.dumps(doc))
    value, _ = restored.collect()
    assert value["redaction_dropped_lines_total"] == 22
    assert restored.collect()[0]["redaction_dropped_lines_total"] == 22


def test_producer_drop_time_reason_survive_checkpoint_and_new_writer(tmp_path):
    feed,collector,heartbeat,doc=setup_feed(tmp_path)
    feed.kind='tetragon';feed.path.write_text('')
    feed.state_path=feed.state_path.with_name('observation_feed_tetragon.json')
    when=(datetime.now(timezone.utc)-timedelta(seconds=.5)).isoformat()
    doc.update(kind='tetragon',dropped_lines_total=2,last_redaction_drop_reason='INVALID_JSON',last_redaction_drop_at=when)
    heartbeat.write_text(json.dumps(doc));collector.collect()
    value=collector.snapshot()['last_value']
    assert value['last_redaction_drop_at']==when and value['last_redaction_drop_reason']=='INVALID_JSON'
    restored=ObservationFeed(kind='tetragon',path=feed.path,data_dir=feed.state_path.parent,data_origin='real',environment='lab',run_id='failure-test',heartbeat_path=heartbeat)
    doc.update(dropped_lines_total=0,last_redaction_drop_reason=None,last_redaction_drop_at=None,started_at=datetime.now(timezone.utc).isoformat(),updated_at=datetime.now(timezone.utc).isoformat())
    heartbeat.write_text(json.dumps(doc));value=restored.collect()[0]
    assert value['redaction_dropped_lines_total']==2
    assert value['last_redaction_drop_at']==when and value['last_redaction_drop_reason']=='INVALID_JSON'
