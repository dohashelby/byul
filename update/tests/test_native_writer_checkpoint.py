"""Both native engines acknowledge only real, open hash-chain prefixes."""
import hashlib
import json
import os
from unittest.mock import patch

import pytest

from guard.adapters import vllm_lifecycle_sensor as base
from guard.adapters import vllm_termination_sensor as vllm
from guard.adapters import sglang_request_termination_sensor as sglang


@pytest.fixture(params=[vllm, sglang], ids=['vllm', 'sglang'])
def writer(request, tmp_path):
    directory = tmp_path / 'session'
    directory.mkdir(mode=0o700)
    config = {'output_dir': str(directory), 'control_path': str(directory / 'SEALED'), 'session': 'a' * 32}
    cls = type('ObservedWriter', (request.param.CheckpointWriterMixin, base.Writer), {'_checkpoint_directory': staticmethod(base._open_directory)})
    with patch.object(base.threading.Thread, 'start'):
        value = cls(config, 'b' * 64, {'uid': os.geteuid(), 'pid': os.getpid(), 'start_ticks': 123})
    yield value
    value.close('PROCESS_EXIT')


def rows(writer):
    path = __import__('pathlib').Path(writer.config['output_dir']) / f"pid-{writer.context['pid']}-{writer.context['start_ticks']}.jsonl"
    return path.read_bytes().splitlines(keepends=True)


def test_marker_binds_actual_open_prefix_and_does_not_close_writer(writer):
    before = rows(writer)
    assert writer.checkpoint() is True
    recorded = rows(writer)
    marker = json.loads(recorded[-1])
    previous = json.loads(before[-1])
    assert marker['event'] == 'CHECKPOINT'
    assert marker['seq'] == previous['seq'] + 1
    assert marker['previous_sha256'] == hashlib.sha256(before[-1]).hexdigest()
    assert marker['payload'] == {
        'schema': 'guard-native-checkpoint-ack/1', 'session': 'a' * 32,
        'prefix_seq': marker['seq'], 'prefix_sha256': marker['previous_sha256']}
    assert marker['monotonic_ns'] >= previous['monotonic_ns']
    assert writer.closed is False
    writer.write('MODULE', {'continued': True})
    assert json.loads(rows(writer)[-1])['event'] == 'MODULE'


def test_closed_writer_never_acknowledges_a_later_cutoff(writer):
    writer.close('SEALED')
    frozen = rows(writer)
    assert writer.checkpoint() is False
    assert rows(writer) == frozen
    assert json.loads(frozen[-1])['payload'] == {'reason': 'SEALED'}


def test_exhausted_writer_emits_only_original_bound_end(writer):
    with patch.object(base, 'MAX_ROWS', writer.seq + 1):
        assert writer.checkpoint() is False
    assert writer.closed is True
    assert json.loads(rows(writer)[-1])['payload'] == {'reason': 'BOUND_REACHED'}
    assert all(json.loads(row)['event'] != 'CHECKPOINT' for row in rows(writer))


def test_append_failure_never_acknowledges_a_checkpoint(writer):
    with patch.object(base.os, 'write', side_effect=OSError('disk full')):
        assert writer.checkpoint() is False
    assert writer.closed is True
    assert all(json.loads(row)['event'] != 'CHECKPOINT' for row in rows(writer))


def test_global_seal_precedes_periodic_marker(writer):
    from pathlib import Path
    Path(writer.config['control_path']).touch()
    writer._watch()
    assert writer.closed is True
    assert json.loads(rows(writer)[-1])['payload'] == {'reason': 'SEALED'}
    assert all(json.loads(row)['event'] != 'CHECKPOINT' for row in rows(writer))


def test_background_watcher_emits_periodic_prefix_before_normal_seal(writer):
    from pathlib import Path
    import sys
    module = sys.modules[writer._watch.__module__]
    waits = []
    class WakeAndSeal:
        def wait(self, seconds):
            waits.append(seconds)
            Path(writer.config['control_path']).touch()
    with patch.object(module.time, 'monotonic', side_effect=[0, 2, 2]), patch.object(module.threading, 'Event', WakeAndSeal):
        writer._watch()
    emitted = [json.loads(row) for row in rows(writer)]
    assert [row['event'] for row in emitted] == ['START', 'CHECKPOINT', 'END']
    assert emitted[-1]['payload'] == {'reason': 'SEALED'}
    assert waits == [0.1]


