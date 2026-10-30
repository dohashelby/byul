"""Online input CPU capture: one collection, three scoped evidence objects."""
from __future__ import annotations

import argparse
import builtins
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import re
import sys
import time
import yaml

from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.core.select import check_policy_value
from guard.collectors.manual import build_template, dump_template, import_evidence
from guard.input_policy import policy_context
from guard.executors import online_input_cpu as product, registry
from guard.executors import input_cpu as req09_product
from guard.executors.base import DraftArtifact, EvidenceDraft
from guard.executors.http_common import incomplete_artifact_capture
from guard.models import CheckDef, DataOrigin, Environment, ExecStatus, Policy, Mode, load_pack, new_id
from guard.store import Store
from guard.store.bundle import export_bundle, restore_bundle, verify_complete
from guard.store.packs import get_pack, put_pack
from tools import b1_input_cpu as collector
from tools import lif14_profiler_cpu as component_gate

PROFILE = 'online-input-source-cpu-proposal-v1'
SOURCE_PROFILES = {'reference5d': '5d35ce3934a7804b298559d2d99eec33792e1eb9',
                   'target031': 'db9527a46873454610df6dbedf79a36d6bf1a7f6'}
GATE_SHA256 = '3ada5b08959d2935d8cf0d43d60e09eeaca4c0c560eda8accd22da9631f41d75'
COMPONENT_COMMIT = '0d0c116c4a1ed1ef1ba93a80b236bdedd2d73670'
# Reviewed collector snapshot de799c2; runtime bytes never become approval.
COLLECTOR_SHA256 = 'a5030100108bede6cae19c77bbb8474a9e5a7be96d01af8510f49306cc0b5cc8'
CHECKS = ('REQ-07', 'REQ-08', 'SUP-08')
_CASES = {case['id']: case for case in product.fixtures()}
_TARGET_EXCLUDED = {'reuse-empty', 'reuse-bool', 'reuse-echo-ignores-ids'}
_EVENTS = {'render_cmpl', 'render_chat', 'render_chat_async', 'validate_token_ids', 'process_inputs', 'validate_prompt_len',
           'validate_model_input', 'online_render_chat', 'online_preprocess_chat', 'reuse_input_gate',
           'template_gate', 'mm_kwargs_gate'}
_REASONS = {'source_contract_observed', 'source_expectation_unavailable', 'consumption_unavailable', 'token_domain_unavailable', 'token_domain_mismatch'}
_OUTCOMES = {'accepted', 'rejected', 'execution_error', 'unsupported'}
_VALIDATION = {'verified', 'unavailable', 'mismatch'}
_ERROR_TYPES = {name for name, value in vars(builtins).items()
                if isinstance(value, type) and issubclass(value, Exception)}
_CONFIG_FILES = {'config.json', 'tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json',
                 'added_tokens.json', 'chat_template.jinja'}
_BOOL_META = {'request_parsed', 'trust_request_chat_template', 'trust_request_mm_kwargs',
    'processor_input_matches_reuse', 'admission_completed', 'reuse_field_removed', 'sampling_reuse_field_present'}
_INT_META = {'processor_input_count', 'prompt_count', 'effective_max_model_len', 'effective_vocab_size', 'effective_max_token_id'}
_HASH_META = {'processor_input_ids_sha256', 'admitted_token_ids_canonical_sha256'}


def _require_normal_python():
    if sys.flags.optimize or sys.version_info[:2] != (3, 12):
        raise ValueError('ONLINE_INPUT_CPU_REQUIRES_NORMAL_PYTHON312')


def _source_commit(profile):
    if not isinstance(profile, str) or profile not in SOURCE_PROFILES:
        raise ValueError('ONLINE_INPUT_CPU_SOURCE_PROFILE_UNSUPPORTED')
    return SOURCE_PROFILES[profile]


def verify_runtime_components(repo):
    gate_file = repo / 'update/tools/lif14_profiler_cpu.py'
    collector_file = repo / 'update/tools/b1_input_cpu.py'
    try:
        if (gate_file.is_symlink() or not Path(component_gate.__file__).resolve().samefile(gate_file)
                or not Path(inspect.getsourcefile(component_gate.verify_component)).resolve().samefile(gate_file)
                or hashlib.sha256(gate_file.read_bytes()).hexdigest() != GATE_SHA256
                or component_gate.PRODUCT_COMPONENT_COMMIT != COMPONENT_COMMIT):
            raise ValueError
    except (OSError, TypeError, AttributeError, ValueError):
        raise ValueError('ONLINE_INPUT_CPU_COMPONENT_GATE_IDENTITY_MISMATCH') from None
    if not isinstance(COLLECTOR_SHA256, str) or re.fullmatch('[0-9a-f]{64}', COLLECTOR_SHA256) is None:
        raise ValueError('ONLINE_INPUT_CPU_COLLECTOR_PIN_UNCONFIGURED')
    try:
        if (collector_file.is_symlink() or not Path(collector.__file__).resolve().samefile(collector_file)
                or not Path(inspect.getsourcefile(collector.capture_online)).resolve().samefile(collector_file)
                or hashlib.sha256(collector_file.read_bytes()).hexdigest() != COLLECTOR_SHA256):
            raise ValueError
    except (OSError, TypeError, AttributeError, ValueError):
        raise ValueError('ONLINE_INPUT_CPU_COLLECTOR_IDENTITY_OR_BYTES_MISMATCH') from None
    component_gate.verify_component(repo)


def _validation(value):
    value = value if isinstance(value, dict) else {}
    post = value.get('post') if isinstance(value.get('post'), dict) else {}
    pre_status, post_status = value.get('pre'), post.get('status')
    safe = {'pre': pre_status if isinstance(pre_status, str) and pre_status in _VALIDATION else 'unavailable',
        'post': {'status': post_status if isinstance(post_status, str) and post_status in _VALIDATION else 'unavailable'},
        'boundary': 'source_fixture_component' if value.get('boundary') == 'source_fixture_component' else 'unavailable'}
    if 'error_type' in post:
        safe['post']['error_type'] = post['error_type'] if isinstance(post['error_type'], str) and post['error_type'] in _ERROR_TYPES else 'Exception'
    return safe


def _events(value):
    return sorted({item for item in value if isinstance(item, str) and item in _EVENTS}) if isinstance(value, list) else []


def _digest(value):
    return product.digest(value)


