"""Verify saved worker shard pages and locate qualified native observations."""

from base64 import b64decode
from collections import Counter
import hashlib
import json
from pathlib import Path

from guard.adapters import vllm_initialization_release_sensor, vllm_initialization_sensor, vllm_lifecycle_catalog
from guard.adapters import vllm_worker_shard_callback as worker_callback
from guard.adapters.vllm_approved_file_read_composition import PROFILES as FILE_READ_PROFILES
from guard.collectors import gpu_common_inputs, worker_shard_producer
from guard.executors.cache_timing_group import read_regular
from guard.executors.runner_inputs import WRAPPER_SCHEMA as CAPTURE_SCHEMA
from guard.models import canonical_json, sha256_hex
from guard.runtime.bounded import strict_json
from guard.runtime.callback_pages import assemble
from guard.runtime.engine_http import (
    NORMAL_CALLBACK_ATTESTATION_SCHEMA, NORMAL_CALLBACK_SOURCE_KEY, NORMAL_CALLBACK_SOURCE_SCHEMA,
    _callback_page_source,
)

CAPTURE_LIMIT = 8 * 1024**2
_CHANGED = 'MVP_SUP06_WORKER_SHARD_SOURCE_CHANGED'
_INVALID_CAPTURE = 'MVP_SUP06_WORKER_SHARD_CAPTURE_INVALID'
_INVALID_PAGES = 'MVP_SUP06_WORKER_SHARD_PAGES_INVALID'
_PAGE_MISMATCH = 'MVP_SUP06_WORKER_SHARD_PAGE_PROJECTION_MISMATCH'
_RELEASE_SOURCE_MISMATCH = 'MVP_SUP06_WORKER_SHARD_RELEASE_SOURCE_MISMATCH'
_MOUNT_RECEIPT_MISMATCH = 'MVP_SUP06_WORKER_SHARD_MOUNT_RECEIPT_MISMATCH'
_FINALIZATION_UNVERIFIED = 'MVP_SUP06_WORKER_SHARD_FINALIZATION_UNVERIFIED'
_PAGE_LIMIT = 1024**2
_EVENT_LIMIT = 4096
_PAGE_DIAGNOSTIC_CODES = {
    'CALLBACK_CAPTURE_NOT_FINALIZED', 'CALLBACK_CHECKPOINT_UNAVAILABLE',
    'CALLBACK_JOURNAL_INCOMPLETE', 'CALLBACK_JOURNAL_STATE_UNAVAILABLE',
    'CALLBACK_PREFIX_INCOMPLETE', 'CALLBACK_PROJECTION_LIMIT',
    'CALLBACK_ROW_INCOMPLETE', 'CALLBACK_SOURCE_SET_CHANGED',
}
_LOAD = 'DefaultModelLoader.track_weights_loading'
_READY = 'WorkerProc.monitor_death_pipe'
_WITNESS_EVENT = {'prepare_call': 'shard_assigned', 'prepare_return': 'shard_assigned',
                  'load_call': 'load_completed', 'load_return': 'load_completed',
                  'ready_candidate_call': 'rank_ready'}
_TERMINAL_SCHEMA = 'guard-observer-journal-terminal/1'
_TERMINAL_FIELDS = {'schema', 'pid', 'start_ticks', 'process_sha256', 'session_sha256',
                    'accepted', 'processed', 'written', 'segments'}
_TERMINAL_LIMIT = 4096
_JOURNAL_LIMIT = 16 * 1024**2


def expected_worker_ranks(env):
    # Worker and TP ranks need a proven common numbering before association.
    runtime = env.runtime
    if runtime.pp_size == runtime.dp_size == 1 and runtime.tp_size is not None and 1 <= runtime.tp_size <= 64:
        return range(runtime.tp_size)
    return ()


def captured_process_epochs(wrapper):
    # This roster is projected from the same pinned capture's pages, apart from
    # the shard record. A process may have no retained rows or a missing epoch.
    processes = wrapper.get('processes')
    if type(processes) is not list:
        raise ValueError('MVP_SUP06_WORKER_SHARD_CAPTURE_INVALID')
    epochs = {}
    for item in processes:
        if (type(item) is not dict or item.keys() != {'pid', 'start_ticks', 'rows'}
                or not gpu_common_inputs._check(item['pid'], 'int') or item['pid'] == 0
                or not gpu_common_inputs._check(item['start_ticks'], 'opt_int')
                or not gpu_common_inputs._check(item['rows'], 'int')):
            raise ValueError('MVP_SUP06_WORKER_SHARD_CAPTURE_INVALID')
        epoch = (item['pid'], item['start_ticks'])
        if epoch in epochs:
            raise ValueError('MVP_SUP06_WORKER_SHARD_CAPTURE_INVALID')
        epochs[epoch] = item['rows']
    return epochs


