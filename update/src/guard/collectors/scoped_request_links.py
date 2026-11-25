"""Project source-qualified request links within one collected native page range."""

from collections import defaultdict

from guard.adapters import vllm_runner_capture
from guard.adapters.vllm_scoped_consumption_callback import ASSIGNMENT_SCHEMA, COMPLETION_SELECTION_SCHEMA
from guard.models import sha256_hex

from .scoped_consumption import _reviewed_call_in_process, _sha, _uint


def _relations(processes, session_sha256, sources, schema, point, id_field, module, caller):
    observations = defaultdict(list)
    for process in processes:
        epoch = (process['pid'], process['start_ticks'])
        if not (_uint(epoch[0]) and epoch[0] > 0 and _uint(epoch[1]) and epoch[1] > 0):
            continue
        for row in process['events']:
            if row.get('schema') != schema or row.get('point') != point or row.get('phase') != 'RETURN':
                continue
            scope, fields = row.get('consumption_scope'), row.get('fields')
            started, finished = row.get('operation_started_ns'), row.get('operation_finished_ns')
            if (row.get('session_sha256') != session_sha256 or row.get('source_file_sha256') != sources[module]
                    or row.get('caller_source_file_sha256') != sources[caller]
                    or row.get('entry_qualified_before') is not True or row.get('entry_qualified_after') is not True
                    or row.get('runtime_context_injected') is not False or not _uint(started) or not _uint(finished)
                    or started > finished or type(scope) is not dict or scope.keys() != {'pid', 'start_ticks'}
                    or not all(_uint(scope[key]) for key in scope) or (scope['pid'], scope['start_ticks']) != epoch
                    or type(fields) is not dict or fields.keys() != {'outer_request_sha256', id_field}
                    or not all(_sha(value) for value in fields.values())
                    or any(not _sha(row.get(key)) for key in (
                        'process_sha256', 'call_sha256', 'entry_code_sha256', 'caller_code_sha256'))):
                continue
            observations[fields['outer_request_sha256']].append((epoch, row))
    return observations


def project(processes, ordered_calls, record):
    """Join exact request fingerprints and worker admissions in their observed epochs."""
    qualification = record['qualification_scope']
    sources = vllm_runner_capture.profile(qualification['engine_version']).sources
    session_sha256 = sha256_hex(record['session'].encode())
    selections = _relations(processes, session_sha256, sources, COMPLETION_SELECTION_SCHEMA,
        'BaseServing._log_inputs', 'response_request_sha256',
        'entrypoints/serve/engine/serving.py', 'entrypoints/openai/completion/serving.py')
    assignments = _relations(processes, session_sha256, sources, ASSIGNMENT_SCHEMA,
        'InputProcessor.assign_request_id', 'native_request_sha256',
        'v1/engine/input_processor.py', 'v1/engine/async_llm.py')
    if not selections or not assignments:
        return []

    responses, native_ids = defaultdict(int), defaultdict(int)
    for entries in selections.values():
        for _, row in entries:
            responses[row['fields']['response_request_sha256']] += 1
    for entries in assignments.values():
        for _, row in entries:
            native_ids[row['fields']['native_request_sha256']] += 1

    epochs = {(process['pid'], process['start_ticks']) for process in processes}
    workers = {}
    for _, _, binding, call, end in ordered_calls:
        actor = record['bindings'][binding]
        if actor['role'] != 'worker' or end['phase'] != 'RETURN':
            continue
        epoch = actor['worker_pid'], actor['start_ticks']
        if epoch in epochs and _reviewed_call_in_process(call, end, {'pid': epoch[0], 'start_ticks': epoch[1]}):
            key = binding, call['process_sha256'], call['call_sha256'], call['point']
            workers[key] = call['operation_started_ns'], end['operation_finished_ns']

    admissions = defaultdict(lambda: defaultdict(list))
    for event in record['events']:
        binding, admission = event['binding'], event['admission_sha256']
        source = event['source_call']
        key = binding, source['process_sha256'], source['call_sha256'], source['point']
        window = workers.get(key)
        if window is not None and _sha(admission) and event['event'] in {'runner_received', 'sampler_applied'}:
            group = binding, admission, event['event']
            admissions[event['request_sha256']][group].append((event['seq'], *window))

    links = []
    for outer, selected in selections.items():
        assigned = assignments.get(outer, [])
        if len(selected) != 1 or len(assigned) != 1:
            continue
        (selection_epoch, selection), = selected
        (assignment_epoch, assignment), = assigned
        response = selection['fields']['response_request_sha256']
        native = assignment['fields']['native_request_sha256']
        if (selection_epoch != assignment_epoch or selection['process_sha256'] != assignment['process_sha256']
                or selection['operation_finished_ns'] > assignment['operation_started_ns']
                or responses[response] != 1 or native_ids[native] != 1):
            continue
        stages = admissions[native]
        witnesses = []
        for (binding, admission, kind), events in sorted(stages.items()):
            if kind != 'runner_received' or len(events) != 1:
                continue
            (received, started, finished), = events
            if started < assignment['operation_finished_ns']:
                continue
            sampler = [seq for seq, start, _ in stages.get((binding, admission, 'sampler_applied'), [])
                       if seq > received and start >= finished]
            if sampler:
                witnesses.append({'binding': binding, 'admission_sha256': admission,
                                  'runner_event_seq': received, 'sampler_event_seqs': sampler})
        if witnesses:
            links.append({'response_request_sha256': response, 'outer_request_sha256': outer,
                          'native_request_sha256': native, 'selection_call_sha256': selection['call_sha256'],
                          'assignment_call_sha256': assignment['call_sha256'], 'workers': witnesses})
    return links
