"""Requested configuration commitment and per-attempt exclusion, engine neutral."""
from contextlib import contextmanager
from pathlib import Path
import os
import inspect
from guard.collectors.check_observations import MAX_RECORD_BYTES, VerifiedRecord, path_identity
from guard.configuration import RESPONSE_SNAPSHOT_INPUT, RESPONSE_SNAPSHOT_OPTION, merge_config, requested_input_paths
from guard.models import Mode, canonical_json, sha256_hex, utcnow


def configuration_sha256(cfg, environments=(), *, target_snapshots=None, normal_file_hashes=None,
                         transition_manifests=None, plan=None, initialization_digests=None,
                         response_route_digests=None, supply_record_digests=None,
                         missing_native_route_prepared_sides=None, rank_common_files=None,
                         record_access_digests=None, retry_snapshot_digests=None, response_snapshot_digests=None,
                         copy_snapshot_digests=None, selected_context_sha256=None,
                         software_digests: dict[Path, tuple[bytes, str]] | None = None,
                         record_reads: dict[Path, VerifiedRecord] | None = None):
    """Commit values and explicitly submitted input bytes, without saving secrets.

    Runtime injected fields are never requested configuration. Managed target
    paths added after selection are absent from the original requested config.
    Requested snapshot paths receive the same private bytes used for their hash.
    An execution can reuse software digests after comparing fresh file bytes.
    A requested observation can also use the exact file bytes verified here.
    """
    package = Path(__file__).parents[1]
    software_suffixes = {'.py', '.json', '.yaml', '.txt'}
    file_digests: dict[Path, str] = {}
    software_candidates: dict[Path, tuple[bytes, str]] = {}
    remaining_record_bytes = MAX_RECORD_BYTES

    def digest_file(path: Path, *, common_record=False):
        nonlocal remaining_record_bytes
        collect_record = record_reads is not None and common_record
        if path in file_digests:
            if collect_record:
                record_reads.setdefault(path, VerifiedRecord(file_digests[path]))
            return file_digests[path]
        before = path_identity(path) if collect_record and remaining_record_bytes else None
        raw = path.read_bytes()
        cache = software_digests if path.is_relative_to(package) and path.suffix in software_suffixes else None
        previous = cache.get(path) if cache is not None else None
        current = previous if previous is not None and raw == previous[0] else (raw, sha256_hex(raw))
        if cache is not None:
            software_candidates[path] = current
        file_digests[path] = current[1]
        if collect_record:
            if before is not None and 0 < len(raw) <= remaining_record_bytes and path_identity(path) == before:
                record_reads[path] = VerifiedRecord(current[1], raw, before)
                remaining_record_bytes -= len(raw)
            else:
                record_reads[path] = VerifiedRecord(current[1])
        return current[1]

    def normalize(value):
        if isinstance(value, dict):
            return {k: normalize(v) for k, v in value.items()}
        if isinstance(value, list): return [normalize(v) for v in value]
        if isinstance(value, Path): return str(value)
        return value
    inputs = {}
    captured_digests = {}
    from guard.configuration import (NORMAL_REQUEST_BINDING_OPTION, RECORD_SNAPSHOT_OPTIONS, RETRY_RECORD_SNAPSHOT_OPTION,
                                     for_check, normal_file_reads_requested, normal_request_binding_requested)
    from guard.executors.run_binding import producer_sha256

    normal_file_reads = normal_file_reads_requested(cfg)
    response_targets = set()
    overlays = cfg.get('check_config')
    if type(overlays) is dict and overlays.get('REQ-13') == {RESPONSE_SNAPSHOT_OPTION: True}:
        for side in ('baseline', 'candidate'):
            settings = merge_config(cfg, cfg.get('sides', {}).get(side, {})).get('response_content')
            if (type(settings) is dict and RESPONSE_SNAPSHOT_INPUT in settings
                    and isinstance(settings.get('target'), (str, Path))):
                response_targets.add(Path(settings['target']))
    request_binding_targets = set()
    if normal_request_binding_requested(cfg):
        for side in ('baseline', 'candidate'):
            effective = for_check(merge_config(cfg, cfg.get('sides', {}).get(side, {})), 'request_binding', 'REQ-11')
            settings = effective.get('request_binding')
            if (type(settings) is dict and settings.get(NORMAL_REQUEST_BINDING_OPTION) is True
                    and isinstance(settings.get('target'), (str, Path))):
                request_binding_targets.add(Path(settings['target']))
    bound_target_bytes = {}
    for location, path, deferred in requested_input_paths(cfg):
        name = str(path)
        key = location[-1]
        if deferred:
            inputs.setdefault(name, {'binding': 'owned-candidate-after-baseline/1'})
            continue
        normal_file_input = normal_file_reads and key in {'prepared', 'reference_manifest'} and location[-3:-1] in {
            ('supply_boot', 'normal'), ('SUP-05', 'normal')}
        if path in response_targets or path in request_binding_targets:
            if path not in bound_target_bytes:
                from guard.executors.kubernetes_response_snapshot import snapshot_target_bytes

                try:
                    raw = snapshot_target_bytes(str(path))
                    bound_target_bytes[path] = raw if path in response_targets or len(raw) <= 65536 else None
                except (OSError, ValueError):
                    bound_target_bytes[path] = None
            target = bound_target_bytes[path]
            if target_snapshots is not None and path in target_snapshots:
                target_snapshots[path] = target
            inputs[name] = sha256_hex(target) if target is not None else None
        elif key in {'record_producer', 'copy_inventory'} or normal_file_input:
            if normal_file_input and normal_file_hashes is not None and name in normal_file_hashes:
                digest = normal_file_hashes[name]
            else:
                try:
                    digest = producer_sha256(name)
                except (OSError, ValueError):
                    digest = None
                if normal_file_input and normal_file_hashes is not None:
                    normal_file_hashes[name] = digest
            inputs[name] = digest
        elif key == 'target' and target_snapshots is not None and path in target_snapshots:
            if path not in captured_digests:
                raw = path.read_bytes() if path.is_file() else None
                target_snapshots[path] = raw
                captured_digests[path] = sha256_hex(raw) if raw is not None else None
            inputs[name] = captured_digests[path]
        else:
            common_record = (location[-2:-1] == ("records",) and "common_inputs" in location
                             or location[-2:] in {("runner_input", "path"), ("supply_reads_input", "path")})
            if path.is_file():
                inputs[name] = digest_file(path, common_record=common_record)
            else:
                inputs[name] = None
                if record_reads is not None and common_record:
                    record_reads[path] = VerifiedRecord(None)
    implementations = {}
    from guard.executors.registry import resolve_executor
    def factories(config, side, env=None):
        engine = env.engine.name if env else config.get('adapter', {}).get('name')
        for name, impl in config.get('executors', {}).items():
            factory = resolve_executor(name, impl, engine=engine, version=env.engine.version if env else None)
            try: source = Path(inspect.getsourcefile(factory))
            except (TypeError, OSError): source = None
            implementations[side + ':' + name] = {'implementation': impl,
                'factory': getattr(factory, '__qualname__', None),
                'source_sha256': digest_file(source) if source is not None and source.is_file() else None}
            if engine is not None:
                from guard.adapters.engine_provider import PROVIDERS, get_provider, load_installed_providers
                load_installed_providers()
                if engine in PROVIDERS:
                    closure = get_provider(engine).execution_recipe_files(factory)
                    implementations[side + ':' + name]['provider_sources'] = sorted(
                        ({'name': Path(path).name, 'sha256': digest_file(Path(path))} for path in closure),
                        key=lambda item: (item['name'], item['sha256']))
    factories(cfg, 'root', environments[0] if len(environments) == 1 else None)
    from guard.executors.kubernetes_response_content import route_target_bytes
    from guard.executors.lifecycle_transition import REDEPLOY_CHECKS, REDEPLOY_SIDE_REUSE, inputs_manifest, redeployment_side_reuse
    from guard.executors.rank_common_input import recipe_source_files
    from guard.executors.run_binding import (INITIALIZATION_CHECKS, OBS06_STAGE4_MIN_CHECK_REVISION, SUPPLY_RECORD_CHECKS,
                                             file_hash, initialization_recipe_digests, native_route_preparation_missing,
                                             supply_record_recipe_digests)
    sides = {s: None for s in cfg.get('sides', {})}
    if len(environments) == 2: sides.update(zip(('baseline', 'candidate'), environments))
    transition_inputs = {}
    redeploy_identities = {}
    redeploy_conflicts = set()
    update = cfg.get('update', {})
    permitted = update.get('allowed_checks')
    paired_external = len(environments) == 2 and update.get('mode', 'external') == 'external'
    eligible = {}
    if paired_external and plan is not None and plan.mode == Mode.update:
        eligible = {item.check_id: item.executor for item in plan.items if item.selected and not item.gaps
                    and (type(permitted) is not list or item.check_id in permitted)}
    init_checks = [check_id for check_id, executor in eligible.items()
                   if executor == 'initialization' and check_id in INITIALIZATION_CHECKS]
    transition_checks = REDEPLOY_CHECKS
    if eligible.get('LIF-06') == 'lifecycle_transition':
        transition_checks = REDEPLOY_CHECKS | {'LIF-06'}
    init_inputs = {}
    response_routes = {}
    response_snapshots = {}
    supply_inputs = {}
    record_access_inputs = {}
    retry_snapshot_inputs = {}
    missing_route_prepared = set()
    rank_inputs = {}
    rank_update = eligible.get('LIF-08') == 'rank_failure'
    rank_audit = len(environments) == 1 and plan is not None and plan.mode == Mode.audit and any(
        item.check_id == 'LIF-08' and item.executor == 'rank_failure' and item.selected and not item.gaps
        for item in plan.items)

    def bind_rank_sources(side, effective):
        if (effective.get('executors', {}).get('rank_failure') == 'common-input'
                and not effective.get('lab_activation') and not effective.get('record_attestation')):
            settings = for_check(effective, 'rank_failure', 'LIF-08').get('rank_failure', {})
            rank_inputs[side] = recipe_source_files(settings)

    if rank_audit:
        bind_rank_sources('single', cfg)
    prebound = update.get('kubernetes_candidate_binding', 'prebound') == 'prebound'
    prebound_response = prebound and eligible.get('REQ-13') == 'response_content'
    prebound_route_record = prebound and eligible.get('REQ-02') == 'route_record'
    prebound_record_access = (prebound and eligible.get('OBS-06') == 'record_access'
                              and plan.item('OBS-06').check_revision >= OBS06_STAGE4_MIN_CHECK_REVISION)
    prebound_retry_snapshot = prebound and eligible.get('LIF-12') == 'request_termination'
    collect_copy_snapshot = (copy_snapshot_digests is not None and prebound
                             and eligible.get('PLT-12') == 'required_event_record')
    supply_checks = [check_id for check_id, executor in eligible.items()
                     if prebound and executor == 'supply_boot' and check_id in SUPPLY_RECORD_CHECKS]
    for side, env in sides.items():
        effective = merge_config(cfg, cfg.get('sides', {}).get(side, {}))
        factories(effective, side, env)
        if rank_update and side in ('baseline', 'candidate'):
            bind_rank_sources(side, effective)
        bindings = effective.get('run_binding')
        # Stage-2 transition inputs use a list of per-generation files rather
        # than the one target path committed by the generic recipe visitor.
        if paired_external and side in ('baseline', 'candidate') and effective.get('executors', {}).get('lifecycle_transition') == 'kubernetes':
            for check_id in sorted(transition_checks):
                if (type(bindings) is not dict or check_id not in bindings
                        or type(permitted) is list and check_id not in permitted):
                    continue
                identities = set() if check_id in REDEPLOY_CHECKS else None
                try:
                    settings = for_check(effective, 'lifecycle_transition', check_id)['lifecycle_transition']
                    digest = sha256_hex(canonical_json(inputs_manifest(settings, generation_identities=identities)))
                except (KeyError, TypeError, ValueError, OSError):
                    digest = None
                if identities is not None:
                    previous = redeploy_identities.setdefault(check_id, set())
                    if redeployment_side_reuse(previous, identities):
                        redeploy_conflicts.add(check_id)
                    previous.update(identities)
                transition_inputs[side + ':' + check_id] = digest
        if (init_checks and side in ('baseline', 'candidate') and type(bindings) is dict
                and effective.get('executors', {}).get('initialization') == 'kubernetes'):
            for check_id in init_checks:
                if check_id not in bindings:
                    continue  # Legacy policy-supplied scenario digests have no file to pin.
                try:
                    settings = for_check(effective, 'initialization', check_id)['initialization']
                    digests = initialization_recipe_digests(settings, bindings[check_id])
                except (KeyError, TypeError, ValueError, OSError):
                    digests = None
                init_inputs[side + ':' + check_id] = digests
        if (prebound_response and side in ('baseline', 'candidate')
                and effective.get('executors', {}).get('response_content') == 'kubernetes'):
            try:
                settings = for_check(effective, 'response_content', 'REQ-13').get('response_content', {})
                if (type(settings) is dict
                        and (RESPONSE_SNAPSHOT_OPTION in settings or RESPONSE_SNAPSHOT_INPUT in settings)):
                    # Only a selected prebound snapshot submits this manifest.
                    # Child files are read by its qualified passive collector.
                    response_snapshots[side] = None
                    if (set(settings) == {'target', RESPONSE_SNAPSHOT_OPTION, RESPONSE_SNAPSHOT_INPUT}
                            and settings[RESPONSE_SNAPSHOT_OPTION] is True):
                        from guard.executors.kubernetes_response_snapshot import snapshot_manifest_bytes
                        from guard.runtime.bounded import strict_json

                        target = bound_target_bytes.get(Path(settings['target']))
                        if target is not None:
                            manifest = snapshot_manifest_bytes(settings[RESPONSE_SNAPSHOT_INPUT]['path'])
                            response_snapshots[side] = {
                                'target_file_sha256': sha256_hex(target),
                                'target_sha256': sha256_hex(canonical_json(strict_json(target, limit=len(target)))),
                                'manifest_sha256': sha256_hex(manifest),
                                'policy_ref': plan.inputs.policy.model_dump(mode='json'),
                            }
                elif 'route_target' in settings:
                    response_routes[side] = sha256_hex(route_target_bytes(settings['route_target']))
            except (KeyError, OSError, TypeError, ValueError):
                if side not in response_snapshots:
                    response_routes[side] = None
        if (prebound_route_record and side in ('baseline', 'candidate') and type(bindings) is dict
                and effective.get('executors', {}).get('route_record') == 'kubernetes'):
            run_cfg = bindings.get('REQ-02')
            if type(run_cfg) is dict and set(run_cfg) == {'record_session'}:
                try:
                    settings = for_check(effective, 'route_record', 'REQ-02').get('route_record', {})
                    if type(settings) is dict and native_route_preparation_missing(settings):
                        missing_route_prepared.add(side)
                except (OSError, TypeError, ValueError):
                    pass  # Invalid or unreadable declarations do not qualify as absent.
        if (prebound_record_access and side in ('baseline', 'candidate') and type(bindings) is dict
                and effective.get('executors', {}).get('record_access') == 'kubernetes'):
            run_cfg = bindings.get('OBS-06')
            if type(run_cfg) is dict and set(run_cfg) == {'record_session', 'record_producer'}:
                try:
                    settings = for_check(effective, 'record_access', 'OBS-06')['record_access']
                    if 'subjects' in settings:
                        record_access_inputs[side] = {
                            'target': file_hash('OBS-06', 'record_target_sha256', settings,
                                                require_regular_json=True),
                            'subjects': file_hash('OBS-06', 'record_subject_plan_sha256', settings,
                                                  require_regular_json=True),
                            'record_producer': producer_sha256(run_cfg['record_producer']),
                        }
                except (KeyError, OSError, TypeError, ValueError):
                    record_access_inputs[side] = None
        if (prebound_retry_snapshot and side in ('baseline', 'candidate')
                and effective.get('executors', {}).get('request_termination') == 'kubernetes'):
            try:
                settings = for_check(effective, 'request_termination', 'LIF-12').get('request_termination', {})
                if type(settings) is dict and settings.get(RETRY_RECORD_SNAPSHOT_OPTION) is True:
                    retry_snapshot_inputs[side] = {
                        'retry_scope_sha256': file_hash('LIF-12', 'retry_scope_sha256', settings),
                        'retry_record_source_sha256': producer_sha256(bindings['LIF-12']['record_producer']),
                    }
            except (KeyError, OSError, TypeError, ValueError):
                retry_snapshot_inputs[side] = None
        if (collect_copy_snapshot and side in ('baseline', 'candidate')
                and effective.get('executors', {}).get('required_event_record') == 'kubernetes'):
            # Reuse the raw hashes already committed in inputs, without another file read.
            try:
                settings = for_check(effective, 'required_event_record', 'PLT-12')['required_event_record']
                if settings.get(RECORD_SNAPSHOT_OPTIONS['PLT-12']) is True:
                    inventory_path = str(Path(settings['copy_inventory']))
                    producer_path = str(Path(bindings['PLT-12']['record_producer']))
                    digests = {'copy_inventory_sha256': inputs[inventory_path],
                               'copy_record_source_sha256': inputs[producer_path]}
                    if all(type(digest) is str for digest in digests.values()):
                        copy_snapshot_digests[side] = digests
            except (KeyError, TypeError, ValueError):
                pass  # This side has no complete snapshot file binding.
        if (supply_checks and side in ('baseline', 'candidate') and type(bindings) is dict
                and effective.get('executors', {}).get('supply_boot') == 'kubernetes'):
            for check_id in supply_checks:
                if check_id not in bindings:
                    continue
                try:
                    settings = for_check(effective, 'supply_boot', check_id)['supply_boot']
                    digests = supply_record_recipe_digests(settings, bindings[check_id])
                except (KeyError, TypeError, ValueError, OSError):
                    digests = None
                supply_inputs[side + ':' + check_id] = digests
    if transition_manifests is not None:
        # Commit even stale declarations. Only a selected execution consumes
        # this diagnostic, so unused bindings cannot block another check.
        transition_manifests.update({
            key: REDEPLOY_SIDE_REUSE if key.split(':')[1] in redeploy_conflicts else digest
            for key, digest in transition_inputs.items()})
    # Commit the actual installed common implementation, not a manifest's
    # assertion about its own bytes. Cache only files in this fresh enumeration,
    # including any already read as factory or provider sources above.
    software = {}
    for path in sorted(package.rglob('*')):
        if not path.is_file() or path.suffix not in software_suffixes:
            continue
        software[path.relative_to(package).as_posix()] = digest_file(path)
        if software_digests is not None:
            software_digests[path] = software_candidates[path]
    recipe = {'schema': 'guard-execution-recipe/1', 'requested': normalize(cfg),
              'inputs': inputs, 'implementations': implementations, 'software_sha256': sha256_hex(canonical_json(software))}
    if selected_context_sha256 is not None:
        # The selection context predates the final selected checks. Verify its
        # unchanged input bytes from this same read before including new check pins.
        context_transitions = {key: value for key, value in transition_inputs.items()
                               if not key.endswith(':LIF-06')}
        selection = dict(recipe)
        if context_transitions:
            selection['transition_manifest_sha256_by_side_check'] = context_transitions
        if sha256_hex(canonical_json(selection)) != selected_context_sha256:
            raise ValueError('EXECUTION_RECIPE_CHANGED_RESELECT_REQUIRED')
    if transition_inputs:
        recipe['transition_manifest_sha256_by_side_check'] = transition_inputs
    if init_inputs:
        recipe['initialization_file_sha256_by_side_check'] = init_inputs
    if initialization_digests is not None:
        initialization_digests.update(init_inputs)
    if response_routes:
        recipe['response_route_target_sha256_by_side'] = response_routes
    if response_route_digests is not None:
        response_route_digests.update(response_routes)
    if response_snapshots:
        recipe['gateway_response_snapshot_by_side'] = response_snapshots
    if response_snapshot_digests is not None:
        response_snapshot_digests.update(response_snapshots)
    if supply_inputs:
        recipe['supply_record_file_sha256_by_side_check'] = supply_inputs
    if supply_record_digests is not None:
        supply_record_digests.update(supply_inputs)
    if record_access_inputs:
        recipe['record_access_file_sha256_by_side'] = record_access_inputs
    if record_access_digests is not None:
        record_access_digests.update(record_access_inputs)
    if retry_snapshot_inputs:
        recipe['retry_snapshot_files_by_side'] = retry_snapshot_inputs
    if retry_snapshot_digests is not None:
        retry_snapshot_digests.update(retry_snapshot_inputs)
    if missing_route_prepared:
        recipe['missing_native_route_prepared_sides'] = sorted(missing_route_prepared)
    if missing_native_route_prepared_sides is not None:
        missing_native_route_prepared_sides.update(missing_route_prepared)
    if rank_inputs:
        recipe['rank_common_files_by_side'] = rank_inputs
    if rank_common_files is not None:
        rank_common_files.update(rank_inputs)
    return sha256_hex(canonical_json(recipe))