def mounted_source_expectation(target, prepared, binding, pins):
    """Bind the optional mount receipt to selected target bytes and the prepared session."""
    if 'normal_tp2_callbacks' not in target:
        return None
    plan = target['normal_tp2_callbacks']
    if (type(plan) is not dict or pins is None or plan.get('manifest_sha256') != pins['prepared']
            or plan.get('session_sha256') != sha256_hex(prepared['session'].encode())
            or plan.get('assets_sha256') != sha256_hex(canonical_json(prepared['assets']))):
        raise ValueError(_MOUNT_RECEIPT_MISMATCH)
    return {'target_file_sha256': pins['target'], 'plan_sha256': sha256_hex(canonical_json(plan)),
            'owned_generation_sha256': sha256_hex(canonical_json(binding)),
            'source_sha256': sha256_hex(_callback_page_source(attest=True).encode()),
            'file_count': len(prepared['assets']) + 2}


def _mounted_source_receipt(wrapper, expected, scope, page_count):
    receipt = wrapper.get('mounted_source_attestation')
    if expected is None and 'mounted_source_attestation' not in wrapper:
        return
    if (expected is None or type(receipt) is not dict or receipt.keys() != {
            'schema', 'data_origin', 'target_sha256', 'target_file_sha256', 'environment_ref', 'plan_sha256',
            'source_sha256', 'owned_generation_sha256', 'observed_pages', 'observation', 'collection_truncated'}
            or receipt['schema'] != NORMAL_CALLBACK_ATTESTATION_SCHEMA
            or receipt['data_origin'] not in ('EXAMPLE', 'MEASURED')
            or receipt['target_sha256'] != scope['target_sha256']
            or receipt['environment_ref'] != scope['environment_ref']
            or any(receipt[key] != expected[key] for key in (
                'plan_sha256', 'target_file_sha256', 'owned_generation_sha256', 'source_sha256'))
            or type(receipt['observed_pages']) is not int or not 1 <= page_count <= receipt['observed_pages'] <= 256
            or type(receipt['collection_truncated']) is not bool
            or receipt['collection_truncated'] is not wrapper.get('truncated')
            or not receipt['collection_truncated'] and receipt['observed_pages'] != page_count):
        raise ValueError(_MOUNT_RECEIPT_MISMATCH)
    observation = receipt['observation']
    if (type(observation) is not dict
            or observation.keys() != {'schema', 'files_sha256', 'filesystem_sha256', 'file_count', 'uid', 'gid'}
            or observation['schema'] != NORMAL_CALLBACK_SOURCE_SCHEMA
            or any(not gpu_common_inputs._check(observation[key], 'sha')
                   for key in ('files_sha256', 'filesystem_sha256'))
            or any(type(observation[key]) is not int for key in ('file_count', 'uid', 'gid'))
            or (observation['file_count'], observation['uid'], observation['gid']) != (expected['file_count'], 2000, 0)):
        raise ValueError(_MOUNT_RECEIPT_MISMATCH)
    return {**receipt, 'archived_pages': page_count}


def _verify_page_release_sources(processes, version, session):
    # The packaged callback accepts both versions. Saved pages are bound to one
    # exact paired release, using the existing reviewed loader and worker pins.
    worker_profile = {'0.30.0': vllm_initialization_release_sensor, '0.31.0': vllm_initialization_sensor}[version]
    loader = vllm_lifecycle_catalog.row(FILE_READ_PROFILES[version])
    modules = {worker_callback.LOADER: loader['sources'][worker_callback.LOADER]['sha256'],
               worker_callback.MP: worker_profile.SOURCE_ADDITIONS[worker_callback.MP]}
    expected = {point: modules[module] for module, points in worker_callback.TARGETS.items() for point in points}
    session_sha256 = sha256_hex(session.encode())
    for process in processes:
        for event in process['events']:
            if (type(event) is not dict or event.get('schema') != worker_callback.SCHEMA
                    or event.get('session_sha256') != session_sha256 or event.get('entry_qualified_before') is not True):
                continue
            point, phase = event.get('point'), event.get('phase')
            if (type(point) is str and point in expected
                    and (phase == 'CALL' or phase in ('RETURN', 'RAISE') and event.get('entry_qualified_after') is True)
                    and event.get('source_file_sha256') != expected[point]):
                raise ValueError(_RELEASE_SOURCE_MISMATCH)