def _sha256(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _token_domain(value):
    if not isinstance(value, dict):
        return {'status': 'unavailable'}
    safe = {'status': 'collected' if value.get('status') == 'collected' else 'unavailable'}
    if safe['status'] == 'unavailable':
        if 'error_type' in value:
            error = value['error_type']
            safe['error_type'] = error if isinstance(error, str) and error in _ERROR_TYPES else 'Exception'
        return safe
    for key in ('model_vocab_size', 'tokenizer_id_count', 'min', 'max'):
        if type(value.get(key)) is int and value[key] >= 0:
            safe[key] = value[key]
    for key in ('contiguous_from_zero', 'request_ids_in_domain', 'request_ids_within_model_vocab'):
        if type(value.get(key)) is bool:
            safe[key] = value[key]
    for key in ('canonical_sha256', 'request_ids_canonical_sha256', 'tokenizer_file_sha256', 'model_config_file_sha256'):
        if _sha256(value.get(key)):
            safe[key] = value[key]
    return safe


def _independent_oov(metadata, ids, config_hashes):
    """The owned tokenizer/model files establish the domain, independent of its cached bound."""
    proof = metadata.get('token_id_domain', {})
    if proof.get('status') != 'collected':
        return False, 'token_domain_unavailable'
    hashes = config_hashes if isinstance(config_hashes, dict) else {}
    expected = {'model_vocab_size': 32, 'tokenizer_id_count': 32, 'min': 0, 'max': 31,
        'canonical_sha256': _digest(list(range(32))), 'contiguous_from_zero': True,
        'request_ids_in_domain': False, 'request_ids_within_model_vocab': False,
        'request_ids_canonical_sha256': _digest(ids)}
    matches = (all(proof.get(key) == value for key, value in expected.items())
        and _sha256(hashes.get('tokenizer.json')) and _sha256(hashes.get('config.json'))
        and proof.get('tokenizer_file_sha256') == hashes['tokenizer.json']
        and proof.get('model_config_file_sha256') == hashes['config.json'])
    return bool(matches), 'source_contract_observed' if matches else 'token_domain_mismatch'


def _safe_metadata(value, case):
    value = value if isinstance(value, dict) else {}
    safe = {key: value[key] for key in _BOOL_META if type(value.get(key)) is bool}
    safe.update({key: value[key] for key in _INT_META if type(value.get(key)) is int and value[key] >= 0})
    safe.update({key: value[key] for key in _HASH_META if _sha256(value.get(key))})
    if 'token_id_domain' in value:
        safe['token_id_domain'] = _token_domain(value['token_id_domain'])
    parsed = value.get('parsed_fields') if isinstance(value.get('parsed_fields'), dict) else {}
    safe['parsed_fields'] = {}
    for field in case['fields']:
        item = parsed.get(field) if isinstance(parsed.get(field), dict) else {}
        safe['parsed_fields'][field] = {'present': item.get('present') is True,
            'fixture_matches': item.get('fixture_matches') is True,
            'canonical_sha256': item.get('canonical_sha256') if _sha256(item.get('canonical_sha256')) else None}
    if value.get('rejection_basis') == 'native_typed_error_response':
        safe['rejection_basis'] = 'native_typed_error_response'
    if type(value.get('native_error_code')) is int and value['native_error_code'] in (400, 422):
        safe['native_error_code'] = value['native_error_code']
    return safe


def _describe_matches(row, case):
    spec = product.describe(case)
    return all(row.get(key) == spec[key] for key in ('check_id', 'stage', 'fixture_canonical_sha256', 'expected'))


def _admission(metadata):
    return (metadata.get('admission_completed') is True and type(metadata.get('prompt_count')) is int
            and metadata['prompt_count'] >= 0 and _sha256(metadata.get('admitted_token_ids_canonical_sha256')))


def _consumed_ids(metadata, ids):
    digest = _digest(ids)
    return (_admission(metadata) and metadata.get('processor_input_matches_reuse') is True
        and metadata.get('processor_input_ids_sha256') == digest and metadata.get('processor_input_count') == len(ids)
        and metadata['admitted_token_ids_canonical_sha256'] == digest and metadata['prompt_count'] == len(ids))


def _literal_override_consumed(metadata):
    # Owned WordLevel fixture: hello=3, world=4; the normal chat also contains user=5.
    digest = _digest([3, 4])
    return (_admission(metadata) and metadata.get('processor_input_ids_sha256') == digest
        and metadata.get('processor_input_count') == 2 and metadata['admitted_token_ids_canonical_sha256'] == digest
        and metadata['prompt_count'] == 2)


def _scope_eligible(row, case, profile, config_hashes=None):
    if profile == 'target031' and case['id'] in _TARGET_EXCLUDED:
        return False, 'source_expectation_unavailable'
    meta = _safe_metadata(row.get('safe_metadata'), case)
    parsed = meta['parsed_fields']
    fields_match = all(parsed[key]['present'] and parsed[key]['fixture_matches']
        and parsed[key]['canonical_sha256'] == _digest(value) for key, value in case['fields'].items())
    if (not fields_match or meta.get('request_parsed') is not True
            or meta.get('trust_request_chat_template') is not case['trust_template']
            or meta.get('trust_request_mm_kwargs') is not case['trust_mm']
            or meta.get('effective_max_model_len') != 16 or meta.get('effective_vocab_size') != 32):
        return False, 'consumption_unavailable'
    events = set(_events(row.get('events')))
    if row.get('outcome') == 'rejected':
        gates = ({'reuse_input_gate', 'validate_token_ids', 'validate_model_input', 'validate_prompt_len'}
                 if case['check_id'] == 'REQ-08' else
                 {'template_gate', 'validate_prompt_len'} if case['check_id'] == 'REQ-07' else {'mm_kwargs_gate'})
        eligible = 'online_render_chat' in events and bool(gates & events)
    elif row.get('outcome') == 'accepted' and _admission(meta):
        if case['check_id'] == 'REQ-08':
            ids = case['fields']['kv_transfer_params']['prompt_token_ids']
            exact_input = _consumed_ids(meta, ids)
            if case['id'] == 'reuse-oov':
                domain_oov, reason = _independent_oov(meta, ids, config_hashes)
                if not domain_oov:
                    return False, reason
                # A gate trace cannot replace actual consumption of the fixed OOV request.
                return bool(exact_input), 'source_contract_observed' if exact_input else 'consumption_unavailable'
            if case['id'] == 'reuse-long':
                exact_input = exact_input and len(ids) >= meta['effective_max_model_len']
            eligible = exact_input or (profile == 'reference5d' and 'reuse_input_gate' in events)
        elif case['check_id'] == 'REQ-07':
            eligible = ('template_gate' in events
                or (case['id'] in {'template-untrusted', 'template-nested-untrusted'} and _literal_override_consumed(meta))
                or (case['id'] == 'kwargs-trusted-overbound' and meta['prompt_count'] >= meta['effective_max_model_len']))
        else:
            eligible = 'mm_kwargs_gate' in events
    else:
        eligible = False
    return bool(eligible), 'source_contract_observed' if eligible else 'consumption_unavailable'


def _outcome(value):
    return value if isinstance(value, str) and value in _OUTCOMES else 'unknown'


def _utc(value):
    if not isinstance(value, str):
        raise ValueError('ONLINE_INPUT_CPU_CAPTURE_TIME_INVALID')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset().total_seconds() != 0:
        raise ValueError('ONLINE_INPUT_CPU_CAPTURE_TIME_INVALID')
    return result


def _safe_rows(rows, profile, config_hashes):
    safe = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('case_id'), str) or row['case_id'] not in _CASES:
            continue
        case = _CASES[row['case_id']]
        declared = row.get('source_scope_eligibility') if isinstance(row.get('source_scope_eligibility'), dict) else {}
        eligible, reason = _scope_eligible(row, case, profile, config_hashes)
        after = row.get('after') if isinstance(row.get('after'), dict) else {}
        safe.append({'case_id': row['case_id'], 'check_id': row.get('check_id') if row.get('check_id') in CHECKS else None,
            'stage': row.get('stage') if row.get('stage') == product.describe(case)['stage'] else 'unknown',
            'expected': row.get('expected') if row.get('expected') in ('accepted', 'rejected', None) else None,
            'fixture_canonical_sha256': row.get('fixture_canonical_sha256') if _sha256(row.get('fixture_canonical_sha256')) else None,
            'outcome': _outcome(row.get('outcome')), 'events': _events(row.get('events')),
            'target_started': row.get('target_started') is True,
            'source_profile': row.get('source_profile') if isinstance(row.get('source_profile'), str) and row['source_profile'] in SOURCE_PROFILES else None,
            'source_commit': row.get('source_commit') if row.get('source_commit') in SOURCE_PROFILES.values() else None,
            'collection_id': row.get('collection_id') if isinstance(row.get('collection_id'), str) and re.fullmatch('online-cpu-[0-9a-f]{32}', row['collection_id']) else None,
            'source_validation': _validation(row.get('source_validation')), 'safe_metadata': _safe_metadata(row.get('safe_metadata'), case),
            'source_scope_eligibility': {'eligible': declared.get('eligible') is True,
                'reason': declared.get('reason') if declared.get('reason') in _REASONS else 'consumption_unavailable'},
            'producer_scope_eligibility': {'eligible': eligible, 'reason': reason},
            'after': {'outcome': _outcome(after.get('outcome')), 'events': _events(after.get('events')),
                      'safe_metadata': _safe_metadata(after.get('safe_metadata'), {**case, 'fields': {}})}})
        if row.get('preparation_status') in ('completed', 'failed'):
            safe[-1]['preparation_status'] = row['preparation_status']
    return safe


