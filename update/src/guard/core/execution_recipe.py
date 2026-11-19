"""Requested configuration commitment and per-attempt exclusion, engine neutral."""
from contextlib import contextmanager
from pathlib import Path
import os
import inspect
from guard.models import Mode, canonical_json, sha256_hex, utcnow


def configuration_sha256(cfg, environments=(), *, target_snapshots=None, normal_file_hashes=None,
                         transition_manifests=None, plan=None, initialization_digests=None):
    """Commit values and explicitly submitted input bytes, without saving secrets.

    Runtime injected fields are never requested configuration. Managed target
    paths added after selection are absent from the original requested config.
    Requested snapshot paths receive the same private bytes used for their hash.
    """
    def normalize(value):
        if isinstance(value, dict):
            return {k: normalize(v) for k, v in value.items() if not k.startswith('_')}
        if isinstance(value, list): return [normalize(v) for v in value]
        if isinstance(value, Path): return str(value)
        return value
    inputs = {}
    captured_digests = {}
    from guard.configuration import normal_file_reads_requested
    normal_file_reads = normal_file_reads_requested(cfg)
    late_candidate = cfg.get('update', {}).get('kubernetes_candidate_binding') == 'after_baseline'
    def files(value, location=()):
        if not isinstance(value, dict): return
        for key, item in value.items():
            if key.startswith('_'): continue
            record_source = key == 'record_producer' and location[-2:] in {
                ('run_binding', 'OBS-01'), ('run_binding', 'PLT-12')}
            copy_inventory = key == 'copy_inventory' and (
                location[-1:] == ('required_event_record',) or location[-2:] == ('check_config', 'PLT-12'))
            normal_file_input = normal_file_reads and key in {'prepared', 'reference_manifest'} and location[-2:] in {
                ('supply_boot', 'normal'), ('SUP-05', 'normal')}
            if (record_source or copy_inventory or normal_file_input) and isinstance(item, (str, Path)):
                # The audit record names the source bytes, so commit them with
                # the exact side's requested record session and inventory.
                from guard.executors.run_binding import producer_sha256
                path = str(item)
                if normal_file_input and normal_file_hashes is not None and path in normal_file_hashes:
                    inputs[path] = normal_file_hashes[path]
                else:
                    try: inputs[path] = producer_sha256(path)
                    except (OSError, ValueError): inputs[path] = None
                    if normal_file_input and normal_file_hashes is not None:
                        normal_file_hashes[path] = inputs[path]
                continue
            if key in {'target', 'manifest', 'gateway_target'} and isinstance(item, (str, Path)):
                path = Path(item)
                if key == 'target' and late_candidate and location[:2] == ('sides', 'candidate'):
                    # Its derivation and ENV are committed now; the owned Pod
                    # generation is observed/sealed by the transition at run time.
                    inputs[str(path)] = {'binding': 'owned-candidate-after-baseline/1'}
                elif target_snapshots is not None and path in target_snapshots:
                    if path not in captured_digests:
                        raw = path.read_bytes() if path.is_file() else None
                        target_snapshots[path] = raw
                        captured_digests[path] = sha256_hex(raw) if raw is not None else None
                    inputs[str(path)] = captured_digests[path]
                else: inputs[str(path)] = sha256_hex(path.read_bytes()) if path.is_file() else None
            elif isinstance(item, dict): files(item, (*location, key))
    files(cfg)
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
                'source_sha256': sha256_hex(source.read_bytes()) if source is not None and source.is_file() else None}
            if engine is not None:
                from guard.adapters.engine_provider import PROVIDERS, get_provider, load_installed_providers
                load_installed_providers()
                if engine in PROVIDERS:
                    closure = get_provider(engine).execution_recipe_files(factory)
                    implementations[side + ':' + name]['provider_sources'] = sorted(
                        ({'name': Path(path).name, 'sha256': sha256_hex(Path(path).read_bytes())} for path in closure),
                        key=lambda item: (item['name'], item['sha256']))
    factories(cfg, 'root', environments[0] if len(environments) == 1 else None)
    from guard.configuration import for_check, merge_config
    from guard.executors.lifecycle_transition import REDEPLOY_CHECKS, REDEPLOY_SIDE_REUSE, inputs_manifest, redeployment_side_reuse
    from guard.executors.run_binding import INITIALIZATION_CHECKS, initialization_recipe_digests
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
    for side, env in sides.items():
        effective = merge_config(cfg, cfg.get('sides', {}).get(side, {}))
        factories(effective, side, env)
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
    if transition_manifests is not None:
        # Commit even stale declarations. Only a selected execution consumes
        # this diagnostic, so unused bindings cannot block another check.
        transition_manifests.update({
            key: REDEPLOY_SIDE_REUSE if key.split(':')[1] in redeploy_conflicts else digest
            for key, digest in transition_inputs.items()})
    # Commit the actual installed common implementation, not a manifest's
    # assertion about its own bytes. This is outside the measured trial.
    package = Path(__file__).parents[1]
    software = {p.relative_to(package).as_posix(): sha256_hex(p.read_bytes())
        for p in sorted(package.rglob('*')) if p.is_file() and p.suffix in {'.py', '.json', '.yaml', '.txt'}}
    recipe = {'schema': 'guard-execution-recipe/1', 'requested': normalize(cfg),
              'inputs': inputs, 'implementations': implementations, 'software_sha256': sha256_hex(canonical_json(software))}
    if transition_inputs:
        recipe['transition_manifest_sha256_by_side_check'] = transition_inputs
    if init_inputs:
        recipe['initialization_file_sha256_by_side_check'] = init_inputs
    if initialization_digests is not None:
        initialization_digests.update(init_inputs)
    return sha256_hex(canonical_json(recipe))


def validate_policy_time(policy):
    now = utcnow()
    if policy.valid_from is not None and now < policy.valid_from:
        raise ValueError('POLICY_NOT_YET_VALID')
    if policy.valid_until is not None and now >= policy.valid_until:
        raise ValueError('POLICY_EXPIRED')


def bind_recipe(plan, cfg, environments=(), *, target_snapshots=None, normal_file_hashes=None):
    return plan.model_copy(update={'inputs': plan.inputs.model_copy(update={
        'execution_recipe_sha256': configuration_sha256(cfg, environments, target_snapshots=target_snapshots,
                                                       normal_file_hashes=normal_file_hashes, plan=plan)})}).seal()


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
