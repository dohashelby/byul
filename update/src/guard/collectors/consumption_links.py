"""Actual CPU engine request and auxiliary consumption connections.

The input is an EXAMPLE lab capture. Links require exact ID assignment, actual
transport payload equality, source-qualified closed calls and worker state.
No alias, time window, same-process token across PIDs, or caller verified flag
can establish a link. GPU/weights contents/worker authorization remain unknown.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path

from guard.adapters import vllm_execution_link_callback as callback
from guard.adapters import vllm_auxiliary_link_callback as auxiliary
from guard.adapters import vllm_auxiliary_read as auxiliary_read

SCHEMA = 'guard-consumption-links-cpu/1'
MAX_ROWS = 4096
_BASE = {'pid', 'start_ticks', 'rank'}
_FIELDS = {
    'InputProcessor.process_inputs': {'request_sha256', 'params', 'core_input_sha256', 'prompt_sha256', 'tokenizer_sha256', 'mm_features_sha256'},
    'InputProcessor.assign_request_id': {'request_sha256', 'external_sha256', 'core_input_sha256'},
    'Scheduler.add_request': {'request_sha256', 'request_object_sha256', 'preemptions', 'params', 'mm_features_sha256'},
    'Scheduler._free_request': {'request_sha256', 'request_object_sha256', 'preemptions', 'params', 'mm_features_sha256'},
    'Scheduler.schedule': {'schedule_sha256', 'request_sha256s', 'attempts'},
    'GPUModelRunner._update_states': {'schedule_sha256', 'requests'},
    'GPUModelRunner._model_forward': {'schedule_sha256', 'execution_parent_verified', 'requests', 'hf_config_sha256', 'model_sha256', 'inputs_embeds_present', 'cpu_parameter', 'inputs_embeds_object_sha256', 'selected_embeddings_sha256'},
    'GPUModelRunner._preprocess': {'schedule_sha256', 'execution_parent_verified', 'requests', 'hf_config_sha256', 'model_sha256', 'inputs_embeds_object_sha256', 'selected_embeddings_sha256'},
    'GPUModelRunner.reload_weights': {'model_sha256', 'cpu_parameter', 'iterator_supplied', 'checkpoint_format'},
    'GPUModelRunner._gather_mm_embeddings': {'schedule_sha256', 'execution_parent_verified', 'requests', 'hf_config_sha256', 'model_sha256', 'embedding_shapes', 'mask_tokens', 'gathered_embeddings_sha256'},
    'GPUModelRunner._sample': {'schedule_sha256', 'execution_parent_verified', 'requests'},
    'Sampler.forward': {'schedule_sha256', 'execution_parent_verified', 'requests', 'metadata_same_as_batch', 'logits_same_as_model_output', 'metadata', 'sampled_shape', 'grammar_present'},
    'apply_grammar_bitmask': {'schedule_sha256', 'execution_parent_verified', 'requests', 'logits_same_as_model_output', 'grammar_present'},
}


def producer():
    digest = hashlib.sha256()
    for path in (Path(callback.__file__), Path(auxiliary.__file__), Path(auxiliary_read.__file__)):
        digest.update(path.name.encode() + b'\0' + path.read_bytes() + b'\0')
    digest.update(b'CPU_RUNNER\0' + CPU_RUNNER.encode())
    return {'name': 'guard-consumption-links-cpu', 'version': '2', 'source_sha256': digest.hexdigest()}


def _hex(value, length=64):
    return type(value) is str and len(value) == length and all(c in '0123456789abcdef' for c in value)


def _safe(value, depth=0):
    if depth > 7:
        return False
    if value is None or type(value) is bool:
        return True
    if type(value) is int:
        return abs(value) < 2**63
    if type(value) is float:
        return math.isfinite(value) and abs(value) < 2**63
    if type(value) is str:
        return _hex(value)
    if type(value) is list:
        return len(value) <= 256 and all(_safe(v, depth + 1) for v in value)
    if type(value) is dict:
        return len(value) <= 32 and all(type(k) is str and k.replace('_', '').isalnum() and _safe(v, depth + 1) for k, v in value.items())
    return False


def _ref(row):
    return {'process_sha256': row['process_sha256'], 'call_sha256': row['call_sha256'], 'point': row['point'], 'phase': row['phase']}


def _process(row):
    f = row['fields']
    return row['process_sha256'], f['pid'], f['start_ticks']


def _fields_valid(point, f):
    if not _BASE <= set(f) or set(f) - (_BASE | _FIELDS[point]):
        return False
    if f['rank'] is not None and (type(f['rank']) is not int or f['rank'] < 0):
        return False
    if 'requests' in f and (type(f['requests']) is not list or any(type(v) is not dict or set(v) != {'request_sha256', 'state_sha256', 'params', 'num_computed_tokens', 'mm_features_sha256'}
                       or not _hex(v['request_sha256']) or not _hex(v['state_sha256']) or type(v['params']) is not dict
                       or (v['mm_features_sha256'] is not None and not _hex(v['mm_features_sha256']))
                       or type(v['num_computed_tokens']) is not int or v['num_computed_tokens'] < 0 for v in f['requests'])):
        return False
    if 'request_sha256s' in f and (type(f['request_sha256s']) is not list or not all(_hex(v) for v in f['request_sha256s'])):
        return False
    if 'attempts' in f and (type(f['attempts']) is not list or any(type(v) is not dict or set(v) != {'request_sha256', 'preemptions', 'request_object_sha256'}
                         or not _hex(v['request_sha256']) or type(v['preemptions']) is not int or v['preemptions'] < 0
                         or not _hex(v['request_object_sha256']) for v in f['attempts'])):
        return False
    parameter = f.get('cpu_parameter')
    if parameter is not None and (type(parameter) is not dict or set(parameter) != {'name_sha256', 'bytes', 'bytes_sha256'}
                                  or not _hex(parameter['name_sha256']) or not _hex(parameter['bytes_sha256'])
                                  or type(parameter['bytes']) is not int or not 0 < parameter['bytes'] <= 256 * 1024**2):
        return False
    return True


def _metadata(row, index):
    value = row['fields'].get('metadata')
    if type(value) is not dict:
        return None
    if set(value) != {'cpu_only', 'temperature', 'top_p', 'top_k', 'all_greedy', 'no_penalties', 'frequency_penalty', 'presence_penalty', 'repetition_penalty', 'allowed_tokens', 'allowed_token_ids_sha256'} or value['cpu_only'] is not True:
        return None
    def at(name, default):
        items = value.get(name)
        return default if items is None else items[index] if type(items) is list and index < len(items) else None
    def milli(v):
        return round(v * 1000) if type(v) in (int, float) else None
    def rounded(v):
        return round(v, 6) if type(v) in (int, float) else None
    return {'temperature_milli': milli(at('temperature', 0)) if value.get('all_greedy') is not True else 0,
            'top_p_milli': milli(at('top_p', 1)), 'top_k': at('top_k', 0),
            'frequency_penalty': rounded(at('frequency_penalty', 0)),
            'presence_penalty': rounded(at('presence_penalty', 0)),
            'repetition_penalty': rounded(at('repetition_penalty', 1)),
            'allowed_tokens': at('allowed_tokens', 0), 'allowed_token_ids_sha256': at('allowed_token_ids_sha256', None)}


def project(record, *, allowed_auxiliary_sha256=None):
    return _project(record, expected_producer=producer(), cases=('normal', 'allowed', 'normal-after-allowed', 'top-k', 'penalties', 'grammar', 'transition'),
                    allowed_auxiliary_sha256=allowed_auxiliary_sha256)


def _project(record, *, expected_producer, cases, allowed_auxiliary_sha256=None, multimodal=False):
    if allowed_auxiliary_sha256 is not None and (type(allowed_auxiliary_sha256) is not list or not all(_hex(v) for v in allowed_auxiliary_sha256)):
        raise ValueError('CONSUMPTION_LINK_AUXILIARY_POLICY_INVALID')
    keys = {'schema', 'producer', 'data_origin', 'session', 'pod', 'prepared_sha256', 'source_ended', 'rows', 'outputs'}
    if (type(record) is not dict or set(record) != keys or record['schema'] != SCHEMA or record['producer'] != expected_producer
            or record['data_origin'] != 'EXAMPLE' or not _hex(record['session'], 32) or not _hex(record['prepared_sha256'])
            or type(record['source_ended']) is not bool or type(record['rows']) is not list or len(record['rows']) > MAX_ROWS
            or type(record['outputs']) is not list or len(record['outputs']) > 32
            or type(record['pod']) is not dict or set(record['pod']) != {'pod_uid', 'container_id', 'restart_count', 'image'}
            or any(type(record['pod'][k]) is not str or not record['pod'][k] for k in ('pod_uid', 'container_id', 'image'))
            or type(record['pod']['restart_count']) is not int or record['pod']['restart_count'] < 0):
        raise ValueError('CONSUMPTION_LINK_ENVELOPE_INVALID')
    diagnostics, calls, aux = set(), defaultdict(list), []
    session_sha = hashlib.sha256(record['session'].encode()).hexdigest()
    for row in record['rows']:
        if type(row) is not dict:
            diagnostics.add('CONSUMPTION_LINK_ROW_INVALID'); continue
        if row.get('phase') == 'GAP':
            code = row.get('code')
            diagnostics.add(code if type(code) is str and code.replace('_', '').isalnum() and len(code) < 128 else 'CONSUMPTION_LINK_SOURCE_GAP')
            continue
        if row.get('schema') == auxiliary.SCHEMA:
            aux.append(row); continue
        point = row.get('point')
        module = next((name for name, points in callback.TARGETS.items() if point in points), None)
        f = row.get('fields')
        if (row.get('schema') != callback.SCHEMA or module is None or row.get('source_file_sha256') != callback.SOURCES[module]
                or row.get('session_sha256') != session_sha or row.get('runtime_context_injected') is not False
                or row.get('entry_qualified_before') is not True or row.get('phase') not in ('CALL', 'RETURN', 'RAISE')
                or (row['phase'] != 'CALL' and row.get('entry_qualified_after') is not True)
                or not all(_hex(row.get(k)) for k in ('process_sha256', 'call_sha256', 'entry_code_sha256'))
                or type(f) is not dict or not _safe(f) or type(f.get('pid')) is not int or f['pid'] < 1
                or type(f.get('start_ticks')) is not int or f['start_ticks'] < 0 or not _fields_valid(point, f)):
            diagnostics.add('CONSUMPTION_LINK_ROW_UNQUALIFIED'); continue
        calls[(row['process_sha256'], row['call_sha256'], point)].append(row)
    pairs, terminals = [], []
    for rows in calls.values():
        if (len(rows) == 2 and {r['phase'] for r in rows} == {'CALL', 'RAISE'}
                and len({_process(r) for r in rows}) == 1 and len({r['entry_code_sha256'] for r in rows}) == 1):
            terminals.append((next(r for r in rows if r['phase'] == 'CALL'), next(r for r in rows if r['phase'] == 'RAISE')))
            continue
        if (len(rows) != 2 or {r['phase'] for r in rows} != {'CALL', 'RETURN'}
                or len({_process(r) for r in rows}) != 1 or len({r['entry_code_sha256'] for r in rows}) != 1):
            diagnostics.add('CONSUMPTION_LINK_CALL_NOT_CLOSED'); continue
        pairs.append((next(r for r in rows if r['phase'] == 'CALL'), next(r for r in rows if r['phase'] == 'RETURN')))
        terminals.append(pairs[-1])
    def closed(point):
        return [(a, b) for a, b in pairs if b['point'] == point]
    summaries, candidates, seen = [], [], set()
    for output in record['outputs']:
        if (type(output) is not dict or set(output) != {'case', 'request_sha256', 'output_tokens', 'allowed_output_matched', 'normal_output_matched', 'grammar_output_matched'}
                or output.get('case') not in cases
                or not _hex(output.get('request_sha256')) or output['request_sha256'] in seen
                or type(output.get('output_tokens')) is not int or not 0 <= output['output_tokens'] <= (4 if output['case'] == 'transition' else 2)
                or any(output[k] is not None and type(output[k]) is not bool for k in ('allowed_output_matched', 'normal_output_matched', 'grammar_output_matched'))):
            diagnostics.add('CONSUMPTION_LINK_OUTPUT_INVALID'); continue
        external = output['request_sha256']; seen.add(external); missing = []
        api = [b for _, b in closed('InputProcessor.process_inputs') if b['fields'].get('request_sha256') == external]
        assigned = [(a, b) for a, b in closed('InputProcessor.assign_request_id') if b['fields'].get('external_sha256') == external]
        summary = {'case': output['case'], 'external_sha256': external, 'internal_sha256': None, 'steps': [], 'missing': missing,
                   'request_chain_observed': False, 'config_reads_linked': [], 'tokenizer_reads_linked': [],
                   'config_content_match_observed': False, 'tokenizer_copy_lineage_observed': False, 'multimodal_embedding_links': []}
        summaries.append(summary)
        if len(api) != 1 or len(assigned) != 1:
            missing.append('unique_api_assignment_missing'); continue
        api = api[0]; assignment_call, assignment = assigned[0]; internal = assignment['fields'].get('request_sha256')
        if (not _hex(internal) or _process(api) != _process(assignment)
                or assignment_call['fields'].get('request_sha256') != external
                or not _hex(api['fields'].get('core_input_sha256'))
                or api['fields']['core_input_sha256'] != assignment['fields'].get('core_input_sha256')
                or api['fields']['core_input_sha256'] != assignment_call['fields'].get('core_input_sha256')):
            missing.append('api_assignment_input_mismatch'); continue
        summary.update(internal_sha256=internal, api_ref=_ref(api), assignment_ref=_ref(assignment))
        admitted = [b for _, b in closed('Scheduler.add_request') if b['fields'].get('request_sha256') == internal]
        if len(admitted) != 1:
            missing.append('unique_admission_missing'); continue
        admission = admitted[0]; original_params = api['fields'].get('params')
        if not isinstance(original_params, dict) or original_params != admission['fields'].get('params'):
            candidates.append({'case': output['case'], 'reason': 'api_admission_options_changed'})
        forwards = [b for _, b in closed('GPUModelRunner._model_forward') if any(r.get('request_sha256') == internal for r in b['fields'].get('requests', []))]
        for forward in forwards:
            f = forward['fields']; wire = f.get('schedule_sha256'); worker = _process(forward)
            schedules = [b for _, b in closed('Scheduler.schedule') if b['fields'].get('schedule_sha256') == wire
                         and internal in b['fields'].get('request_sha256s', []) and _process(b) == _process(admission)]
            updates = [b for _, b in closed('GPUModelRunner._update_states') if b['fields'].get('schedule_sha256') == wire and _process(b) == worker]
            samples = [(a, b) for a, b in closed('Sampler.forward') if b['fields'].get('schedule_sha256') == wire and _process(b) == worker]
            if not _hex(wire) or f.get('execution_parent_verified') is not True or len(schedules) != 1 or len(updates) != 1 or len(samples) != 1:
                missing.append('unique_transport_worker_sample_link_missing'); continue
            scheduled = schedules[0]; update = updates[0]; sample_call, sample = samples[0]
            attempt = [r for r in scheduled['fields'].get('attempts', []) if r.get('request_sha256') == internal]
            state = [r for r in f.get('requests', []) if r.get('request_sha256') == internal]
            updated = [r for r in update['fields'].get('requests', []) if r.get('request_sha256') == internal]
            sampled = [(i, r) for i, r in enumerate(sample_call['fields'].get('requests', [])) if r.get('request_sha256') == internal]
            if (len(attempt) != 1 or len(state) != 1 or len(updated) != 1 or len(sampled) != 1
                    or attempt[0].get('request_object_sha256') != admission['fields'].get('request_object_sha256')
                    or type(attempt[0].get('preemptions')) is not int or attempt[0]['preemptions'] < 0
                    or not _hex(state[0].get('state_sha256')) or updated[0].get('state_sha256') != state[0]['state_sha256']
                    or sampled[0][1].get('state_sha256') != state[0]['state_sha256']):
                missing.append('admission_attempt_worker_state_mismatch'); continue
            if (sample_call['fields'].get('metadata_same_as_batch') is not True or sample_call['fields'].get('logits_same_as_model_output') is not True
                    or sample_call['fields'].get('execution_parent_verified') is not True or sample['fields'].get('sampled_shape') != [len(sample_call['fields']['requests']), 1]):
                missing.append('actual_model_logits_metadata_sample_unverified'); continue
            if original_params and original_params.get('structured_outputs_present'):
                grammar = [b for _, b in closed('apply_grammar_bitmask') if b['fields'].get('schedule_sha256') == wire and _process(b) == worker]
                if (len(grammar) != 1 or sample_call['fields'].get('grammar_present') is not True
                        or grammar[0]['fields'].get('execution_parent_verified') is not True
                        or grammar[0]['fields'].get('logits_same_as_model_output') is not True or grammar[0]['fields'].get('grammar_present') is not True):
                    missing.append('actual_grammar_application_unverified'); continue
            metadata = _metadata(sample_call, sampled[0][0])
            if (metadata is None or any(v is None for k, v in metadata.items() if k != 'allowed_token_ids_sha256')
                    or not isinstance(original_params, dict)):
                missing.append('sampler_metadata_missing'); continue
            if (any(original_params.get(k) != v for k, v in metadata.items()) or state[0].get('params') != original_params
                    or updated[0].get('params') != original_params or sampled[0][1].get('params') != original_params):
                candidates.append({'case': output['case'], 'reason': 'options_changed_before_actual_sampler', 'schedule_sha256': wire})
            if multimodal and output['case'] != 'text-recovery' and state[0]['num_computed_tokens'] == 0:
                gathers = [b for _, b in closed('GPUModelRunner._gather_mm_embeddings') if _process(b) == worker and b['fields'].get('schedule_sha256') == wire]
                prepared = [b for _, b in closed('GPUModelRunner._preprocess') if _process(b) == worker and b['fields'].get('schedule_sha256') == wire]
                feature = api['fields'].get('mm_features_sha256')
                if (not _hex(feature) or admission['fields'].get('mm_features_sha256') != feature
                        or state[0].get('mm_features_sha256') != feature or updated[0].get('mm_features_sha256') != feature
                        or len(gathers) != 1 or len(prepared) != 1
                        or not all(r['fields'].get('execution_parent_verified') is True for r in gathers + prepared)):
                    missing.append('actual_multimodal_feature_transport_missing')
                else:
                    g, p = gathers[0]['fields'], prepared[0]['fields']
                    digest = g.get('gathered_embeddings_sha256')
                    if (not _hex(digest) or not g.get('mask_tokens') or not _hex(p.get('inputs_embeds_object_sha256'))
                            or p['inputs_embeds_object_sha256'] != f.get('inputs_embeds_object_sha256') or f.get('inputs_embeds_present') is not True):
                        missing.append('actual_multimodal_model_input_missing')
                    elif p.get('selected_embeddings_sha256') != digest or f.get('selected_embeddings_sha256') != digest:
                        candidates.append({'case': output['case'], 'reason': 'gathered_embeddings_changed_before_actual_model', 'schedule_sha256': wire})
                    else:
                        summary['multimodal_embedding_links'].append({'mm_features_sha256': feature, 'embeddings_sha256': digest,
                            'model_input_object_sha256': p['inputs_embeds_object_sha256'], 'mask_tokens': g['mask_tokens'],
                            'refs': [_ref(v) for v in (gathers[0], prepared[0], forward)]})
            summary['steps'].append({'schedule_sha256': wire, 'attempt': attempt[0]['preemptions'], 'worker_pid': worker[1],
                                     'worker_start_ticks': worker[2], 'worker_process_sha256': worker[0],
                                     'state_sha256': state[0]['state_sha256'], 'hf_config_sha256': f.get('hf_config_sha256'),
                                     'num_computed_tokens': state[0]['num_computed_tokens'],
                                     'model_sha256': f.get('model_sha256'),
                                     'cpu_parameter': f.get('cpu_parameter'),
                                     'refs': [_ref(v) for v in (admission, scheduled, update, forward, sample_call, sample)]})
        if not forwards or len(summary['steps']) != len(forwards):
            missing.append('all_forward_steps_not_linked')
        # These fixed CPU runners disable speculative decoding and sample one
        # output token per forward. A dropped/unqualified step cannot disappear
        # from the selected chain just because another step still matches.
        if len(summary['steps']) != output['output_tokens']:
            missing.append('forward_sample_output_count_mismatch')
        if not any(b['fields'].get('request_sha256') == internal and _process(b) == _process(admission) for _, b in closed('Scheduler._free_request')):
            missing.append('request_finish_missing')
        if output['allowed_output_matched'] is False or output['normal_output_matched'] is False or output['grammar_output_matched'] is False:
            candidates.append({'case': output['case'], 'reason': 'actual_model_output_control_mismatch'})
        summary['request_chain_observed'] = bool(summary['steps']) and not missing
        _auxiliary_links(aux, record['session'], api, summary, diagnostics)
        if allowed_auxiliary_sha256 is not None:
            excluded = {(r['path_sha256'], r['file_sha256']) for r in summary['config_reads_linked'] + summary['tokenizer_reads_linked']
                        if r['file_sha256'] not in allowed_auxiliary_sha256}
            candidates.extend({'case': output['case'], 'reason': 'unapproved_auxiliary_read_linked_to_actual_request',
                               'path_sha256': path, 'file_sha256': digest} for path, digest in sorted(excluded))
    reloads = []
    for call, terminal in terminals:
        if terminal['point'] != 'GPUModelRunner.reload_weights':
            continue
        first, last = call['fields'].get('cpu_parameter'), terminal['fields'].get('cpu_parameter')
        reloads.append({'phase': terminal['phase'], 'process_sha256': terminal['process_sha256'], 'pid': terminal['fields']['pid'],
                        'start_ticks': terminal['fields']['start_ticks'], 'model_sha256': terminal['fields'].get('model_sha256'),
                        'before': first, 'after': last, 'refs': [_ref(call), _ref(terminal)]})
        if (terminal['phase'] == 'RAISE' and isinstance(first, dict) and isinstance(last, dict)
                and first.get('name_sha256') == last.get('name_sha256') and first.get('bytes_sha256') != last.get('bytes_sha256')):
            candidates.append({'reason': 'cpu_parameter_changed_during_failed_reload', 'ref': _ref(terminal)})
    return {'requests': summaries, 'reloads': reloads, 'violation_candidates': candidates, 'diagnostic_codes': sorted(diagnostics),
            'scenario_complete': len(summaries) == len(cases) and {r['case'] for r in summaries} == set(cases),
            'selected_request_chains_complete': bool(summaries) and len(summaries) == len(record['outputs']) and all(r['request_chain_observed'] for r in summaries),
            'selected_auxiliary_links_complete': bool(summaries) and all(r['config_content_match_observed'] and r['tokenizer_copy_lineage_observed'] for r in summaries),
            'selected_multimodal_links_complete': multimodal and bool(summaries) and all(r['request_chain_observed'] and (r['case'] == 'text-recovery' or r['multimodal_embedding_links']) for r in summaries),
            'capture_complete': record['source_ended'] and not diagnostics,
            'source_ended': record['source_ended'],
            'auxiliary_policy_supplied': allowed_auxiliary_sha256 is not None,
            'weights_contents_verified': False, 'kv_kernel_reads_verified': False, 'GPU_verified': False,
            'producer_authenticated': False, 'missing': ['full_weights_atomicity_and_GPU_consumption_unobserved', 'native_tokenizer_reads_unattributed',
                                                     'selected_MM_processor_worker_model_not_run', 'V2_sampler_not_run']}


def _auxiliary_links(rows, session, api, summary, diagnostics):
    accepted = []
    for row in rows:
        if (row.get('session') != session or not _hex(row.get('process_sha256')) or type(row.get('pid')) is not int
                or type(row.get('start_ticks')) is not int or row.get('loader_call') is not None and not _hex(row['loader_call'], 32)):
            diagnostics.add('AUXILIARY_LINK_SCOPE_INVALID'); continue
        if row.get('phase') == 'LOADER_RETURN':
            role = row.get('role'); source = row.get('source_sha256')
            expected = {'config': auxiliary.CONFIG_SHA, 'tokenizer': auxiliary_read.SOURCES['transformers.tokenization_utils_base'], 'tokenizer_cache': auxiliary.TOKENIZER_SHA}.get(role) if type(role) is str else None
            if (source != expected or row.get('qualified_before') is not True or row.get('qualified_after') is not True or expected is None
                    or not _hex(row.get('loader_call'), 32)):
                diagnostics.add('AUXILIARY_LINK_LOADER_UNQUALIFIED'); continue
            if ((role == 'config' and not _hex(row.get('config_sha256')))
                    or (role in ('tokenizer', 'tokenizer_cache') and not _hex(row.get('tokenizer_sha256')))
                    or (role == 'tokenizer_cache' and not _hex(row.get('input_tokenizer_sha256')))):
                diagnostics.add('AUXILIARY_LINK_RESULT_IDENTITY_UNAVAILABLE'); continue
            accepted.append(row)
        elif row.get('phase') == 'READ_RETURN' and row.get('stable') is True and row.get('qualified') is True and row.get('complete') is True:
            source = row.get('source_sha256')
            expected_sources = {auxiliary_read.SOURCES[name] for name, points in auxiliary_read.POINTS.items()
                                if type(row.get('point')) is str and points.get(row['point']) == row.get('role')}
            if (not _hex(source) or source not in expected_sources or not _hex(row.get('file_sha256'))
                    or not _hex(row.get('path_sha256')) or not _hex(row.get('parser_input_sha256')) or not _hex(row.get('call'), 32)):
                diagnostics.add('AUXILIARY_LINK_READ_INVALID'); continue
            accepted.append(row)
    def reads(loader):
        return [{'process_sha256': r['process_sha256'], 'pid': r['pid'], 'start_ticks': r['start_ticks'],
                 'loader_call': r['loader_call'], 'read_call': r['call'], 'file_sha256': r['file_sha256'], 'path_sha256': r['path_sha256']}
                for r in accepted if r.get('phase') == 'READ_RETURN' and r.get('loader_call') == loader.get('loader_call')
                and (r['process_sha256'], r['pid'], r['start_ticks']) == (loader['process_sha256'], loader['pid'], loader['start_ticks'])]
    configs = {s['hf_config_sha256'] for s in summary['steps'] if _hex(s['hf_config_sha256'])}
    summary['config_reads_linked'] = [r for loader in accepted if loader.get('role') == 'config' and loader.get('phase') == 'LOADER_RETURN'
                                      and loader.get('config_sha256') in configs for r in reads(loader)]
    identity = _process(api); tokenizer = api['fields'].get('tokenizer_sha256')
    tokens = {tokenizer} if _hex(tokenizer) else set()
    local = [r for r in accepted if (r['process_sha256'], r['pid'], r['start_ticks']) == identity]
    for _ in range(len(local)):
        earlier = set(tokens)
        tokens.update(r.get('input_tokenizer_sha256') for r in local if r.get('role') == 'tokenizer_cache' and r.get('tokenizer_sha256') in tokens)
        if earlier == tokens:
            break
    summary['tokenizer_reads_linked'] = [r for loader in local if loader.get('phase') == 'LOADER_RETURN' and loader.get('role') == 'tokenizer'
                                        and _hex(loader.get('tokenizer_sha256')) and loader['tokenizer_sha256'] in tokens for r in reads(loader)]
    summary['config_content_match_observed'] = bool(summary['config_reads_linked'])
    summary['tokenizer_copy_lineage_observed'] = bool(summary['tokenizer_reads_linked'])


CPU_RUNNER = r'''
import hashlib,hmac,json,os,sys
os.environ.pop('VLLM_CPU_KVCACHE_SPACE',None)
os.environ['VLLM_ENABLE_V1_MULTIPROCESSING']='0'
from vllm import LLM,SamplingParams
from vllm.sampling_params import StructuredOutputsParams
settings=json.load(open('/guard-lifecycle/engine-callbacks/settings.json'))
secret=bytes.fromhex(settings['secret'])
def fingerprint(value): return hmac.new(secret,b's:'+value.encode(),hashlib.sha256).hexdigest()
def fail_partial_reload(worker):
    import torch
    runner=worker.model_runner
    name,parameter=next(runner.model.named_parameters())
    baseline=parameter.detach().clone()
    worker._guard_cpu_reload_baseline=(name,baseline)
    changed=baseline.clone();changed.view(-1)[0]+=1
    def incoming():
        yield name,changed
        raise RuntimeError('GUARD_CPU_RELOAD_ITERATOR_FAILURE')
    try:
        with torch.no_grad(): runner.reload_weights(weights_iterator=incoming(),is_checkpoint_format=False)
    except RuntimeError as exc:
        if str(exc)=='GUARD_CPU_RELOAD_ITERATOR_FAILURE': return True
        raise
    raise RuntimeError('GUARD_CPU_RELOAD_FAILURE_NOT_OBSERVED')
def restore_parameter(worker):
    import torch
    name,baseline=worker._guard_cpu_reload_baseline
    with torch.no_grad(): worker.model_runner.reload_weights(weights_iterator=iter([(name,baseline)]),is_checkpoint_format=False)
    del worker._guard_cpu_reload_baseline
    return True
llm=LLM(model=json.loads(sys.argv[1]),max_model_len=128,max_num_seqs=1,max_num_batched_tokens=128,enforce_eager=True,
        dtype='float32',distributed_executor_backend='mp',kv_cache_memory_bytes=128*1024*1024)
results=[];baseline=None
try:
    for case,allowed in [('normal',None),('allowed',[2]),('normal-after-allowed',None),('top-k',None),('penalties',None),('grammar',None)]:
        options={'temperature':.7,'top_p':.8,'top_k':1,'seed':123} if case=='top-k' else {'temperature':0,'frequency_penalty':1,'presence_penalty':1,'repetition_penalty':1.2} if case=='penalties' else {'temperature':0}
        if case=='grammar': options['structured_outputs']=StructuredOutputsParams(regex='a')
        params=SamplingParams(max_tokens=2,ignore_eos=True,allowed_token_ids=allowed,**options)
        output=llm.generate('Hello',params,use_tqdm=False)[0]
        tokens=output.outputs[0].token_ids
        if case=='normal': baseline=list(tokens)
        results.append({'case':case,'request_sha256':fingerprint(output.request_id),'output_tokens':len(tokens),
                        'allowed_output_matched':all(t==2 for t in tokens) if allowed else None,
                        'normal_output_matched':list(tokens)==baseline if case=='normal-after-allowed' else None,
                        'grammar_output_matched':output.outputs[0].text.strip()=='a' if case=='grammar' else None})
    request_id='guard-lif06-cpu-transition'
    llm.llm_engine.add_request(request_id,'Hello',SamplingParams(max_tokens=4,temperature=0,ignore_eos=True))
    llm.llm_engine.step()
    if llm.collective_rpc(fail_partial_reload)!=[True]: raise RuntimeError('GUARD_CPU_RELOAD_ACK_MISSING')
    llm.llm_engine.step()
    if llm.collective_rpc(restore_parameter)!=[True]: raise RuntimeError('GUARD_CPU_RESTORE_ACK_MISSING')
    final=[]
    while llm.llm_engine.has_unfinished_requests(): final=llm.llm_engine.step()
    output=next(v for v in final if v.request_id==request_id)
    results.append({'case':'transition','request_sha256':fingerprint(request_id),'output_tokens':len(output.outputs[0].token_ids),
                    'allowed_output_matched':None,'normal_output_matched':None,'grammar_output_matched':None})
finally:
    print('GUARD_CAPTURE_RESULT='+json.dumps(results),flush=True)
'''
