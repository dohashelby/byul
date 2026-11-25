"""Requested configuration commitment and per-attempt exclusion, engine neutral."""
from contextlib import contextmanager
from pathlib import Path
import os
import inspect
from guard.collectors.check_observations import MAX_RECORD_BYTES, VerifiedRecord, path_identity
from guard.configuration import for_side, requested_input_paths
from guard.models import canonical_json, sha256_hex, utcnow


def configuration_sha256(
    cfg, environments=(), *, software_digests: dict[Path, tuple[bytes, str]] | None = None,
    record_reads: dict[Path, VerifiedRecord] | None = None,
):
    """Commit values and explicitly submitted input bytes, without saving secrets.

    Runtime injected fields are never requested configuration. Managed target
    paths added after selection are absent from the original requested config.
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
    for location, path, deferred in requested_input_paths(cfg):
        if not deferred:
            common_record = (location[-2:-1] == ("records",) and "common_inputs" in location
                             or location[-2:] in {("runner_input", "path"), ("supply_reads_input", "path")})
            if path.is_file():
                inputs[str(path)] = digest_file(path, common_record=common_record)
            else:
                inputs[str(path)] = None
                if record_reads is not None and common_record:
                    record_reads[path] = VerifiedRecord(None)
        elif str(path) not in inputs:
            # Its derivation and ENV are committed now; the owned Pod
            # generation is observed/sealed by the transition at run time.
            inputs[str(path)] = {'binding': 'owned-candidate-after-baseline/1'}
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
    sides = {s: None for s in cfg.get('sides', {})}
    if len(environments) == 2: sides.update(zip(('baseline', 'candidate'), environments))
    for side, env in sides.items():
        factories(for_side(cfg, side), side, env)
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
    return sha256_hex(canonical_json({'schema': 'guard-execution-recipe/1', 'requested': normalize(cfg),
        'inputs': inputs, 'implementations': implementations, 'software_sha256': sha256_hex(canonical_json(software))}))


def validate_policy_time(policy, *, as_of=None):
    now = utcnow() if as_of is None else as_of
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('POLICY_VALIDITY_REQUIRES_TIMEZONE')
    if policy.valid_from is not None and now < policy.valid_from:
        raise ValueError('POLICY_NOT_YET_VALID')
    if policy.valid_until is not None and now >= policy.valid_until:
        raise ValueError('POLICY_EXPIRED')


def bind_recipe(plan, cfg, environments=()):
    actual = configuration_sha256(cfg, environments)
    expected = plan.inputs.execution_recipe_sha256
    if expected is not None:
        if expected != actual:
            raise ValueError('EXECUTION_RECIPE_CHANGED_RESELECT_REQUIRED')
        return plan
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
