"""Use owner-bound TP2 preflight census times to fill the approved LIF-11 window."""

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from guard.adapters import resource_recovery_probe as probe
from guard.executors.resource_recovery_contract import stamp
from guard.models import DataOrigin, Environment, Policy, canonical_json, load_pack, sha256_hex
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_resource_recovery import PROFILE, PROBE_SOURCE_SHA256
from tools.mvp_a_input_preflight import _read
from tools.mvp_runtime_policy import ROOT, POLICY_MAX_BYTES, approved_rules, fill_runtime_values, seed_run_store
from tools.mvp_versions import versions

CHECK = 'LIF-11'
START = 'recovery_validity_start'
END = 'recovery_validity_end'
ABSENT_AFTER = 'recovery_required_absent_after'
SCHEMA = 'guard-mvp-tp2-recovery-preflight/1'
MAX_SOURCE_BYTES = 1024 * 1024
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _unix_ns(value):
    elapsed = value - EPOCH
    return ((elapsed.days * 86400 + elapsed.seconds) * 1000000 + elapsed.microseconds) * 1000


def _census_time(source, env, target, ranks, skew_ms):
    ref = env.ref().model_dump(mode='json')
    if (type(source) is not dict or set(source) != {'environment_ref', 'target_sha256', 'selection', 'census'}
            or source['environment_ref'] != ref or type(target) is not dict or target.get('environment_ref') != ref
            or source['target_sha256'] != sha256_hex(canonical_json(target))):
        raise ValueError('MVP_RECOVERY_PREFLIGHT_SCOPE_MISMATCH')
    selection, census = source['selection'], source['census']
    probe.validate_selection(selection)
    if ({str(owner['rank']) for owner in selection['owners']} != set(ranks) or type(census) is not dict
            or census.get('profile') != PROFILE or census.get('probe_source_sha256') != PROBE_SOURCE_SHA256
            or census.get('selection_sha256') != sha256_hex(canonical_json(selection))
            or any(census.get(key) is not True for key in ('collected', 'owner_binding_verified', 'clock_window_verified'))
            or census.get('diagnostic_codes') != []):
        raise ValueError('MVP_RECOVERY_PREFLIGHT_CENSUS_INCOMPLETE')
    packet = probe.validate_result(census.get('packet'), selection)
    start = stamp(census.get('started_at')).astimezone(timezone.utc)
    finish = stamp(census.get('finished_at')).astimezone(timezone.utc)
    skew_ns = skew_ms * 1000000
    if (finish < start or any(owner['status'] != 'match' for owner in packet['owners'])
            or not _unix_ns(start) - skew_ns <= packet['started_wall_ns'] <= packet['finished_wall_ns'] <= _unix_ns(finish) + skew_ns):
        raise ValueError('MVP_RECOVERY_PREFLIGHT_CENSUS_INCOMPLETE')
    return start, finish


def materialize(policy, pack, environments, targets, preflight, *, issued_at=None):
    rules = approved_rules(policy, pack, CHECK)
    values = policy.values[CHECK]
    if (rules != {START: {'rule': 'fresh_preflight_start_rfc3339', 'max_clock_skew_ms': 100},
                  END: {'rule': 'start_plus_ms', 'milliseconds': 600000},
                  ABSENT_AFTER: {'rule': 'start_plus_ms', 'milliseconds': 30000}}
            or values['recovery_max_clock_skew_ms'] != rules[START]['max_clock_skew_ms']):
        raise ValueError('MVP_RECOVERY_RULE_UNSUPPORTED')
    expected = versions()
    if (type(preflight) is not dict or set(preflight) != {'schema', 'base_policy_ref', 'sides'}
            or preflight['schema'] != SCHEMA or preflight['base_policy_ref'] != policy.ref().model_dump(mode='json')
            or type(preflight['sides']) is not dict or set(preflight['sides']) != set(expected)
            or set(environments) != set(expected) or set(targets) != set(expected)):
        raise ValueError('MVP_RECOVERY_PREFLIGHT_INVALID')
    ranks = values['recovery_expected_ranks']
    pair = []
    process_generations = set()
    # PIDs are namespace-local and can repeat across independent boots.
    identity_keys = ('boot_sha256', 'pid_namespace', 'pid', 'start_ticks')
    for side, env in environments.items():
        engine = expected[side]
        if (not env.hash_ok() or env.data_origin == DataOrigin.EXAMPLE
                or (env.engine.name, env.engine.version, env.engine.image_digest) != (
                    engine['engine'], engine['version'], engine['image'])
                or env.runtime.tp_size != len(ranks) or (env.runtime.gpu_count or 0) < len(ranks)):
            raise ValueError('MVP_RECOVERY_PREFLIGHT_TP2_REQUIRED')
        source = preflight['sides'][side]
        pair.append(_census_time(source, env, targets[side], ranks, rules[START]['max_clock_skew_ms']))
        selected_generations = {
            tuple(owner['context'][key] for key in identity_keys)
            for owner in source['selection']['owners']
        }
        if process_generations & selected_generations:
            raise ValueError('MVP_RECOVERY_PREFLIGHT_PROCESS_GENERATION_REUSED')
        process_generations.update(selected_generations)
    start = min(began for began, _ in pair)
    deadline = start + timedelta(milliseconds=rules[ABSENT_AFTER]['milliseconds'])
    end = start + timedelta(milliseconds=rules[END]['milliseconds'])
    issued_at = issued_at or datetime.now(timezone.utc)
    if (not max(finished for _, finished in pair) <= issued_at < deadline
            or (policy.valid_from is not None and start < policy.valid_from)
            or (policy.valid_until is not None and end > policy.valid_until)):
        raise ValueError('MVP_RECOVERY_PREFLIGHT_OUTSIDE_WINDOW')
    return fill_runtime_values(policy, CHECK, {START: start.isoformat(), END: end.isoformat(),
                                               ABSENT_AFTER: deadline.isoformat()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', type=Path, required=True)
    for side in ('baseline', 'candidate'):
        parser.add_argument(f'--{side}-environment', type=Path, required=True)
        parser.add_argument(f'--{side}-target', type=Path, required=True)
    parser.add_argument('--preflight', type=Path, required=True)
    parser.add_argument('--preflight-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--store', type=Path, help='Append the exact base and new POL to the prepared Guard run store')
    args = parser.parse_args()
    policy = Policy.model_validate(strict_json(_read(args.policy)[0], limit=POLICY_MAX_BYTES))
    sides = ('baseline', 'candidate')
    envs = {side: Environment.model_validate(strict_json(_read(getattr(args, side + '_environment'))[0])) for side in sides}
    targets = {side: strict_json(_read(getattr(args, side + '_target'))[0]) for side in sides}
    raw = _read(args.preflight)[0]
    if sha256_hex(raw) != args.preflight_sha256:
        raise ValueError('MVP_RECOVERY_PREFLIGHT_SOURCE_CHANGED')
    pack = load_pack(ROOT / 'packs/PACK-BASELINE')
    revised = materialize(policy, pack, envs, targets, strict_json(raw, limit=MAX_SOURCE_BYTES))
    if args.output.exists():
        raise FileExistsError(args.output)
    revised = seed_run_store(args.store, policy, revised)
    with args.output.open('xb') as stream:
        stream.write(canonical_json(revised.to_dict()))


if __name__ == '__main__':
    main()