def _source_bound(row, profile, collection_id):
    case = _CASES.get(row.get('case_id')) if isinstance(row.get('case_id'), str) else None
    validation = _validation(row.get('source_validation'))
    return (case is not None and _describe_matches(row, case) and row.get('target_started') is True
        and row.get('source_profile') == profile and row.get('source_commit') == _source_commit(profile)
        and row.get('collection_id') == collection_id and validation['pre'] == 'verified'
        and validation['boundary'] == 'source_fixture_component' and validation['post']['status'] in {'verified', 'unavailable'})


def _project(rows, profile, collection_id, config_hashes):
    qualified, decisions = [], {}
    counts = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get('case_id'), str):
            counts[row['case_id']] = counts.get(row['case_id'], 0) + 1
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('case_id'), str) or row['case_id'] not in _CASES:
            continue
        key, case = row['case_id'], _CASES[row['case_id']]
        eligible, reason = _scope_eligible(row, case, profile, config_hashes)
        declared = row.get('source_scope_eligibility') if isinstance(row.get('source_scope_eligibility'), dict) else {}
        bound = _source_bound(row, profile, collection_id) and counts[key] == 1
        eligible = bound and eligible and declared.get('eligible') is True
        decisions[key] = {'eligible': bool(eligible), 'reason': reason if bound else 'consumption_unavailable'}
        if eligible:
            qualified.append(deepcopy(row))
    return product.project(qualified), qualified, decisions


def _validated_record(raw, profile, producer_sha256):
    if (not isinstance(raw, dict) or raw.get('source_profile') != profile or raw.get('source_commit') != _source_commit(profile)
            or raw.get('own_producer_sha256') != producer_sha256 or raw.get('installed_target') is not None
            or raw.get('capture_status') not in ('completed', 'incomplete') or not isinstance(raw.get('results'), list)
            or not isinstance(raw.get('collection_id'), str) or re.fullmatch('online-cpu-[0-9a-f]{32}', raw['collection_id']) is None):
        raise ValueError('ONLINE_INPUT_CPU_RECORD_ATTRIBUTION_MISMATCH')
    validation = _validation(raw.get('source_validation'))
    if validation['pre'] != 'verified' or validation['boundary'] != 'source_fixture_component':
        raise ValueError('ONLINE_INPUT_CPU_RECORD_PRECHECK_UNAVAILABLE')
    hashes = raw.get('config_file_bytes_sha256')
    if (not isinstance(hashes, dict) or not hashes or any(name not in _CONFIG_FILES or not _sha256(value)
            for name, value in hashes.items()) or 'config.json' not in hashes):
        raise ValueError('ONLINE_INPUT_CPU_CONFIG_HASH_INVALID')
    fixture = raw.get('fixture_config')
    if not isinstance(fixture, dict) or any(fixture.get(key) != value for key, value in
        {'origin': 'DECLARED', 'vocab_size': 32, 'max_model_len': 16, 'hidden_size': 4}.items()):
        raise ValueError('ONLINE_INPUT_CPU_FIXTURE_CONFIG_MISMATCH')
    started, finished = _utc(raw.get('capture_started_at')), _utc(raw.get('capture_finished_at'))
    if finished < started:
        raise ValueError('ONLINE_INPUT_CPU_CAPTURE_TIME_INVALID')
    aux = raw.get('auxiliary_write') if isinstance(raw.get('auxiliary_write'), dict) else {}
    auxiliary = {'status': aux.get('status') if aux.get('status') in ('completed', 'failed') else 'failed'}
    if 'error_type' in aux:
        auxiliary['error_type'] = aux['error_type'] if isinstance(aux['error_type'], str) and aux['error_type'] in _ERROR_TYPES else 'Exception'
    return {'profile': PROFILE, 'source_profile': profile, 'source_commit': _source_commit(profile),
        'scope': 'source_cpu_online_renderer_then_admission', 'installed_target': None,
        'runner_hook_verified': False, 'runner_consumption_verified': False, 'collection_id': raw['collection_id'],
        'capture_started_at': started.isoformat(), 'capture_finished_at': finished.isoformat(),
        'capture_status': raw['capture_status'], 'source_validation': validation, 'auxiliary_write': auxiliary,
        'own_producer_sha256': producer_sha256, 'config_file_bytes_sha256': dict(hashes),
        'source_features': {key: {'present': value['present']} for key, value in raw.get('source_features', {}).items()
            if key in _EVENTS and isinstance(value, dict) and type(value.get('present')) is bool},
        'fixture_config': {'origin': 'DECLARED', 'vocab_size': 32, 'max_model_len': 16, 'hidden_size': 4},
        'results': _safe_rows(raw['results'], profile, hashes)}