def _read_terminal_marker(packet, session_sha256):
    if (type(packet) is not dict or packet.keys() != {'raw_sha256', 'raw_b64'}
            or not gpu_common_inputs._check(packet['raw_sha256'], 'sha')
            or type(packet['raw_b64']) is not str or len(packet['raw_b64']) > 4 * ((_TERMINAL_LIMIT + 2) // 3)):
        return None
    try:
        raw = b64decode(packet['raw_b64'], validate=True)
        marker = strict_json(raw, limit=_TERMINAL_LIMIT)
    except (TypeError, ValueError):
        return None
    if (sha256_hex(raw) != packet['raw_sha256'] or type(marker) is not dict or marker.keys() != _TERMINAL_FIELDS
            or marker['schema'] != _TERMINAL_SCHEMA or marker['session_sha256'] != session_sha256
            or not gpu_common_inputs._check(marker['process_sha256'], 'sha')
            or any(not gpu_common_inputs._check(marker[key], 'int')
                   for key in ('pid', 'start_ticks', 'accepted', 'processed', 'written'))
            or not (marker['pid'] and marker['start_ticks'])
            or not 0 < marker['accepted'] == marker['processed'] == marker['written'] <= _EVENT_LIMIT):
        return None
    segments = marker['segments']
    if (type(segments) is not list or not 0 < len(segments) <= 32
            or any(type(segment) is not dict or segment.keys() != {'name', 'bytes', 'sha256'}
                   or segment['name'] != f"callbacks-{marker['pid']}-{index:04d}.jsonl"
                   or not gpu_common_inputs._check(segment['bytes'], 'int') or segment['bytes'] == 0
                   or not gpu_common_inputs._check(segment['sha256'], 'sha')
                   for index, segment in enumerate(segments))
            or sum(segment['bytes'] for segment in segments) > _JOURNAL_LIMIT):
        return None
    serialized = (json.dumps(marker, sort_keys=True, separators=(',', ':')) + '\n').encode()
    return marker if raw == serialized else None


class _TerminalReadback:
    """Compare worker terminal markers with the entire exact saved writer streams."""

    def __init__(self, page, reference, scope):
        self.markers = {}
        self.extents = {}
        self.page_sha256 = reference
        packets = page.get('journal_terminals')
        snapshot = page.get('snapshot')
        initial_offsets = page.get('start_cursor', {})
        if (type(packets) is not list or len(packets) > 64
                or type(snapshot) is not dict or type(initial_offsets) is not dict):
            return
        session_sha256 = sha256_hex(scope['session'].encode())
        candidates = []
        for packet in packets:
            marker = _read_terminal_marker(packet, session_sha256)
            if marker is not None:
                candidates.append((packet['raw_sha256'], marker))
        counts = Counter(marker['pid'] for _, marker in candidates)
        for raw_sha256, marker in candidates:
            pid, ticks = marker['pid'], marker['start_ticks']
            if counts[pid] != 1:
                continue
            files = {name: entry for name, entry in snapshot.items()
                     if type(entry) is dict and type(entry.get('pid')) is int and entry['pid'] == pid}
            segments = {segment['name']: segment for segment in marker['segments']}
            if (files.keys() != segments.keys()
                    or any(type(entry.get('start_ticks')) is not int or entry['start_ticks'] != ticks
                           or type(entry.get('size')) is not int or entry['size'] != segments[name]['bytes']
                           or type(initial_offsets.get(name, 0)) is not int or initial_offsets.get(name, 0) != 0
                           for name, entry in files.items())):
                continue
            self.markers[(pid, ticks)] = (raw_sha256, marker)
            self.extents.update({name: {'digest': hashlib.sha256(), 'size': 0, 'rows': 0, 'valid': True}
                                 for name in files})

    def preserve(self, page):
        if type(page.get('rows')) is not list:
            return
        for row in page['rows']:
            if type(row) is not dict or type(row.get('file')) is not str:
                continue
            name = row['file']
            if name not in self.extents:
                continue
            extent = self.extents[name]
            if not extent['valid']:
                continue
            # JournalSink writes this exact serialization. Validate its original
            # row digest and extent before hashing the entire retained sequence.
            raw = (json.dumps(row.get('event'), sort_keys=True, separators=(',', ':')) + '\n').encode()
            offset = extent['size']
            if (type(row.get('offset')) is not int or row['offset'] != offset
                    or type(row.get('end')) is not int or row['end'] != offset + len(raw)
                    or row.get('raw_sha256') != sha256_hex(raw)):
                extent['valid'] = False
                continue
            extent['digest'].update(raw)
            extent['size'] = row['end']
            extent['rows'] += 1

    def verified(self):
        retained = {}
        for epoch, (raw_sha256, marker) in self.markers.items():
            count = 0
            for segment in marker['segments']:
                extent = self.extents[segment['name']]
                if (not extent['valid'] or extent['size'] != segment['bytes']
                        or extent['digest'].hexdigest() != segment['sha256']):
                    break
                count += extent['rows']
            else:
                if count == marker['written']:
                    retained[epoch] = (marker['process_sha256'], {
                        'marker_sha256': raw_sha256, 'page_sha256': self.page_sha256,
                        'written_rows': count, 'segment_count': len(marker['segments']),
                    })
        return retained


def verify_saved_pages(wrapper, archive, document, *, record, epochs, pod, scope, version, expected_ranks=(),
                       mounted_source=None, require_finalized=False):
    """Replay original pages, optionally requiring closure of every expected writer."""
    refs = wrapper.get('raw_page_refs', [])
    if (type(refs) is not list or len(refs) > 256 or bool(refs) != (archive is not None)
            or any(type(ref) is not dict or ref.keys() != {'path', 'sha256'}
                   or ref['path'] != f'page-{index:04d}.json'
                   or not gpu_common_inputs._check(ref['sha256'], 'sha') for index, ref in enumerate(refs))):
        raise ValueError(_INVALID_PAGES)
    receipt = _mounted_source_receipt(wrapper, mounted_source, scope, len(refs))
    if require_finalized and (not refs or not expected_ranks or receipt is None
                              or receipt['data_origin'] != 'MEASURED' or receipt['collection_truncated']):
        raise ValueError(_FINALIZATION_UNVERIFIED)
    if not refs:
        return
    if len({ref['sha256'] for ref in refs}) != len(refs):
        raise ValueError(_INVALID_PAGES)

    errors = []
    count = 0
    locators = {}
    terminal = None

    def pages():
        nonlocal count, terminal
        capture_id = None
        for ref in refs:
            try:
                _, page = document(archive / ref['path'], ref['sha256'], _PAGE_LIMIT)
                page_id = page.get('capture_id') if type(page) is dict else None
                if (type(page_id) is not str or not gpu_common_inputs._SESSION.fullmatch(page_id)
                        or (capture_id is not None and page_id != capture_id)):
                    raise ValueError(_INVALID_PAGES)
            except (OSError, ValueError) as exc:
                # The live assembler reports iterator errors as gaps. A declared
                # archive reference must instead resolve to its original bytes.
                errors.append(_CHANGED if isinstance(exc, OSError) or str(exc) == _CHANGED else _INVALID_PAGES)
                return
            if (receipt is not None and canonical_json(page.get(NORMAL_CALLBACK_SOURCE_KEY))
                    != canonical_json(receipt['observation'])):
                errors.append(_MOUNT_RECEIPT_MISMATCH)
                return
            capture_id = page_id
            count += 1
            if expected_ranks and count == 1 and 'journal_terminals' in page:
                terminal = _TerminalReadback(page, ref['sha256'], scope)
            elif count > 1 and 'journal_terminals' in page:
                terminal = None  # The producer emits terminal markers only on the first page.
            if terminal is not None:
                terminal.preserve(page)
            if expected_ranks and type(page.get('rows')) is list:
                remaining = _EVENT_LIMIT - len(locators)
                for row_index, row in enumerate(page['rows'][:remaining]):
                    if type(row) is dict and type(row.get('event')) is dict:
                        locators[id(row['event'])] = {'page_sha256': ref['sha256'], 'row_index': row_index}
            yield page

    try:
        replayed = assemble(pages(), max_events=_EVENT_LIMIT)
    except (KeyError, TypeError, ValueError):
        raise ValueError(_INVALID_PAGES) from None
    if errors:
        raise ValueError(errors[0])
    if count != len(refs) or 'CALLBACK_PAGE_INVALID' in replayed['diagnostic_codes']:
        raise ValueError(_INVALID_PAGES)
    if receipt is not None and replayed['truncated'] and not receipt['collection_truncated']:
        raise ValueError(_MOUNT_RECEIPT_MISMATCH)

    processes = replayed['processes']
    if {(p['pid'], p['start_ticks']): len(p['events']) for p in processes} != epochs:
        raise ValueError(_PAGE_MISMATCH)
    _verify_page_release_sources(processes, version, scope['session'])
    try:
        replayed_record, _ = worker_shard_producer.record_from_processes(processes, pod=pod, **scope)
    except (KeyError, TypeError, ValueError):
        raise ValueError(_PAGE_MISMATCH) from None
    if canonical_json(replayed_record) != canonical_json(record):
        raise ValueError(_PAGE_MISMATCH)
    if expected_ranks:
        markers = terminal.verified() if terminal is not None else {}
        witnesses = _rank_witnesses(processes, record, pod, scope, expected_ranks, locators, markers)
        if require_finalized:
            ranks = witnesses['ranks']
            if (replayed['truncated'] or len(record['bindings']) != len(expected_ranks)
                    or [row['rank'] for row in ranks] != list(expected_ranks)
                    or any('journal_terminal' not in row for row in ranks)):
                raise ValueError(_FINALIZATION_UNVERIFIED)
        # A per-writer terminal can coexist with a gap in the overall capture.
        diagnostics = sorted(_PAGE_DIAGNOSTIC_CODES.intersection(replayed['diagnostic_codes']))
        if diagnostics:
            witnesses['page_diagnostic_codes'] = diagnostics
        if receipt is not None:
            # The raw pages preserve the reported file digest. Live owner proof
            # and hashing the deployed tree still belong to the collector.
            witnesses['mounted_source_attestation'] = receipt
        return witnesses


def _select_rank_rows(process, rank):
    points = {worker_callback.PREPARE: [], _LOAD: [], _READY: []}
    calls = {}
    for row in process['events']:
        if type(row) is not dict or row.get('schema') != worker_callback.SCHEMA:
            continue
        call = row.get('call_sha256')
        if gpu_common_inputs._check(call, 'sha'):
            calls.setdefault(call, []).append(row)
        point, phase = row.get('point'), row.get('phase')
        if type(point) is str and point in points and (point != _READY or phase == 'CALL'):
            points[point].append(row)

    selected = {}
    for point, name in ((worker_callback.PREPARE, 'prepare'), (_LOAD, 'load')):
        invocation = points[point]
        if [row.get('phase') for row in invocation] == ['CALL', 'RETURN']:
            entry, terminal = invocation
            call = entry.get('call_sha256')
            if gpu_common_inputs._check(call, 'sha') and calls[call] == invocation:
                selected.update({name + '_call': entry, name + '_return': terminal})
    ready = points[_READY]
    if len(ready) == 1:
        candidate = ready[0]
        fields, call = candidate.get('fields'), candidate.get('call_sha256')
        if (type(fields) is dict and type(fields.get('rank')) is int and fields['rank'] == rank
                and gpu_common_inputs._check(call, 'sha')):
            invocation = calls[call]
            if (invocation[0] is candidate and len(invocation) <= 2
                    and all(row.get('point') == _READY for row in invocation)):
                selected['ready_candidate_call'] = candidate
    return {key: row for key, row in selected.items()
            if type(row.get('fields')) is dict and type(row['fields'].get('tp_rank')) is int
            and row['fields']['tp_rank'] == rank}


def _shards_by_binding(record, event):
    by_binding = {}
    for row in record['events']:
        if row['event'] == event:
            by_binding.setdefault(row['binding'], []).append((row['shard_sha256'], row['bytes']))
    return by_binding


def _rank_witnesses(processes, record, pod, scope, expected_ranks, locators, markers):
    originals = {(item['pid'], item['start_ticks']): item for item in processes}
    ranks = Counter(row['rank'] for row in record['bindings'])
    observed = {(row['binding'], row['event']) for row in record['events']}
    candidates, selected_processes = {}, []
    for index, binding in enumerate(record['bindings']):
        rank = binding['rank']
        epoch = (binding['worker_pid'], binding['start_ticks'])
        if rank not in expected_ranks or ranks[rank] != 1 or epoch not in originals:
            continue
        selected = {key: event for key, event in _select_rank_rows(originals[epoch], rank).items()
                    if (index, _WITNESS_EVENT[key]) in observed}
        if selected:
            candidates[(rank, *epoch)] = index, selected
            selected_ids = {id(event) for event in selected.values()}
            selected_processes.append({**originals[epoch], 'events': [
                event for event in originals[epoch]['events'] if id(event) in selected_ids]})

    if not selected_processes:
        return {'expected_ranks': list(expected_ranks), 'ranks': []}
    try:
        qualified, _ = worker_shard_producer.record_from_processes(selected_processes, pod=pod, **scope)
    except ValueError as exc:
        if str(exc) != 'WORKER_SHARD_NO_WORKER_IDENTITY':
            raise
        return {'expected_ranks': list(expected_ranks), 'ranks': []}
    output = []
    original_assignments = _shards_by_binding(record, 'shard_assigned')
    qualified_assignments = _shards_by_binding(qualified, 'shard_assigned')
    original_requirements = _shards_by_binding(record, 'shard_required')
    qualified_requirements = _shards_by_binding(qualified, 'shard_required')
    for index, binding in enumerate(qualified['bindings']):
        rank, pid, ticks = (binding[key] for key in ('rank', 'worker_pid', 'start_ticks'))
        events = {row['event'] for row in qualified['events'] if row['binding'] == index}
        original_index, selected = candidates[(rank, pid, ticks)]
        if 'shard_assigned' in events:
            assigned = selected['prepare_return']['fields']
            expected = list(zip(assigned['files'], assigned['file_bytes']))
            if original_assignments.get(original_index) != expected or qualified_assignments.get(index) != expected:
                events.remove('shard_assigned')
        witnesses = {}
        for key, event in selected.items():
            required = _WITNESS_EVENT[key]
            if required in events and id(event) in locators:
                witnesses[key] = {**locators[id(event)], 'call_sha256': event['call_sha256'],
                                  'source_file_sha256': event['source_file_sha256'],
                                  'operation_started_ns': event['operation_started_ns'],
                                  'operation_finished_ns': event['operation_finished_ns']}
        if 'prepare_return' in witnesses:
            fields = selected['prepare_return']['fields']
            if fields['index_present'] is True and fields['required']:
                required_shards = sorted(set(fields['required']))
                projected = [(digest, None) for digest in required_shards]
                if original_requirements.get(original_index) == qualified_requirements.get(index) == projected:
                    witnesses['prepare_return']['indexed_shard_assignment'] = {
                        'required_shard_count': len(required_shards),
                        'assigned_shard_count': len(set(fields['files'])),
                        'required_shards_assigned': set(required_shards).issubset(fields['files']),
                    }
        if witnesses:
            closed = markers.get((pid, ticks))
            if closed is not None and all(selected[key]['process_sha256'] == closed[0] for key in witnesses):
                witnesses['journal_terminal'] = closed[1]
            output.append({'rank': rank, 'worker_pid': pid, 'start_ticks': ticks, **witnesses})
    return {'expected_ranks': list(expected_ranks), 'ranks': sorted(output, key=lambda row: row['rank'])}


def replay_input(pointer, env, *, record, pod, scope, mounted_source=None, require_finalized=False):
    from guard.update_validation import _plain_path

    if (type(pointer) is not dict or pointer.keys() != {'path', 'sha256', 'page_archive'}
            or not gpu_common_inputs._check(pointer['sha256'], 'sha')
            or any(type(pointer[key]) is not str or not pointer[key] for key in ('path', 'page_archive'))):
        raise ValueError(_INVALID_CAPTURE)

    def document(path, digest, limit):
        try:
            path = _plain_path(Path(path))
            raw = read_regular(path, limit=limit, expected_sha256=digest)
        except (OSError, ValueError):
            raise ValueError(_CHANGED) from None
        return path, strict_json(raw, limit=limit)

    _, wrapper = document(pointer['path'], pointer['sha256'], CAPTURE_LIMIT)
    if (type(wrapper) is not dict or wrapper.get('schema') != CAPTURE_SCHEMA
            or wrapper.get('session') != scope['session'] or type(wrapper.get('records')) is not dict):
        raise ValueError(_INVALID_CAPTURE)
    entry = wrapper['records'].get('worker-shard-load')
    if (type(entry) is not dict or entry.keys() != {'record', 'validation'}
            or canonical_json(entry['record']) != canonical_json(record)):
        raise ValueError(_PAGE_MISMATCH)

    verified = verify_saved_pages(wrapper, Path(pointer['page_archive']), document, record=record,
                                  epochs=captured_process_epochs(wrapper), pod=pod, scope=scope,
                                  version=env.engine.version, expected_ranks=expected_worker_ranks(env),
                                  mounted_source=mounted_source, require_finalized=require_finalized)
    return {'capture_sha256': pointer['sha256'], **verified} if verified is not None else None