def validate_policy_time(policy, *, as_of=None):
    now = utcnow() if as_of is None else as_of
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('POLICY_VALIDITY_REQUIRES_TIMEZONE')
    if policy.valid_from is not None and now < policy.valid_from:
        raise ValueError('POLICY_NOT_YET_VALID')
    if policy.valid_until is not None and now >= policy.valid_until:
        raise ValueError('POLICY_EXPIRED')


def bind_recipe(plan, cfg, environments=(), *, target_snapshots=None, normal_file_hashes=None,
                selected_context_sha256=None):
    expected = plan.inputs.execution_recipe_sha256
    if selected_context_sha256 is not None and selected_context_sha256 != expected:
        raise ValueError('EXECUTION_RECIPE_CHANGED_RESELECT_REQUIRED')
    actual = configuration_sha256(cfg, environments, target_snapshots=target_snapshots,
                                  normal_file_hashes=normal_file_hashes, plan=plan,
                                  selected_context_sha256=selected_context_sha256)
    if expected is not None:
        if expected == actual:
            return plan
        if selected_context_sha256 is None:
            raise ValueError('EXECUTION_RECIPE_CHANGED_RESELECT_REQUIRED')
    return plan.model_copy(update={'inputs': plan.inputs.model_copy(update={
        'execution_recipe_sha256': actual})}).seal()


@contextmanager
def execution_lock(root, plan_id):
    """OS-released lock; a crashed owner cannot leave a permanent sentinel."""
    path = Path(root) / 'locks' / (plan_id + '.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            stream.write(b'0'); stream.flush(); stream.seek(0)
            try: msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError: raise ValueError('PLAN_EXECUTION_ALREADY_RUNNING') from None
            try: yield
            finally:
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try: fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise ValueError('PLAN_EXECUTION_ALREADY_RUNNING') from None
            try: yield
            finally: fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