def _write_artifact(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(record, stream, sort_keys=True, indent=2)


class _SharedCapture:
    """Private per-run binding; EXAMPLE tests inject only an in-memory callable."""
    name, version = 'online_input_cpu', '0.1.0'

    def __init__(self, env, plan_ref, source_root, source_profile, *, capture_fn, producer_sha256, component_check):
        self.env_ref, self.plan_ref, self.origin = env.ref(), plan_ref, env.data_origin
        self.source_root, self.source_profile = Path(source_root), source_profile
        self.capture_fn, self.producer_sha256, self.component_check = capture_fn, producer_sha256, component_check
        self.loaded, self.record, self.observations = False, None, {}
        self.started, self.finished, self.component_ok = None, None, True
        self.collection_id, self.baseline = None, {}
        self._check_binding(env, plan_ref, source_profile)

    def _check_binding(self, env, plan_ref, profile):
        if (not env.hash_ok() or env.ref() != self.env_ref or plan_ref != self.plan_ref
                or profile != self.source_profile or env.data_origin != self.origin or env.engine.name != 'vllm'
                or env.engine.version != 'reference-' + _source_commit(profile)):
            raise ValueError('ONLINE_INPUT_CPU_SHARED_ENVIRONMENT_OR_PLAN_MISMATCH')

    def _load(self, workdir):
        self.loaded = True
        self.started = datetime.now(timezone.utc)
        try:
            raw = deepcopy(self.capture_fn(self.source_root, Path(workdir) / 'owned-online-input-cpu', source_profile=self.source_profile))
            record = _validated_record(raw, self.source_profile, self.producer_sha256)
            projection, qualified, decisions = _project(raw['results'], self.source_profile, record['collection_id'], record['config_file_bytes_sha256'])
            record['source_scope_qualification'] = decisions
            for row in record['results']:
                row['producer_scope_eligibility'] = decisions.get(row['case_id'], {'eligible': False, 'reason': 'consumption_unavailable'})
            self.started, self.finished = _utc(record['capture_started_at']), _utc(record['capture_finished_at'])
            self.collection_id, self.baseline = record['collection_id'], decisions
            self.record, self.observations = record, projection
            by_case = {row.get('case_id'): row for row in raw['results'] if isinstance(row, dict) and isinstance(row.get('case_id'), str)}
            self.complete = (record['capture_status'] == 'completed' and record['source_validation']['post']['status'] == 'verified'
                and record['auxiliary_write']['status'] == 'completed' and set(by_case) == set(_CASES)
                and len(raw['results']) == len(_CASES)
                and all(_source_bound(row, self.source_profile, record['collection_id'])
                    and _validation(row.get('source_validation'))['post']['status'] == 'verified'
                    and row.get('outcome') in ('accepted', 'rejected')
                    and isinstance(row.get('after'), dict) and row['after'].get('outcome') == 'accepted'
                    and _admission(_safe_metadata(row['after'].get('safe_metadata'), {**_CASES[key], 'fields': {}}))
                    and (decisions[key]['eligible'] or self.source_profile == 'target031' and key in _TARGET_EXCLUDED)
                    for key, row in by_case.items()))
        except Exception:
            self.complete = False
            self.finished = datetime.now(timezone.utc)

    def draft(self, check, env, policy_values, workdir, *, plan_ref, source_profile):
        _require_normal_python()
        self._check_binding(env, plan_ref, source_profile)
        if check.id not in CHECKS or policy_values:
            raise ValueError('ONLINE_INPUT_CPU_CHECK_OR_POLICY_UNSUPPORTED')
        try:
            self.component_check()
        except Exception:
            if not self.loaded:
                raise
            self.component_ok = False
        if not self.loaded:
            self._load(workdir)
        try:
            self.component_check()
        except Exception:
            self.component_ok = False
        origin = DataOrigin.EXAMPLE if self.origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED
        config = {'profile': PROFILE, 'source_profile': source_profile, 'source_commit': _source_commit(source_profile),
                  'collection_id': self.collection_id, 'installed_target': None, 'runner_hook_verified': False}
        complete = self.record is not None and self.complete and self.component_ok
        observations = deepcopy(self.observations.get(check.id, {}))
        # The product projection uses [] for an uncollected axis; EVD's existing
        # null contract requires None at this boundary, without changing verdicts.
        for observation in observations.values():
            if observation.get('collected') is False:
                observation['value'] = None
        if self.record is not None:
            observations['online_cpu_cases_complete'] = {'value': bool(complete), 'collected': True}
        draft = EvidenceDraft(status=ExecStatus.COMPLETED if complete else ExecStatus.ABORTED, data_origin=origin,
            normal_control={'attempted': self.record is not None, 'succeeded': bool(complete) if self.record is not None else None},
            observations=observations, config=config,
            challenges=[{'step': step, 'attempted': self.record is not None} for step in check.challenge_steps],
            started_at=self.started, finished_at=self.finished,
            abort_reason=None if complete else ('ONLINE_INPUT_CPU_CAPTURE_INCOMPLETE' if self.record is not None else 'ONLINE_INPUT_CPU_CAPTURE_UNAVAILABLE'))
        if not self.component_ok:
            draft.abort_reason = (draft.abort_reason + '; ' if draft.abort_reason else '') + 'ONLINE_INPUT_CPU_COMPONENT_POSTCHECK_UNAVAILABLE'
        if self.record is not None:
            artifact = Path(workdir) / ('online-input-' + check.id.lower() + '.json')
            try:
                _write_artifact(artifact, {**deepcopy(self.record), 'check_id': check.id})
                draft.artifacts = [DraftArtifact(name=artifact.name, path=artifact, collector=PROFILE)]
            except Exception:
                draft = incomplete_artifact_capture(draft)
        return draft


def proposal_pack(folder, profile):
    _source_commit(profile)
    (folder / 'checks').mkdir(parents=True)
    files = []
    for cid in CHECKS:
        key = 'online_input_confirmed_violations'
        check = CheckDef(id=cid, revision=1, area='SUP' if cid.startswith('SUP') else 'REQ', purpose='추가 코드 실행 허용' if cid.startswith('SUP') else '최종 입력 검증',
            question='선정 source의 OnlineRenderer/request gate와 실제 admission 관측은 일치하는가?',
            review_order='source_cpu_reference_first', applies_when={'always': True},
            normal_control='기존 16개 사례 직후 같은 renderer/processor의 정상 입력 수용',
            challenge_steps=['기존 재사용 ID·template·MM 기본 gate 16개 source CPU 관측'],
            required_observations=[{'key': key, 'type': 'list[str]'}, {'key': 'online_cpu_cases_complete', 'type': 'bool'}],
            satisfied_if='제한된 source CPU 관측; 기업 승인·실제 endpoint/최종 소비는 별도 확인',
            fail_criteria='source별 실제 필드/소비가 확인된 기존 입력 경계 위반', unknown_criteria='필드 지원·소비·귀속 또는 source별 거부 보장 미확인',
            executor=_SharedCapture.name, judge={'plugin': 'rule_table', 'params': product.judge_params()},
            limitations=['소유 local config CPU 직접 호출. 기업 한도·API 신원·MM 코드/최종 worker 소비 미검증.'])
        body = yaml.safe_dump(check.model_dump(mode='json'), allow_unicode=True, sort_keys=False).encode()
        relative = 'checks/' + cid + '.yaml'
        (folder / relative).write_bytes(body)
        files.append({'path': relative, 'sha256': hashlib.sha256(body).hexdigest()})
    manifest = {'pack_id': 'PACK-B-ONLINE-INPUT-CPU-' + profile.upper(), 'revision': 1, 'engines': ['vllm'],
                'checks': list(CHECKS), 'files_sha256': files, 'review': {'status': 'DRAFT'}}
    (folder / 'pack.yaml').write_text(yaml.safe_dump(manifest, sort_keys=False))
    return load_pack(folder)


def run_pipeline(source_root, output_root, *, source_profile='reference5d', origin=DataOrigin.DECLARED):
    _require_normal_python()
    commit = _source_commit(source_profile)
    repo = Path(__file__).resolve().parents[2]
    verify_runtime_components(repo)
    began = time.perf_counter()
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=False)
    now = datetime.now(timezone.utc)
    pack = proposal_pack(output_root / 'proposal-pack', source_profile)
    env = Environment(id=new_id('ENV'), created_at=now, data_origin=origin,
                      engine={'name': 'vllm', 'version': 'reference-' + commit}).seal()
    policy = Policy(id=new_id('POL'), created_at=now, data_origin=origin,
                    status='DRAFT', pack=pack.ref(), scope={'include': list(CHECKS)}).seal()
    store = Store(output_root / 'store')
    put_pack(store, pack.path)
    store.put_many([env, policy])
    plan = select(pack, [env], policy)
    store.put(plan)
    shared = _SharedCapture(env, plan.ref(), source_root, source_profile,
        capture_fn=collector.capture_online, producer_sha256=COLLECTOR_SHA256, component_check=lambda: verify_runtime_components(repo))
    class BoundExecutor:
        name, version = _SharedCapture.name, _SharedCapture.version
        def run(self, check, environment, values, adapter, workdir):
            return shared.draft(check, environment, values, workdir, plan_ref=plan.ref(), source_profile=source_profile)
    executor = BoundExecutor()
    key = _SharedCapture.name + ':source-cpu-proposal'
    previous = registry.EXECUTORS.get(key)
    registry.EXECUTORS[key] = lambda raw: executor
    try:
        evidences = run_plan(store, pack, plan, RunConfig(executors={_SharedCapture.name: 'source-cpu-proposal'}))
    finally:
        if previous is None:
            registry.EXECUTORS.pop(key, None)
        else:
            registry.EXECUTORS[key] = previous
    results = judge_plan(store, pack, plan, as_of=now)
    replay = judge_plan(store, get_pack(store, plan.inputs.pack), plan, as_of=now)
    if [result.ref() for result in replay] != [result.ref() for result in results]:
        raise ValueError('ONLINE_INPUT_CPU_EXACT_REF_REJUDGMENT_MISMATCH')
    report = build_report(store, pack, plan, as_of=now)
    store.put(report)
    for obj in (env, policy, plan, *evidences, *results, report):
        path = output_root / (obj.kind.value + ('-' + obj.check_id if hasattr(obj, 'check_id') else '') + '.json')
        with path.open('x') as stream:
            json.dump(obj.to_dict(), stream, ensure_ascii=False, indent=2)
    summary = {'profile': PROFILE, 'source_profile': source_profile, 'source_commit': commit,
        'product_component_commit': COMPONENT_COMMIT, 'collection_id': shared.collection_id,
        'capture_started_at': shared.started.isoformat() if shared.started else None,
        'capture_finished_at': shared.finished.isoformat() if shared.finished else None,
        'capture_invocations': int(shared.loaded), 'installed_target': None, 'runner_hook_verified': False, 'runner_consumption_verified': False,
        'permanent_registration_verified': False, 'pack': pack.ref().model_dump(mode='json'),
        'evidence': [evd.ref().model_dump(mode='json') for evd in evidences],
        'results': [result.ref().model_dump(mode='json') for result in results],
        'report': report.ref().model_dump(mode='json'),
        'checks': {result.check_id: {'execution_status': next(e.execution.status.value for e in evidences if e.check_id == result.check_id),
            'data_origin': next(e.data_origin.value for e in evidences if e.check_id == result.check_id),
            'verdicts': result.verdicts.model_dump(mode='json')} for result in results},
        'exact_ref_rejudgment_unchanged': True, 'store_verify_errors': store.verify(),
        'parent_wall_seconds': time.perf_counter() - began}
    with (output_root / 'summary.json').open('x') as stream:
        json.dump(summary, stream, indent=2)
    return summary