def test_opt_in_rollover_keeps_two_linked_closed_shards_and_active_marker(writer):
    from pathlib import Path
    writer._rolling = True
    writer._rolling_max_rows = 12
    writer.write('MODULE', {'module': 'source.module', 'verified': True, 'source_sha256': 'c' * 64})
    for index in range(60):
        writer.write('NATIVE', {'index': index})
        assert writer.checkpoint() is True
    directory = Path(writer.config['output_dir'])
    archived = sorted(directory.glob('*-segment-*.jsonl'), key=lambda p: int(p.stem.rsplit('-', 1)[1]))
    assert len(archived) == 2
    assert writer.closed is False
    for path in archived:
        data = path.read_bytes()
        closed = [json.loads(line) for line in data.splitlines()]
        assert closed[-1]['payload'] == {'reason': 'SEGMENT_ROLLOVER'}
        assert path.stat().st_mode & 0o777 == 0o600
        assert len(closed) <= 12
    current = [json.loads(row) for row in rows(writer)]
    assert current[0]['event'] == 'START'
    assert current[1]['event'] == 'SEGMENT'
    assert current[1]['payload']['previous_sha256'] == hashlib.sha256(archived[-1].read_bytes()).hexdigest()
    assert current[1]['payload']['previous_bytes'] == archived[-1].stat().st_size
    assert current[2]['event'] == 'MODULE'
    assert current[-1]['event'] == 'CHECKPOINT'
    assert current[-1]['payload']['prefix_seq'] == current[-1]['seq']
    assert current[-1]['payload']['prefix_sha256'] == current[-1]['previous_sha256']
    writer.close('SEALED')
    assert json.loads(rows(writer)[-1])['payload'] == {'reason': 'SEALED'}


def test_failed_rotation_stops_future_ack_without_repairing_original(writer):
    writer._rolling = True
    writer._rolling_max_rows = writer.seq + 4
    before = rows(writer)
    with patch.object(os, 'link', side_effect=OSError('archive unavailable')):
        assert writer.checkpoint() is False
    assert writer.closed is True
    recorded = rows(writer)
    assert recorded[:len(before)] == before
    assert json.loads(recorded[-1])['payload'] == {'reason': 'SEGMENT_ROLLOVER'}
    assert writer.checkpoint() is False
    assert rows(writer) == recorded


def test_runtime_opt_in_is_explicit_and_default_remains_fixed(writer):
    assert writer._rolling is False
    config = dict(writer.config)
    context = {**writer.context, 'start_ticks': 124}
    with patch.dict(os.environ, {'BYUL_NATIVE_ROLLING': '1'}), patch.object(base.threading.Thread, 'start'):
        other = type(writer)(config, 'b' * 64, context)
    try:
        assert other._rolling is True
    finally:
        other.close('PROCESS_EXIT')


def test_module_verification_loss_is_replayed_after_rollover(writer):
    writer._rolling = True
    writer._rolling_max_rows = 12
    writer.write('MODULE', {'module': 'changed.module', 'verified': True})
    writer.write('MODULE', {'module': 'changed.module', 'verified': False, 'reason': 'changed'})
    for _ in range(12):
        assert writer.checkpoint() is True
    latest = [json.loads(row) for row in rows(writer)]
    receipts = [row['payload'] for row in latest if row['event'] == 'MODULE']
    assert receipts == [{'module': 'changed.module', 'verified': False, 'reason': 'changed'}]


def test_raced_archive_destination_is_never_overwritten(writer):
    from pathlib import Path
    writer._rolling = True
    writer._rolling_max_rows = writer.seq + 4
    destination = Path(writer.config['output_dir']) / f"pid-{writer.context['pid']}-{writer.context['start_ticks']}-segment-0.jsonl"
    original_link = os.link
    def raced(*args, **kwargs):
        destination.write_bytes(b'preexisting immutable archive')
        return original_link(*args, **kwargs)
    with patch.object(os, 'link', side_effect=raced):
        assert writer.checkpoint() is False
    assert destination.read_bytes() == b'preexisting immutable archive'
    assert writer.closed is True
