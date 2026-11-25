"""Review the security UNKNOWN limit from original saved update RPTs and stores."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re

from guard.core.compare import compare, comparison_differences, security_comparison_verdict
from guard.core.judge import judge_evidence
from guard.core.report import _side as report_side
from guard.core.select import _in_scope, check_policy_value, evaluate_check
from guard.executors import passive_api_hop, run_binding
from guard.models import Ref, Side, Verdict, canonical_json, load_pack, sha256_hex, weakest_origin
from guard.models.artifacts import model_closure_sha256
from guard.models.objects import object_from_dict
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_binding import validate_container_binding
from guard.store import Store
from guard.update_validation import _plain_path
from guard.validation_evidence import read_objects, verify_store_files
from tools.mvp_a_input_preflight import _pairs, _read
from tools.mvp_versions import versions

ROOT = Path(__file__).resolve().parents[1]
SIDES = ('baseline', 'candidate')
UNKNOWN_LIMIT = 6
SHA256 = re.compile(r'[0-9a-f]{64}')


def _key(ref):
    return ref.kind, ref.id, ref.revision, ref.sha256


def _dump(ref):
    return ref.model_dump(mode='json')


class InvalidReport(ValueError):
    pass


def _passive_hop_values(evd):
    document = evd.execution.run_binding if evd is not None else None
    values = document.get('values') if isinstance(document, dict) else None
    if evd is not None and evd.check_id == 'PLT-08' and isinstance(values, dict) and values.get('profile') == passive_api_hop.PROFILE:
        return values
    return None


def _verify_passive_hop(evd, source, env, policy, plan, artifact_store, values):
    """Recheck the verified same-report source artifact without a live target."""
    if (artifact_store is None or source is None or evd.artifacts
            or evd.execution.executor is None or evd.execution.executor.name != passive_api_hop.EXECUTOR
            or evd.execution.executor.version != passive_api_hop.VERSION
            or set(values) != {'profile', 'source_evidence', 'source_artifact_sha256', 'target_sha256'}
            or evd.execution.run_binding.get('schema') != run_binding.SCHEMA_2
            or evd.execution.run_binding.get('files') != {
                key: values[key] for key in ('target_sha256', 'source_artifact_sha256')}
            or values['source_evidence'] != _dump(source.ref())
            or source.ref() not in evd.refs or not plan.inputs.execution_recipe_sha256
            or source.plan != evd.plan or source.environment != evd.environment or source.side != evd.side
            or not evd.execution.attempt_id or source.execution.attempt_id != evd.execution.attempt_id
            or source.data_origin != 'MEASURED' or source.data_origin != evd.data_origin
            or source.comparability != evd.comparability or source.execution.finished_at is None
            or evd.execution.started_at is None or source.execution.finished_at > evd.execution.started_at
            or evd.normal_control.attempted or evd.normal_control.succeeded is not None
            or any(step.attempted for step in evd.challenges)):
        return False
    try:
        target_hash = values['target_sha256']
        if type(target_hash) is not str or SHA256.fullmatch(target_hash) is None:
            return False
        summary, artifact_hash = passive_api_hop._read_summary(artifact_store, source)
        if artifact_hash != values['source_artifact_sha256']:
            return False
        binding = summary['kubernetes']['binding']
        if (type(binding) is not dict or set(binding) != {'context', 'run_id', 'engine', 'probe'}
                or not re.fullmatch(r'[A-Za-z0-9._:@/-]{1,200}', binding['context'])
                or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', binding['run_id'])):
            return False
        for role in ('engine', 'probe'):
            validate_container_binding(binding[role])
        if (binding['engine']['image'] != env.engine.image_digest
                or any(binding['engine'][field] == binding['probe'][field] for field in ('pod_uid', 'container_id', 'pod_ip'))):
            return False
        first, last = passive_api_hop._bound_request(summary, source, binding, target_hash)
        prohibited = policy.values.get('PLT-08', {}).get('plaintext_forbidden')
        if type(prohibited) is not bool:
            return False
        trial = {**values, 'original_source_data_origin': source.data_origin.value, 'transport': 'plain_http',
                 'source_role': 'selected_owned_kubernetes_probe', 'destination_role': 'selected_owned_kubernetes_engine_pod',
                 'method': 'POST', 'route': '/v1/completions', 'outcome': 200, 'capture_complete': True,
                 'owner_binding_verified_before_after': True, 'started_at': first.isoformat(), 'finished_at': last.isoformat(),
                 'global_plaintext_forbidden': prohibited, 'hop_policy_adjudicated': False, 'deployed_caller_path_verified': False}
        return {key: value.model_dump(mode='json') for key, value in evd.observations.items()} == {
            'api_transport_trials': {'value': [trial], 'collected': True},
            'forbidden_plaintext_successes': {'value': None, 'collected': False},
            'api_transport_scope_complete': {'value': False, 'collected': True},
        }
    except (KeyError, OSError, TypeError, ValueError, AttributeError):
        return False


def _verify_side(check, item, evd, result, shown, env, policy, plan, report, *, source=None, artifact_store=None):
    side = {'environment': _dump(env.ref()), 'evidence': _dump(evd.ref()) if evd else None,
            'result': _dump(result.ref()) if result else None, 'verdicts': None, 'blocking_codes': [],
            'reported_security': None, 'security_failure_tags': [],
            'execution_status': evd.execution.status.value if evd else None}
    blockers = side['blocking_codes']
    if evd is None:
        blockers.append('ORIGINAL_EVIDENCE_MISSING')
    if result is None:
        blockers.append('ORIGINAL_RESULT_MISSING')
    if shown is None or shown.result is None:
        blockers.append('ORIGINAL_RPT_SIDE_MISSING')
    binary = report.security_verdict_version is not None
    if shown is not None and (
        (shown.evidence_ref != (evd.ref() if evd else None) and (binary or shown.evidence_ref is not None))
        or (shown.data_origin != (evd.data_origin if evd else None) and (binary or shown.data_origin is not None))
        or (shown.execution_status != (evd.execution.status if evd else None) and (binary or shown.execution_status is not None))
    ):
        blockers.append('ORIGINAL_RPT_EVIDENCE_MISMATCH')
    if result is None and shown is not None and shown.result is None:
        placeholder = report_side(None, evd, binary_security=binary)
        if ('ORIGINAL_RPT_EVIDENCE_MISMATCH' not in blockers and shown.security == placeholder.security
                and shown.security_failure_tags == placeholder.security_failure_tags
                and shown.functional is None and shown.performance is None and shown.verification_level is None):
            side['reported_security'] = shown.security.value
            side['security_failure_tags'] = [tag.value for tag in shown.security_failure_tags]
    if evd is not None:
        execution = evd.execution
        if execution.status != 'COMPLETED':
            blockers.append(f'EXECUTION_{execution.status.value}')
        if evd.data_origin != 'MEASURED' or execution.operator is not None or execution.override_reason:
            blockers.append('AUTOMATED_MEASUREMENT_REQUIRED')
        passive_values = _passive_hop_values(evd)
        passive_verified = passive_values is not None and _verify_passive_hop(evd, source, env, policy, plan, artifact_store, passive_values)
        if passive_values is not None and not passive_verified:
            blockers.append('PASSIVE_API_HOP_PROVENANCE_INVALID')
        if ((not passive_verified and not evd.artifacts) or any(artifact.bytes < 1 for artifact in evd.artifacts)
                or execution.executor is None or execution.executor.name != check.executor
                or execution.adapter is None or execution.adapter.name != env.engine.name
                or not SHA256.fullmatch(execution.executor.config_sha256)):
            blockers.append('ORIGINAL_EXECUTOR_OR_ARTIFACTS_INVALID')
        if (execution.started_at is None or execution.finished_at is None
                or execution.finished_at < execution.started_at):
            blockers.append('ORIGINAL_EXECUTION_TIME_INVALID')
        if (evd.comparability.model_inventory_sha256 != model_closure_sha256(env.model)
                or evd.comparability.gpu != env.runtime.gpu_model or evd.comparability.driver != env.runtime.driver
                or evd.comparability.cpu is not None
                or not SHA256.fullmatch(evd.comparability.config_sha256 or '')
                or not SHA256.fullmatch(evd.comparability.fixture_sha256 or '')):
            blockers.append('ORIGINAL_COMPARABILITY_INCOMPLETE')
        document = execution.run_binding
        if document is None:
            if run_binding.keys_for(check):
                blockers.append('ORIGINAL_RUN_BINDING_MISSING')
        else:
            blockers.extend(run_binding.verify(document, evd))
            values = document.get('values')
            if isinstance(values, dict) and run_binding.CURRENT_POLICY_REF in values:
                if values[run_binding.CURRENT_POLICY_REF] != _dump(policy.ref()):
                    blockers.append('RUN_BINDING_POLICY_REF_MISMATCH')
    if evd is not None and result is not None and shown is not None and shown.result is not None:
        as_of = report.as_of or result.as_of or report.created_at
        replay = judge_evidence(check, evd, policy, as_of,
                                condition_origins=[condition.origin for condition in item.conditions], plan=plan, binary_security=binary)
        if (result.evidence != evd.ref() or len(result.refs) != len(replay.refs)
                or {_key(ref) for ref in result.refs} != {_key(ref) for ref in replay.refs}
                or shown.result != result.ref() or shown.verification_level != result.verification_level
                or result.evaluation_mode is not None or (report.as_of is not None and result.as_of != report.as_of)
                or result.security_verdict_version != report.security_verdict_version
                or shown.security_failure_tags != result.security_failure_tags
                or any(getattr(shown, axis) != getattr(result.verdicts, axis)
                       for axis in ('security', 'functional', 'performance'))
                or any(getattr(result, key) != getattr(replay, key)
                       for key in ('judge', 'verdicts', 'security_failure_tags', 'verification_level', 'data_origin', 'missing_observations'))):
            blockers.append('ORIGINAL_RESULT_REPLAY_MISMATCH')
        elif 'ORIGINAL_RPT_EVIDENCE_MISMATCH' not in blockers:
            side['reported_security'] = shown.security.value
            side['security_failure_tags'] = [tag.value for tag in result.security_failure_tags]
            if not blockers:
                side['verdicts'] = result.verdicts.model_dump(mode='json')
    if evd is not None and (evd.execution.cleanup_errors
                            or evd.execution.status == 'COMPLETED' and evd.execution.abort_reason):
        blockers.append('EXECUTION_OR_CLEANUP_UNRESOLVED')
    return side


def _verified_rows(original_report, objects, pack, expected, artifact_store=None):
    parsed = list(map(object_from_dict, objects))
    by_ref = {_key(obj.ref()): obj for obj in parsed}
    if len(by_ref) != len(parsed) or any(not obj.hash_ok() for obj in parsed):
        raise InvalidReport('INVALID_OR_DUPLICATE_STORED_OBJECT')

    def resolve(ref):
        if ref.kind == 'PACK' and ref == pack.ref():
            return pack
        return by_ref[_key(ref)]

    report = object_from_dict(original_report)
    if report.kind != 'RPT' or report.mode != 'update' or resolve(report.ref()).to_dict() != original_report:
        raise InvalidReport('ORIGINAL_RPT_MISMATCH')
    manifest = {_key(ref) for ref in report.inputs_manifest}
    if len(manifest) != len(report.inputs_manifest) or manifest != {_key(ref) for ref in report.refs}:
        raise InvalidReport('ORIGINAL_RPT_MANIFEST_MISMATCH')
    inputs = [resolve(ref) for ref in report.inputs_manifest]
    plan, = [obj for obj in inputs if getattr(obj, 'kind', None) == 'PLAN']
    if (plan.mode != 'update' or plan.inputs.pack != pack.ref() or len(plan.inputs.environments) != 2
            or plan.inputs.environments[0] == plan.inputs.environments[1]):
        raise InvalidReport('ORIGINAL_PLAN_SCOPE_MISMATCH')
    policy = resolve(plan.inputs.policy)
    envs = [resolve(ref) for ref in plan.inputs.environments]
    plan_inputs = [pack.ref(), policy.ref(), *plan.inputs.environments]
    if plan.inputs.change:
        resolve(plan.inputs.change)
        plan_inputs.append(plan.inputs.change)
    if (policy.kind != 'POL' or policy.pack != pack.ref() or plan.inputs.previous_plan is not None
            or {_key(ref) for ref in plan.refs} != {_key(ref) for ref in plan_inputs}
            or {_key(ref) for ref in report.inputs_manifest if ref.kind not in {'EVD', 'RES'}}
            != {_key(ref) for ref in [*plan_inputs, plan.ref()]}):
        raise InvalidReport('ORIGINAL_PLAN_INPUTS_MISMATCH')
    for side, env in zip(SIDES, envs, strict=True):
        engine = expected[side]
        if env.kind != 'ENV' or (env.engine.name, env.engine.version, env.engine.image_digest) != (
                engine['engine'], engine['version'], engine['image']):
            raise InvalidReport('ENGINE_PAIR_MISMATCH')
        runtime = env.runtime
        if (env.data_origin == 'EXAMPLE' or not env.model.id or not env.model.revision
                or not SHA256.fullmatch(env.model.inventory_sha256 or '')
                or not runtime.gpu_count or not runtime.gpu_model or not runtime.driver or runtime.cpu is not None
                or (runtime.tp_size or 0) * (runtime.pp_size or 1) * (runtime.dp_size or 1) != runtime.gpu_count):
            raise InvalidReport('ORIGINAL_GPU_ENVIRONMENT_REQUIRED')

    origins = [obj.data_origin for obj in inputs if getattr(obj, 'kind', None) in {'ENV', 'POL', 'PLAN', 'EVD', 'RES'}]
    official = policy.status == 'CONFIRMED' and pack.reviewed and 'EXAMPLE' not in origins
    if (report.official != official or report.data_origin != weakest_origin(origins)
            or report.policy_status != policy.status or report.pack_review_status != ('REVIEWED' if pack.reviewed else 'DRAFT')):
        raise InvalidReport('ORIGINAL_RPT_PROVENANCE_MISMATCH')

    plan_items = {item.check_id: item for item in plan.items}
    report_rows = {row.check_id: row for row in report.by_check}
    if (len(plan_items) != len(plan.items) or set(plan_items) != set(pack.checks)
            or len(report_rows) != len(report.by_check) or set(report_rows) != {c.id for c in pack.active_checks()}):
        raise InvalidReport('ORIGINAL_CHECK_ROWS_MISMATCH')
    side_env = dict(zip(SIDES, envs, strict=True))
    selected = {cid for cid, item in plan_items.items() if item.selected}
    evidence, results = {}, {}
    for obj in inputs:
        kind = getattr(obj, 'kind', None)
        if kind in {'EVD', 'RES'}:
            pair = obj.check_id, obj.side.value
            table = evidence if kind == 'EVD' else results
            if pair in table or pair[0] not in selected or pair[1] not in side_env:
                raise InvalidReport('ORIGINAL_SIDE_REFS_AMBIGUOUS')
            if kind == 'EVD':
                env_ref = side_env[pair[1]].ref()
                expected_refs = [plan.ref(), env_ref]
                passive_values = _passive_hop_values(obj)
                if passive_values is not None:
                    try:
                        source_ref = Ref.model_validate(passive_values.get('source_evidence'))
                    except ValueError:
                        raise InvalidReport('ORIGINAL_EVIDENCE_BINDING_MISMATCH') from None
                    if (source_ref.kind != 'EVD' or not source_ref.id or source_ref.revision < 1
                            or SHA256.fullmatch(source_ref.sha256) is None or len(obj.refs) != 3):
                        raise InvalidReport('ORIGINAL_EVIDENCE_BINDING_MISMATCH')
                    expected_refs.append(source_ref)
                if (obj.plan != plan.ref() or obj.environment != env_ref or len(obj.refs) != len(expected_refs)
                        or (report.execution_attempt_id is not None and obj.execution.attempt_id != report.execution_attempt_id)
                        or {_key(ref) for ref in obj.refs} != {_key(ref) for ref in expected_refs}):
                    raise InvalidReport('ORIGINAL_EVIDENCE_BINDING_MISMATCH')
            table[pair] = obj
    rows = {}
    base, cand = envs
    model_keys = ('id', 'revision', 'inventory_sha256', 'tokenizer_revision', 'components')
    differences = ['model.' + key for key in model_keys if getattr(base.model, key) != getattr(cand.model, key)]
    if base.runtime != cand.runtime:
        differences.append('runtime')
    for check_id in sorted(selected):
        item, check, shown = plan_items[check_id], pack.checks[check_id], report_rows[check_id]
        applicability, conditions, _ = evaluate_check(check, envs)
        if (not shown.selected or not _in_scope(check_id, policy) or shown.applicability != item.applicability
                or item.applicability != applicability or item.conditions != conditions or shown.gaps != item.gaps
                or check.revision != item.check_revision or check.executor != item.executor):
            raise InvalidReport('ORIGINAL_SELECTION_MISMATCH')
        policy_values = policy.values.get(check_id, {})
        blockers = []
        for param in check.policy_params:
            if param.required or param.key in policy_values:
                try:
                    check_policy_value(check_id, param.key, param.type, policy_values.get(param.key))
                except ValueError:
                    blockers.append('ORIGINAL_POLICY_VALUE_UNAVAILABLE')
        sides = {}
        for side, env in side_env.items():
            source = None
            if check_id == 'PLT-08':
                # PLT-01 sorts first and its exact displayed RES/EVD has been replayed.
                original = rows.get('PLT-01', {}).get(side)
                if original is not None and not original['blocking_codes']:
                    source = evidence.get(('PLT-01', side))
            sides[side] = _verify_side(check, item, evidence.get((check_id, side)), results.get((check_id, side)),
                                       getattr(shown, side), env, policy, plan, report,
                                       source=source, artifact_store=artifact_store)
        base_evd, cand_evd = (evidence.get((check_id, side)) for side in SIDES)
        observed_diff = comparison_differences(base_evd.comparability, cand_evd.comparability) if base_evd and cand_evd else []
        same_attempt = base_evd and cand_evd and base_evd.execution.attempt_id == cand_evd.execution.attempt_id
        if not same_attempt and (base_evd or cand_evd or report.security_verdict_version is not None):
            observed_diff = ['EXECUTION_ATTEMPT_INCOMPLETE_OR_DIFFERENT']
            functional = 'NOT_COMPARABLE' if base_evd or cand_evd else None
            if (shown.comparison != 'NOT_COMPARABLE' or shown.functional_comparison != functional
                    or shown.comparison_reasons != observed_diff):
                blockers.append('ORIGINAL_RPT_COMPARISON_MISMATCH')
        elif all(side['verdicts'] is not None for side in sides.values()):
            for axis, name in (('security', 'comparison'), ('functional', 'functional_comparison')):
                baseline = sides['baseline']['verdicts'][axis] or 'UNKNOWN'
                candidate = sides['candidate']['verdicts'][axis] or 'UNKNOWN'
                if axis == 'security':
                    baseline = security_comparison_verdict(Verdict(baseline), sides['baseline']['security_failure_tags'])
                    candidate = security_comparison_verdict(Verdict(candidate), sides['candidate']['security_failure_tags'])
                replay = compare(baseline, candidate, not observed_diff)
                if getattr(shown, name) != replay:
                    blockers.append('ORIGINAL_RPT_COMPARISON_MISMATCH')
            if shown.comparison_reasons != observed_diff:
                blockers.append('ORIGINAL_RPT_COMPARISON_REASONS_MISMATCH')
        rows[check_id] = {
            'selected': True,
            'comparison': shown.comparison, 'functional_comparison': shown.functional_comparison,
            'comparison_reasons': [*observed_diff, *differences],
            'applicability': item.applicability.value, 'selection_gaps': [gap.value for gap in item.gaps],
            'blocking_codes': list(dict.fromkeys(blockers)), **sides,
        }
    for item in plan.items:
        if item.not_selected_reason != 'NOT_APPLICABLE' or item.check_id not in pack.checks:
            continue
        check = pack.checks[item.check_id]
        row = report_rows.get(item.check_id)
        if (row is None or row.selected or row.applicability != 'NOT_APPLICABLE' or row.gaps
                or row.baseline is not None or row.candidate is not None or row.single is not None
                or item.applicability != 'NOT_APPLICABLE' or item.gaps or item.check_revision != check.revision
                or item.executor != check.executor or not _in_scope(item.check_id, policy)):
            continue
        evaluations = [evaluate_check(check, [env]) for env in envs]
        if (item.conditions == [condition for _, conditions, _ in evaluations for condition in conditions]
                and all(applicability == 'NOT_APPLICABLE'
                        and any(c.result == 'FALSE' and c.origin in ('MEASURED', 'DECLARED') for c in conditions)
                        for applicability, conditions, _ in evaluations)):
            rows[item.check_id] = {'selected': False, 'applicability': 'NOT_APPLICABLE', **{
                side: {'environment': _dump(side_env[side].ref()),
                       'conditions': [{'fact': c.fact, 'origin': c.origin, 'result': c.result} for c in conditions]}
                for side, (_, conditions, _) in zip(SIDES, evaluations, strict=True)}}
    refs = {'pack': _dump(pack.ref()), 'policy': _dump(policy.ref()), 'plan': _dump(plan.ref()), 'report': _dump(report.ref())}
    return rows, refs, official


def review(run_inputs, *, pack, expected, assignments=None):
    """Aggregate immutable observations without changing strict execution acceptance."""
    active = [check.id for check in pack.active_checks()]
    if len(active) != len(set(active)):
        raise ValueError('ACTIVE_CHECK_IDS_DUPLICATED')
    assigned = {}
    if assignments is not None:
        if type(assignments) is not dict or set(assignments) - set(active):
            raise ValueError('REPORT_ASSIGNMENTS_INVALID')
        for check_id, value in assignments.items():
            ref = Ref.model_validate(value)
            if ref.kind != 'RPT' or not ref.id or ref.revision < 1 or not SHA256.fullmatch(ref.sha256):
                raise ValueError('REPORT_ASSIGNMENTS_INVALID')
            assigned[check_id] = _dump(ref)
    reviews, policies = [], []
    by_check = defaultdict(list)
    for report, objects, original_pack, *stores in run_inputs:
        if original_pack.ref() != pack.ref():
            reviews.append({'verified': False, 'reason': 'PACK_REF_MISMATCH'})
            continue
        try:
            rows, refs, official = _verified_rows(report, objects, pack, expected, stores[0] if stores else None)
        except InvalidReport as error:
            reviews.append({'verified': False, 'reason': str(error)})
            continue
        except (ValueError, KeyError, TypeError, AttributeError):
            reviews.append({'verified': False, 'reason': 'EVIDENCE_CONTRACT_INVALID'})
            continue
        policies.append(refs['policy'])
        reviews.append({'verified': True, 'official': official, 'refs': refs})
        for check_id, row in rows.items():
            by_check[check_id].append({**row, 'report': refs['report']})

    inventory, unknown, unconfirmed, unresolved = {}, [], [], []
    for check_id in sorted(active):
        candidates = by_check[check_id]
        all_refs = [row['report'] for row in candidates]
        assigned_ref = assigned.get(check_id)
        if assigned_ref is not None:
            candidates = [row for row in candidates if row['report'] == assigned_ref]
        if len(candidates) != 1:
            status = 'ORIGINAL_ROW_MISSING' if not candidates else 'ORIGINAL_ROW_AMBIGUOUS'
            if assigned_ref is not None and not candidates:
                status = 'ASSIGNED_ORIGINAL_ROW_UNAVAILABLE'
            inventory[check_id] = {'status': status, 'reports': all_refs}
            unresolved.append(check_id)
            continue
        row, = candidates
        if not row['selected']:
            inventory[check_id] = {'status': 'SELECTION_VERIFIED', **row}
            continue
        unknown_sides = [side for side in SIDES if row[side]['reported_security'] == 'UNKNOWN']
        if unknown_sides:
            unknown.append(check_id)
        unconfirmed_sides = [side for side in SIDES if row[side]['security_failure_tags']
                             and 'CONFIRMED_VIOLATION' not in row[side]['security_failure_tags']]
        blockers = row['blocking_codes']
        if unconfirmed_sides:
            unconfirmed.append(check_id)
            blockers.append('SECURITY_PASS_NOT_ESTABLISHED')
        if any(row[side]['blocking_codes'] for side in SIDES):
            blockers.append('ORIGINAL_SIDE_UNRESOLVED')
        if row['comparison_reasons'] or 'NOT_COMPARABLE' in (row['comparison'], row['functional_comparison']):
            blockers.append('COMPARISON_UNRESOLVED')
        if row['applicability'] != 'APPLICABLE' or row['selection_gaps']:
            blockers.append('SELECTION_UNRESOLVED')
        if blockers:
            unresolved.append(check_id)
        status = 'ORIGINAL_ROW_UNRESOLVED' if blockers else 'MEASURED_ROWS_VERIFIED'
        inventory[check_id] = {'status': status, **row,
                               'security_unknown_sides': unknown_sides, 'unconfirmed_security_failure_sides': unconfirmed_sides,
                               'blocking_codes': blockers}

    exact_policy = bool(policies) and all(policy == policies[0] for policy in policies)
    verified_inputs = bool(reviews) and all(report['verified'] for report in reviews)
    complete = not unresolved and exact_policy and verified_inputs
    execution_coverage = {}
    for side in SIDES:
        coverage = {'not_run': [], 'aborted': [], 'unverified': [], 'verified_measured': [], 'verified_not_applicable': []}
        for check_id, row in inventory.items():
            source = row.get(side, {})
            execution_status = source.get('execution_status')
            if row['status'] == 'SELECTION_VERIFIED':
                coverage['verified_not_applicable'].append(check_id)
            elif execution_status in {'NOT_RUN', 'ABORTED'}:
                coverage[execution_status.lower()].append(check_id)
            elif execution_status == 'COMPLETED' and not source['blocking_codes']:
                coverage['verified_measured'].append(check_id)
            else:
                coverage['unverified'].append(check_id)
        execution_coverage[side] = coverage
    execution_complete = exact_policy and verified_inputs and all(
        not (row['not_run'] or row['aborted'] or row['unverified']) for row in execution_coverage.values())
    return {
        'schema': 'guard-mvp-compare-review/1', 'pack_ref': _dump(pack.ref()),
        'policy_ref': policies[0] if exact_policy else None, 'active_checks': len(active), 'reports': reviews,
        'assigned_reports': assigned,
        'unknown_security_check_ids': unknown, 'unknown_security_count': len(unknown),
        'unconfirmed_security_failure_check_ids': unconfirmed,
        'unknown_security_limit': UNKNOWN_LIMIT, 'unresolved_check_ids': unresolved,
        'execution_coverage': execution_coverage, 'paired_execution_complete': execution_complete,
        'comparison_complete': complete, 'unknown_security_limit_met': complete and len(unknown) <= UNKNOWN_LIMIT,
        'all_reports_official': verified_inputs and all(report['official'] for report in reviews), 'rows': inventory,
    }


def _read_runs(pairs, *, include_artifact_store=False):
    for store, report_path in pairs:
        objects = read_objects(store)
        report = json.loads(report_path.read_bytes(), object_pairs_hook=_pairs)
        saved = object_from_dict(report)
        if saved.kind != 'RPT' or not saved.hash_ok() or report not in objects:
            raise InvalidReport('ORIGINAL_RPT_MISMATCH')
        plan_ref, = [ref for ref in saved.inputs_manifest if ref.kind == 'PLAN']
        # The existing verifier takes one PLAN to locate the pinned pack. Keep
        # every other object in its artifact scan and retain the entire saved
        # graph for the subsequent exact-ref and object-seal checks.
        verification_objects = [obj for obj in objects if obj['kind'] != 'PLAN' or (
            obj['id'], obj['revision'], obj['content_sha256']) == (plan_ref.id, plan_ref.revision, plan_ref.sha256)]
        run = report, objects, verify_store_files(store, verification_objects)
        yield (*run, Store.read_only(store)) if include_artifact_store else run


def _replay_archived_plt08(checked, artifact_store, targets):
    """Bind a pair of pinned target files to the original report's PLT-01 controls."""
    reports = checked['reports']
    original = checked['rows']['PLT-01']
    if (len(reports) != 1 or not reports[0]['verified'] or not reports[0]['official']
            or original.get('selected') is not True
            or original.get('report') != reports[0]['refs']['report']
            or any(side not in original or original[side]['blocking_codes'] for side in SIDES)):
        raise InvalidReport('PLT08_ARCHIVED_SOURCE_UNVERIFIED')
    plan_ref = Ref.model_validate(reports[0]['refs']['plan'])
    sources = {side: artifact_store.resolve(Ref.model_validate(original[side]['evidence'])) for side in SIDES}
    attempt_ids = {source.execution.attempt_id for source in sources.values()}
    if len(attempt_ids) != 1 or not next(iter(attempt_ids)):
        raise InvalidReport('PLT08_ARCHIVED_SOURCE_UNVERIFIED')
    attempt_id, = attempt_ids
    receipts, captured = {}, []
    for side in SIDES:
        path, digest = targets[side]
        path = _plain_path(Path(path))
        snapshot = _read(path, max_bytes=passive_api_hop._TARGET_BYTES)
        raw, _ = snapshot
        if SHA256.fullmatch(digest) is None or sha256_hex(raw) != digest:
            raise ValueError('PLT08_ARCHIVED_TARGET_FILE_CHANGED')
        target = strict_json(raw, limit=passive_api_hop._TARGET_BYTES)
        target_sha256 = sha256_hex(canonical_json(target))
        source = sources[side]
        observations = passive_api_hop.read_passive_api_hop(artifact_store, plan_ref=plan_ref, side=Side(side),
            attempt_id=attempt_id, source_ref=source.ref(), target=target, expected_target_sha256=target_sha256)
        trial, = observations['api_transport_trials']['value']
        receipts[side] = {'environment_ref': _dump(source.environment), 'source_evidence': _dump(source.ref()),
                          'source_artifact_sha256': trial['source_artifact_sha256'],
                          'target_file_sha256': digest, 'target_sha256': trial['target_sha256'],
                          'observations': observations}
        captured.append((path, snapshot))
    try:
        stable = all(_read(path, max_bytes=passive_api_hop._TARGET_BYTES) == snapshot for path, snapshot in captured)
    except (OSError, ValueError):
        stable = False
    if not stable:
        raise ValueError('PLT08_ARCHIVED_TARGET_FILE_CHANGED')
    return {'report_ref': reports[0]['refs']['report'], 'plan_ref': _dump(plan_ref),
            'attempt_id': attempt_id, 'sides': receipts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', metavar=('RESTORED_STORE', 'ORIGINAL_REPORT'), nargs=2,
                        action='append', type=Path, required=True)
    parser.add_argument('--assignments', type=Path, help='JSON mapping from check ID to exact original RPT Ref')
    parser.add_argument('--plt08-targets', nargs=4, metavar=('BASELINE_PATH', 'BASELINE_SHA256', 'CANDIDATE_PATH', 'CANDIDATE_SHA256'),
                        help='Recheck the original PLT-01 pair against two pinned saved target files from one run')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.plt08_targets is not None and len(args.run) != 1:
        parser.error('--plt08-targets requires exactly one original --run')
    pack = load_pack(ROOT / 'packs/PACK-BASELINE')
    if len(pack.active_checks()) != 69:
        raise ValueError('MVP_ACTIVE_SCOPE_MISMATCH')
    if any(args.output.resolve().is_relative_to(store.resolve()) for store, _ in args.run):
        parser.error('--output must be outside the restored stores')
    assignments = json.loads(args.assignments.read_bytes(), object_pairs_hook=_pairs) if args.assignments else None
    if args.assignments and type(assignments) is not dict:
        parser.error('--assignments must contain a JSON object')
    runs = _read_runs(args.run, include_artifact_store=True)
    if args.plt08_targets is not None:
        runs = list(runs)
    result = review(runs, pack=pack, expected=versions(), assignments=assignments)
    if args.plt08_targets is not None:
        baseline_path, baseline_sha256, candidate_path, candidate_sha256 = args.plt08_targets
        result['archived_plt08_source_replay'] = _replay_archived_plt08(result, runs[0][3], {
            'baseline': (baseline_path, baseline_sha256), 'candidate': (candidate_path, candidate_sha256)})
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