REQ09_PROFILE = 'b1-req09-target031-cpu/1'
REQ09_PACK_REF = {'kind': 'PACK', 'id': 'PACK-BASELINE', 'revision': 31,
    'sha256': '54f4f2357533a8b201ad4397d4b182b08b89ee4821ffe67763e3fbecd8f369ac'}
REQ09_OVERRIDE = 'source CPU InputProcessor mask/offset boundary only; model/HTTP not executed'
_REQ09_META = ('prompt_count', 'token_ids_canonical_sha256', 'embedding_shape', 'embedding_dtype',
    'embedding_bytes_sha256', 'mask_count', 'mask_canonical_sha256', 'routed_experts_prompt_start')
_REQ09_OBSERVATIONS = {
    'accepted_challenges': 'list[str]', 'challenge_outcomes': 'dict[str, outcome]',
    'challenges_planned': 'list[str]', 'functional_probes_planned': 'list[str]',
    'functional_probe_results': 'dict[str, outcome]', 'input_endpoints_tested': 'list[str]',
    'input_formats_tested': 'list[str]', 'challenge_rejection_results': 'dict[str, outcome]',
    'challenge_consumption': 'dict[str, outcome]', 'confirmed_input_violations': 'list[str]'}


def _req09_inventory(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(Path(root).rglob('*')) if path.is_file()}


