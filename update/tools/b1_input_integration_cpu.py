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
from guard.executors import online_input_cpu as product, registry
from guard.executors.base import DraftArtifact, EvidenceDraft
from guard.executors.http_common import incomplete_artifact_capture
from guard.models import CheckDef, DataOrigin, Environment, ExecStatus, Policy, load_pack
from guard.store import Store
from guard.store.packs import get_pack, put_pack
from tools import b1_input_cpu as collector
from tools import lif14_profiler_cpu as component_gate

PROFILE = 'online-input-source-cpu-proposal-v1'
SOURCE_PROFILES = {'reference5d': '5d35ce3934a7804b298559d2d99eec33792e1eb9',
                   'target031': 'db9527a46873454610df6dbedf79a36d6bf1a7f6'}
GATE_SHA256 = '5f9e45cb57979970e821857a2a26de06509622f5b681d8d6fb0938b254dbf7e2'
COMPONENT_COMMIT = 'ee1ff38421dc710685e3ebaa670b1e083991e9b5'
# Reviewed collector snapshot de799c2; runtime bytes never become approval.
COLLECTOR_SHA256 = 'cf92c4c32588314f83a637523785e0ebb5e6dd6bbab50954fa1a24c2f86e39bb'
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
    env = Environment(id='ENV-B-ONLINE-INPUT-CPU-' + source_profile.upper(), created_at=now, data_origin=origin,
                      engine={'name': 'vllm', 'version': 'reference-' + commit}).seal()
    policy = Policy(id='POL-B-ONLINE-INPUT-CPU-' + source_profile.upper(), created_at=now, data_origin=origin,
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


def main():
    _require_normal_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-profile', choices=tuple(SOURCE_PROFILES), default='reference5d')
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    args = parser.parse_args()
    summary = run_pipeline(args.source_root, args.output_root, source_profile=args.source_profile)
    print(json.dumps(summary))
    return 0 if all(check['execution_status'] == 'COMPLETED' for check in summary['checks'].values()) and not summary['store_verify_errors'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