def _req09_refs(store):
    return sorted((obj.kind.value, obj.id, obj.revision, obj.content_sha256)
        for kind in ('ENV', 'POL', 'CHG', 'PLAN', 'EVD', 'RES', 'RPT') for obj in store.iter_kind(kind))


def _req09_preflight(source_store, plan_id, pack_path, output_root, entered_by):
    source = Store.read_only(Path(source_store))
    verify_complete(source)
    pack = load_pack(Path(pack_path))
    plan = source.latest(plan_id)
    if (plan.kind != 'PLAN' or plan.mode != Mode.audit or len(plan.inputs.environments) != 1
            or plan.inputs.pack != pack.ref() or pack.ref().model_dump(mode='json') != REQ09_PACK_REF
            or get_pack(source, plan.inputs.pack).ref() != pack.ref()):
        raise ValueError('REQ09_ORIGINAL_PACK31_AUDIT_PLAN_REQUIRED')
    check = pack.checks['REQ-09']
    if (check.revision != 8 or check.executor != 'input_validation'
            or check.judge is None or check.judge.plugin != 'rule_table'
            or {key.key: key.type for key in check.required_observations} != _REQ09_OBSERVATIONS
            or [(key.key, key.type, key.required) for key in check.policy_params] != [
                ('max_input_tokens', 'int', True), ('rejection_codes', 'list[str]', True),
                ('allowed_endpoints', 'list[str]', True), ('allowed_input_formats', 'list[str]', True)]
            or len(check.required_observations_prose) != 4 or len(check.challenge_steps) != 2
            or not plan.item('REQ-09').selected or plan.item('REQ-09').check_revision != 8):
        raise ValueError('REQ09_ORIGINAL_DEFINITION_REQUIRED')
    env = source.resolve(plan.inputs.environments[0])
    if (env.engine.name != 'vllm' or env.engine.version != 'source-' + SOURCE_PROFILES['target031']
            or env.engine.image_digest is not None or any(value is not None for value in env.model.model_dump().values())):
        raise ValueError('REQ09_SOURCE_ONLY_ENVIRONMENT_REQUIRED')
    policy = source.resolve(plan.inputs.policy)
    values = policy.values.get('REQ-09', {})
    if policy.pack != pack.ref() or any(key.key not in values for key in check.policy_params):
        raise ValueError('REQ09_PINNED_POLICY_REQUIRED')
    for key in check.policy_params:
        check_policy_value(check.id, key.key, key.type, values[key.key])
    if (values['max_input_tokens'] <= 0 or not values['rejection_codes']
            or policy_context(check, values).blocked):
        raise ValueError('REQ09_POLICY_PROFILE_UNSUPPORTED')
    output_root = Path(output_root).absolute()
    if (output_root.exists() or output_root.is_symlink()
            or output_root.resolve().is_relative_to(source.root.resolve())
            or source.root.resolve().is_relative_to(output_root.resolve())
            or type(entered_by) is not str or not entered_by.strip()):
        raise ValueError('REQ09_FRESH_OUTPUT_AND_DECLARED_OPERATOR_REQUIRED')
    return source, pack, plan, env


def _req09_metadata(value, *, admitted=False):
    if type(value) is not dict or any(key not in value for key in (*_REQ09_META, 'canonical_sha256')):
        return None
    safe = {key: value[key] for key in _REQ09_META}
    for key in ('prompt_count', 'mask_count'):
        if safe[key] is not None and (type(safe[key]) is not int or not 0 <= safe[key] <= 262144):
            return None
    for key in ('token_ids_canonical_sha256', 'embedding_bytes_sha256', 'mask_canonical_sha256'):
        if safe[key] is not None and not _sha256(safe[key]):
            return None
    shape = safe['embedding_shape']
    if shape is not None and (type(shape) is not list or not 1 <= len(shape) <= 4
            or any(type(size) is not int or not 0 <= size <= 262144 for size in shape)):
        return None
    if (safe['embedding_dtype'] not in (None, 'torch.float32')
            or type(safe['routed_experts_prompt_start']) is not int
            or safe['routed_experts_prompt_start'].bit_length() > 256
            or value['canonical_sha256'] != req09_product.digest(safe)):
        return None
    safe['canonical_sha256'] = value['canonical_sha256']
    if admitted:
        if (value.get('admission_completed') is not True or not _sha256(value.get('processor_input_sha256'))
                or value.get('processor_output_sha256') != safe['canonical_sha256']):
            return None
        safe.update(admission_completed=True, processor_input_sha256=value['processor_input_sha256'],
                    processor_output_sha256=value['processor_output_sha256'])
    return safe


def _req09_expected_input(case):
    value = {'prompt_count': 3, 'token_ids_canonical_sha256': None, 'embedding_shape': None,
        'embedding_dtype': None, 'embedding_bytes_sha256': None, 'mask_count': None,
        'mask_canonical_sha256': None, 'routed_experts_prompt_start': 0}
    if case['kind'] in ('mask', 'shape'):
        shape = case['value']['shape']
        elements = 1
        for size in shape: elements *= size
        value.update(embedding_shape=shape, embedding_dtype='torch.float32',
                     embedding_bytes_sha256=hashlib.sha256(bytes(elements * 4)).hexdigest())
        if case['kind'] == 'mask':
            count = case['value']['mask_length']
            value.update(token_ids_canonical_sha256=req09_product.digest([3, 4, 5]),
                         mask_count=count, mask_canonical_sha256=req09_product.digest([False] * count))
    else:
        value.update(token_ids_canonical_sha256=req09_product.digest([3, 4, 5]),
                     routed_experts_prompt_start=case['value'])
    return value


def _req09_record(raw):
    if (type(raw) is not dict or raw.get('profile') != REQ09_PROFILE
            or raw.get('source_profile') != 'target031' or raw.get('source_commit') != SOURCE_PROFILES['target031']
            or raw.get('scope') != 'source_cpu_input_processor_boundary_only'
            or raw.get('data_origin') != 'DECLARED' or raw.get('installed_target') is not None
            or raw.get('runner_consumption_verified') is not False
            or raw.get('own_producer_sha256') != COLLECTOR_SHA256
            or type(raw.get('collection_id')) is not str
            or re.fullmatch('req09-target031-cpu-[0-9a-f]{32}', raw['collection_id']) is None
            or type(raw.get('results')) is not list or len(raw['results']) > 12
            or raw.get('fixture_config') != {'origin': 'DECLARED', 'vocab_size': 32, 'max_model_len': 16, 'hidden_size': 4}
            or raw.get('effective_config') != {'vocab_size': 32, 'max_model_len': 16, 'hidden_size': 4}):
        raise ValueError('REQ09_CAPTURE_ATTRIBUTION_OR_FIXTURE_MISMATCH')
    validation = _validation(raw.get('source_validation'))
    if validation['pre'] != 'verified' or validation['boundary'] != 'source_fixture_component':
        raise ValueError('REQ09_CAPTURE_SOURCE_PRECHECK_UNAVAILABLE')
    hashes = raw.get('config_file_bytes_sha256')
    if (type(hashes) is not dict or not {'config.json', 'tokenizer.json'} <= set(hashes)
            or any(name not in _CONFIG_FILES or not _sha256(value) for name, value in hashes.items())):
        raise ValueError('REQ09_CONFIG_BYTES_UNAVAILABLE')
    started, finished = _utc(raw.get('started_at')), _utc(raw.get('finished_at'))
    if finished < started:
        raise ValueError('REQ09_CAPTURE_TIME_INVALID')
    cases = {case['id']: case for case in req09_product.fixtures() if case['check_id'] == 'REQ-09'}
    counts = {}
    for row in raw['results']:
        if type(row) is dict and type(row.get('case_id')) is str:
            counts[row['case_id']] = counts.get(row['case_id'], 0) + 1
    rows, qualified = [], []
    for row in raw['results']:
        if type(row) is not dict or row.get('case_id') not in cases:
            continue
        case = cases[row['case_id']]
        spec = req09_product.describe(case)
        source = _validation(row.get('source_validation'))
        raw_validation = row.get('source_validation') if type(row.get('source_validation')) is dict else {}
        raw_post = raw_validation.get('post') if type(raw_validation.get('post')) is dict else {}
        inputs = _req09_metadata(row.get('input_metadata'))
        admitted = _req09_metadata(row.get('admitted_metadata'), admitted=True)
        events = _events(row.get('events'))
        eligible = (counts[row['case_id']] == 1 and all(row.get(key) == value for key, value in spec.items())
            and row.get('source_profile') == 'target031' and row.get('source_commit') == SOURCE_PROFILES['target031']
            and row.get('collection_id') == raw['collection_id']
            and source['pre'] == 'verified' and raw_post.get('status') in {'verified', 'unavailable'}
            and source['boundary'] == 'source_fixture_component' and 'process_inputs' in events
            and inputs is not None and all(inputs[key] == value for key, value in _req09_expected_input(case).items()))
        if row.get('outcome') == 'accepted':
            eligible = (eligible and admitted is not None and admitted['processor_input_sha256'] == inputs['canonical_sha256']
                and all(admitted[key] == inputs[key] for key in _REQ09_META))
        elif row.get('outcome') != 'rejected':
            eligible = False
        after = row.get('after') if type(row.get('after')) is dict else {}
        safe_row = {**spec, 'outcome': _outcome(row.get('outcome')), 'events': events,
            'input_metadata': inputs, 'admitted_metadata': admitted, 'source_validation': source,
            'qualified': bool(eligible), 'after': {'outcome': _outcome(after.get('outcome')),
                'events': _events(after.get('events')),
                'admitted_metadata': _req09_metadata(after.get('admitted_metadata'), admitted=True)}}
        rows.append(safe_row)
        if eligible:
            qualified.append({**spec, 'outcome': row['outcome'], 'admitted_metadata': admitted})
    observation = req09_product.project(qualified)['REQ-09']['input_cpu_confirmed_violations']
    record = {key: raw[key] for key in ('profile', 'source_profile', 'source_commit', 'scope', 'collection_id',
        'fixture_config', 'own_producer_sha256')}
    record.update(data_origin='DECLARED', installed_target=None, runner_consumption_verified=False,
        started_at=started.isoformat(), finished_at=finished.isoformat(), source_validation=validation,
        config_file_bytes_sha256=dict(hashes), results=rows, full_scope_coverage=False,
        capture_status='completed' if (raw.get('capture_status') == 'completed'
            and validation['post']['status'] == 'verified'
            and all(row['source_validation']['post']['status'] == 'verified' for row in rows)) else 'incomplete',
        auxiliary_write={'status': 'completed' if (raw.get('auxiliary_write') or {}).get('status') == 'completed' else 'failed'},
        confirmed_input_violations=observation['value'] if observation['collected'] else None)
    record['effective_config'] = {'vocab_size': 32, 'max_model_len': 16, 'hidden_size': 4}
    record['observed_source_file_bytes_sha256'] = {key: value for key, value in
        (raw.get('observed_source_file_bytes_sha256') or {}).items()
        if key in {'vllm/renderers/base.py', 'vllm/v1/engine/input_processor.py'} and _sha256(value)}
    record['source_features'] = {key: {'present': value['present']} for key, value in
        (raw.get('source_features') or {}).items() if key in _EVENTS and type(value) is dict
        and type(value.get('present')) is bool}
    return record


def run_req09_pipeline(source_root, output_root, *, source_store, plan_id, pack_path, entered_by):
    """원 REQ-09 정의의 source CPU 부분 관측을 기존 수동 증거로 연결한다."""
    _require_normal_python()
    source, pack, plan, env = _req09_preflight(source_store, plan_id, pack_path, output_root, entered_by)
    repo = Path(__file__).resolve().parents[2]
    verify_runtime_components(repo)
    before = _req09_inventory(source.root)
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=False)
    export_bundle(source.root, output_root / 'source.zip')
    restore_bundle(output_root / 'source.zip', output_root / 'store')
    destination = Store(output_root / 'store')
    raw = collector.capture_req09_target031(Path(source_root), output_root / 'capture')
    record = _req09_record(raw)
    record['source_environment_origin'] = env.data_origin.value
    issues = []
    try:
        verify_runtime_components(repo)
    except Exception:
        issues.append('REQ09_COMPONENT_POSTCHECK_UNAVAILABLE')
    attachment = 'req09-input-cpu.json'
    try:
        _write_artifact(output_root / attachment, record)
    except Exception:
        attachment = None
        issues.append('REQ09_ARTIFACT_WRITE_UNAVAILABLE')
    doc = build_template(destination, pack, plan, include_automated=True)
    doc['checks'] = [entry for entry in doc['checks'] if entry['check_id'] == 'REQ-09' and entry['side'] == 'single']
    if len(doc['checks']) != 1:
        raise ValueError('REQ09_ORIGINAL_MANUAL_ENTRY_REQUIRED')
    entry = doc['checks'][0]
    violations = record['confirmed_input_violations']
    entry.update(override_reason=REQ09_OVERRIDE, entered_by=entered_by, entered_at=record['finished_at'],
        started_at=record['started_at'], finished_at=record['finished_at'], attachments=[attachment] if attachment else [],
        verdicts={'security': 'FAIL' if violations else 'UNKNOWN', 'functional': 'UNKNOWN', 'performance': 'UNKNOWN'},
        basis='선정 source CPU InputProcessor의 고정 mask/offset 대응과 실제 수용을 대조했다. HTTP·모델 소비는 미실행이다.',
        limitations=['source CPU InputProcessor 부분 관측; 전체 질문의 정상 대조·token/길이 HTTP·모델 소비 미실행.',
                    'shape expected=None은 관측만 하며 위반을 생성하지 않음. 기업 승인 진위·runner 소비·전체 scope UNKNOWN.'])
    entry['normal_control']['result'] = 'not_attempted'
    for challenge in entry['challenges']: challenge['attempted'] = False
    for observation in entry['required_observations']:
        selected = observation['key'] == 'confirmed_input_violations' and violations is not None
        observation.update(value=violations if selected else None, collected=selected)
    answers = [
        '로컬 fixture vocab=32/max_model_len=16/hidden_size=4는 source CPU 조건이다. 기업 제한·HTTP token/길이 집행 전체는 UNKNOWN.',
        'mask/embedding 제출·수용의 정제 hash/count/shape/dtype를 첨부했다. 실제 모델 consumer·전체 dtype/shape 규칙은 UNKNOWN.',
        '독립 offset 입력의 고정 경계와 실제 InputProcessor 수용을 대조했다. 확인된 부분 위반=' + json.dumps(violations) + '.',
        '출처는 고정 source CPU 직접 호출의 DECLARED 관측이다. 사례별 후속 정상 admission만 기록하며 HTTP/worker/모델·메모리/타 요청 영향은 UNKNOWN.']
    for note, answer in zip(entry['observation_notes'], answers): note['answer'] = answer
    form = output_root / 'manual-evidence.yaml'
    try:
        with form.open('x', encoding='utf-8') as stream: stream.write(dump_template(doc))
    except OSError:
        issues.append('REQ09_MANUAL_FORM_WRITE_UNAVAILABLE')
    import_evidence(destination, pack, form, document=doc)
    as_of = datetime.now(timezone.utc).replace(microsecond=0)
    results = judge_plan(destination, pack, plan, as_of=as_of)
    report = build_report(destination, pack, plan, as_of=as_of)
    destination.put(report)
    verify_complete(destination)
    refs = _req09_refs(destination)
    export_bundle(destination.root, output_root / 'evidence.zip')
    restore_bundle(output_root / 'evidence.zip', output_root / 'restored')
    restored = Store(output_root / 'restored')
    replay = judge_plan(restored, pack, plan, as_of=as_of)
    if (_req09_refs(restored) != refs or [item.ref() for item in replay] != [item.ref() for item in results]):
        raise ValueError('REQ09_EXACT_REF_RESTORE_MISMATCH')
    rereport = build_report(restored, pack, plan, as_of=as_of)
    if rereport.model_dump(exclude={'id', 'content_sha256', 'created_at'}) != report.model_dump(exclude={'id', 'content_sha256', 'created_at'}):
        raise ValueError('REQ09_RESTORED_REPORT_MISMATCH')
    verify_complete(restored)
    if _req09_inventory(source.root) != before:
        raise ValueError('REQ09_SOURCE_STORE_CHANGED')
    summary = {'profile': REQ09_PROFILE, 'source_profile': 'target031', 'source_commit': SOURCE_PROFILES['target031'],
        'capture_invocations': 1, 'pack': pack.ref().model_dump(mode='json'), 'plan': plan.ref().model_dump(mode='json'),
        'policy': plan.inputs.policy.model_dump(mode='json'), 'environment': env.ref().model_dump(mode='json'),
        'collection_id': record['collection_id'], 'source_store_unchanged': True, 'exact_refs_preserved': True,
        'installed_target': None, 'runner_consumption_verified': False, 'full_scope_coverage': False,
        'as_of': as_of.isoformat(), 'issues': issues, 'confirmed_input_violations': violations,
        'checks': {'REQ-09': {'execution_status': 'COMPLETED', 'data_origin': 'DECLARED',
            'verdicts': next(item.verdicts.model_dump(mode='json') for item in results if item.check_id == 'REQ-09')}},
        'store_verify_errors': destination.verify()}
    _write_artifact(output_root / 'summary.json', summary)
    return summary


def main():
    _require_normal_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-profile', choices=tuple(SOURCE_PROFILES), default='reference5d')
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--check', choices=('REQ-09',))
    parser.add_argument('--source-store', type=Path)
    parser.add_argument('--plan', dest='plan_id')
    parser.add_argument('--pack', dest='pack_path', type=Path)
    parser.add_argument('--entered-by')
    args = parser.parse_args()
    if args.check == 'REQ-09':
        if args.source_profile != 'target031' or any(value is None for value in
                (args.source_store, args.plan_id, args.pack_path, args.entered_by)):
            parser.error('REQ-09는 target031과 원 Store/PLAN/PACK/입력자가 필요합니다')
        summary = run_req09_pipeline(args.source_root, args.output_root, source_store=args.source_store,
            plan_id=args.plan_id, pack_path=args.pack_path, entered_by=args.entered_by)
    else:
        if any(value is not None for value in (args.source_store, args.plan_id, args.pack_path, args.entered_by)):
            parser.error('원본 증거 연결 옵션은 --check REQ-09에만 적용합니다')
        summary = run_pipeline(args.source_root, args.output_root, source_profile=args.source_profile)
    print(json.dumps(summary))
    return 0 if all(check['execution_status'] == 'COMPLETED' for check in summary['checks'].values()) and not summary['store_verify_errors'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
