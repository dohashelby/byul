"""Prepare scoped lifecycle assets and bind inputs; no engine or client creation."""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import re
import uuid
import os
import secrets
import stat

from guard.adapters import vllm_lifecycle_sensor as sensor
from guard.adapters import vllm_termination_sensor as termination_sensor
from guard.adapters import vllm_fault_sensor as fault_sensor,vllm_fault_control as fault_control,vllm_fault_runtime as fault_runtime
from guard.adapters import sglang_request_termination_sensor as sglang_sensor
from guard.adapters import sglang_termination_launcher as sglang_launcher
from guard.adapters import vllm_safe_open_mapping_sensor as mapping_sensor
from guard.adapters import vllm_ready_endpoint_sensor as endpoint_sensor,vllm_ready_endpoint_contract as endpoint_contract
from guard.adapters import vllm_worker_generation_sensor as generation_sensor,vllm_worker_generation_contract as generation_contract
from guard.adapters import vllm_worker_initialization_sensor as joint_sensor
from guard.adapters import vllm_worker_startup_sensor as startup_sensor,vllm_worker_startup_contract as startup_contract
from guard.adapters import vllm_lifecycle_profiles as profiles
from guard.adapters import vllm_online_sensor as online, vllm_online_input as online_input
from guard.adapters import vllm_attempt_sensor as attempt
from guard.adapters import vllm_lifecycle_release_sensor as release_sensor
from guard.adapters import vllm_lifecycle_catalog as configured
from guard.adapters import vllm_initialization_release_sensor as init_release
from guard.adapters import vllm_release_observation_sensor as release_observation
from guard.adapters import vllm_fresh_request_binding as fresh_binding
from guard.adapters import vllm_request_output_sensor as output_sensor
from guard.adapters import vllm_stream_buffer_sensor as stream_sensor
from guard.adapters import vllm_rank_failure_sensor as rank_sensor
from guard.adapters import vllm_initialization_sensor as init_sensor
from guard.adapters import vllm_profiler_sensor as profiler_sensor
from guard.adapters import vllm_file_read_sensor as read_sensor
from guard.adapters import vllm_weight_iteration_sensor as weight_sensor
from guard.collectors.file import load_environment
from guard.collectors.route_observations import validate_output
from guard.executors.owned_capture import strict_json, validate_owned_source, validate_engine_target, engine_identity
from guard.models import DataOrigin
from guard.update_validation import reserve_output


_STARTUP_PROFILES = (startup_sensor.PROFILE, joint_sensor.PROFILE, generation_sensor.PROFILE, endpoint_sensor.PROFILE)
_MAPPING_PROFILES = {mapping_sensor.PROFILE, *_STARTUP_PROFILES}
_MODEL_READ_PROFILES = {read_sensor.PROFILE, *_MAPPING_PROFILES}


_FIXED_BASE_PINS = {
    termination_sensor.PROFILE: (termination_sensor.BASE8_SHA256, 'LIFECYCLE_TERMINATION_DEPENDENCY_UNAVAILABLE'),
    attempt.PROFILE: (attempt.BASE_SHA256, 'LIFECYCLE_ATTEMPT_DEPENDENCY_UNAVAILABLE'),
    stream_sensor.PROFILE: (stream_sensor.BASE8_SHA256, 'LIFECYCLE_STREAM_DEPENDENCY_UNAVAILABLE'),
    rank_sensor.PROFILE: (rank_sensor.BASE8_SHA256, 'LIFECYCLE_RANK_DEPENDENCY_UNAVAILABLE'),
    init_sensor.PROFILE: (init_sensor.BASE8_SHA256, 'LIFECYCLE_INITIALIZATION_DEPENDENCY_UNAVAILABLE'),
    profiler_sensor.PROFILE: (profiler_sensor.BASE8_SHA256, 'LIFECYCLE_PROFILER_DEPENDENCY_UNAVAILABLE'),
    weight_sensor.PROFILE: (weight_sensor.BASE8_SHA256, 'LIFECYCLE_WEIGHT_ITERATION_DEPENDENCY_UNAVAILABLE'),
    release_sensor.PROFILE: (release_sensor.BASE_SHA256, 'LIFECYCLE_RELEASE_DEPENDENCY_UNAVAILABLE'),
    online.PROFILE: (online.BASE_SHA256, 'LIFECYCLE_ONLINE_DEPENDENCY_UNAVAILABLE'),
    output_sensor.PROFILE: (output_sensor.BASE8_SHA256, 'LIFECYCLE_OUTPUT_DEPENDENCY_UNAVAILABLE'),
}
_FIXED_EXTRA_ASSETS = {
    online.PROFILE: {'guard_online_input_callback.py': (online_input, online.CALLBACK_SHA256)},
    output_sensor.PROFILE: {'guard_attempt_invocation.py': (attempt, output_sensor.INVOCATION10_SHA256)},
}


def _fixed_dependencies(profile):
    if profile not in _FIXED_BASE_PINS:
        return {}
    digest, _ = _FIXED_BASE_PINS[profile]
    return {'guard_lifecycle_base.py': (sensor, digest), **_FIXED_EXTRA_ASSETS.get(profile, {})}


def _worker_dependencies(profile):
    if profile not in _STARTUP_PROFILES:
        return {}
    from guard.adapters import vllm_worker_readiness as startup_callback, vllm_slot_mask as startup_support
    layers = (
        ("WORKER_STARTUP_DEPENDENCY_UNAVAILABLE", (
            ("guard_safe_open_mapping.py", mapping_sensor, startup_sensor.MAPPING18_SHA256),
            ("guard_worker_startup_contract.py", startup_contract, startup_sensor.CONTRACT_SHA256),
            ("guard_worker_readiness_callback.py", startup_callback, startup_sensor.CALLBACK_SHA256),
            ("guard_worker_readiness_support.py", startup_support, startup_sensor.SUPPORT_SHA256),
        )),
        ("WORKER_INITIALIZATION_DEPENDENCY_UNAVAILABLE", (
            ("guard_worker_startup.py", startup_sensor, joint_sensor.STARTUP19_SHA256),
            ("guard_initialization.py", init_sensor, joint_sensor.INITIALIZATION14_SHA256),
        )),
        ("WORKER_GENERATION_DEPENDENCY_UNAVAILABLE", (
            ("guard_worker_initialization.py", joint_sensor, generation_sensor.JOINT20_SHA256),
            ("guard_worker_generation_contract.py", generation_contract, generation_sensor.CONTRACT_SHA256),
        )),
        ("READY_ENDPOINT_DEPENDENCY_UNAVAILABLE", (
            ("guard_worker_generation.py", generation_sensor, endpoint_sensor.GENERATION21_SHA256),
            ("guard_ready_endpoint_contract.py", endpoint_contract, endpoint_sensor.CONTRACT_SHA256),
        )),
    )
    active_layers = layers[:_STARTUP_PROFILES.index(profile) + 1]
    return {name: (module, digest, error) for error, entries in active_layers for name, module, digest in entries}


def _is_release_variant(profile):
    return profile in {v[0] for v in release_observation.PROFILES.values()}


def _configured_dependencies(profile):
    return configured.startup_assets(profile) | configured.stream_assets(profile)


def _release_variant_dependencies(profile):
    return {
        "guard_lifecycle_base.py": (sensor, release_observation.BASE_SHA256),
        "guard_lifecycle_release.py": (release_sensor, release_observation.RELEASE_SHA256),
        "guard_lifecycle_extension.py": (
            release_observation.extension(profile),
            release_observation.extension_sha256(profile),
        ),
        "guard_request_binding.py": (fresh_binding, release_observation.BINDING_SHA256),
    }


def prepare_assets(output, repository, *, profile=None, model_read_scope=None,fault_control_target_sha256=None):
    selected = release_observation if _is_release_variant(profile) else configured if configured.contains(profile) else fault_sensor if profile==fault_sensor.PROFILE else sglang_sensor if profile==sglang_sensor.PROFILE else termination_sensor if profile==termination_sensor.PROFILE else sensor if profile in (None, sensor.PROFILE) else online if profile == online.PROFILE else attempt if profile == attempt.PROFILE else output_sensor if profile == output_sensor.PROFILE else stream_sensor if profile == stream_sensor.PROFILE else rank_sensor if profile == rank_sensor.PROFILE else init_sensor if profile == init_sensor.PROFILE else profiler_sensor if profile == profiler_sensor.PROFILE else weight_sensor if profile == weight_sensor.PROFILE else read_sensor if profile == read_sensor.PROFILE else mapping_sensor if profile == mapping_sensor.PROFILE else startup_sensor if profile == startup_sensor.PROFILE else joint_sensor if profile == joint_sensor.PROFILE else generation_sensor if profile == generation_sensor.PROFILE else endpoint_sensor if profile == endpoint_sensor.PROFILE else release_sensor if profile == release_sensor.PROFILE else init_release if profile == init_release.PROFILE else None
    if selected is None:
        raise ValueError("LIFECYCLE_PROFILE_UNAVAILABLE")
    if selected is fault_sensor:
        if not fault_control.fingerprint(fault_control_target_sha256):raise ValueError('FAULT_CONTROL_TARGET_REQUIRED')
    elif fault_control_target_sha256 is not None:raise ValueError('FAULT_CONTROL_PROFILE_REQUIRED')
    selected_profile = profile if selected in (configured, release_observation) else selected.PROFILE
    from guard.collectors import model_read_scope as scope_contract
    if selected_profile in _MODEL_READ_PROFILES:
        if model_read_scope is None:raise ValueError('MODEL_READ_SCOPE_REQUIRED')
        scope,scope_raw=scope_contract.load_scope(model_read_scope)
    elif model_read_scope is not None:raise ValueError('MODEL_READ_SCOPE_PROFILE_MISMATCH')
    output = Path(output).absolute()
    if any(parent.is_symlink() or parent.is_junction() or (parent/".git").exists() for parent in output.parents):
        raise ValueError("LIFECYCLE_OUTPUT_IN_REPOSITORY")
    output = reserve_output(output, protected=[Path(repository)])
    session = uuid.uuid4().hex
    key_raw = None
    if configured.request_binding(selected_profile) or selected is release_observation:
        key_raw = json.dumps({'schema': 'guard-lifecycle-request-key/1', 'session': session,
            'key_hex': secrets.token_hex(32)}, sort_keys=True, separators=(',', ':')).encode()
    source = (configured.render_source(profile, **({'request_key_sha256': hashlib.sha256(key_raw).hexdigest()}
        if key_raw is not None else {})) if selected is configured else
        release_observation.render_source(release_observation.variant_for(profile),
            request_key_sha256=hashlib.sha256(key_raw).hexdigest())
        if selected is release_observation else Path(selected.__file__).read_bytes())
    digest = hashlib.sha256(source).hexdigest()
    config = {"schema":sensor.CONFIG_SCHEMA, "session":session,
        "output_dir":"/guard-lifecycle/data/"+session,
        "control_path":"/guard-lifecycle/data/"+session+"/SEALED"}
    bootstrap = (
        "import hashlib,pathlib,sys,types\n"
        "def _guard_lifecycle_start():\n"
        "    try:\n"
        "        path=pathlib.Path('/guard-lifecycle/guard_lifecycle_sensor.py')\n"
        "        raw=path.read_bytes()\n"
        f"        if hashlib.sha256(raw).hexdigest()!={digest!r}: return\n"
        "        module=types.ModuleType('guard_lifecycle_sensor')\n"
        "        module.__file__=str(path)\n"
        "        exec(compile(raw,str(path),'exec'),module.__dict__)\n"
        "        sys.modules[module.__name__]=module\n"
        "        module.install()\n"
        "    except Exception:\n"
        "        pass\n"
        "_guard_lifecycle_start()\n"
    ).encode()
    values = {"guard_lifecycle_sensor.py":source, "sitecustomize.py":bootstrap,
              "settings.json":(json.dumps(config,sort_keys=True,indent=2)+"\n").encode()}
    if key_raw is not None:
        values['request-binding-key.json'] = key_raw
    if selected_profile in _MODEL_READ_PROFILES:
        base_raw=Path(sensor.__file__).read_bytes();iteration_raw=Path(weight_sensor.__file__).read_bytes()
        if (hashlib.sha256(base_raw).hexdigest()!=read_sensor.BASE8_SHA256 or hashlib.sha256(iteration_raw).hexdigest()!=read_sensor.ITERATION16_SHA256):
            raise ValueError('LIFECYCLE_FILE_READ_DEPENDENCY_UNAVAILABLE')
        deployed=scope_contract.deploy_scope(scope,scope_raw,session)
        values.update({'guard_lifecycle_base.py':base_raw,'guard_weight_iteration.py':iteration_raw,'model-read-scope.json':deployed})
        if selected_profile in _MAPPING_PROFILES:
            scope_module=Path(read_sensor.__file__).read_bytes()
            if hashlib.sha256(scope_module).hexdigest()!=mapping_sensor.SCOPE17_SHA256:raise ValueError('SAFE_OPEN_MAPPING_SCOPE_DEPENDENCY_UNAVAILABLE')
            values['guard_file_read_scope.py']=scope_module
        for filename, (module, digest, error) in _worker_dependencies(selected_profile).items():
            raw = Path(module.__file__).read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError(error)
            values[filename] = raw
    elif selected_profile in _FIXED_BASE_PINS:
        dependencies = _fixed_dependencies(selected_profile)
        files = {name: Path(module.__file__).read_bytes() for name, (module, _) in dependencies.items()}
        if any(hashlib.sha256(files[name]).hexdigest() != digest for name, (_, digest) in dependencies.items()):
            error = (_FIXED_BASE_PINS[selected_profile][1] if selected_profile in _FIXED_EXTRA_ASSETS
                     else 'LIFECYCLE_ATTEMPT_DEPENDENCY_UNAVAILABLE')
            raise ValueError(error)
        values.update(files)
    elif selected is fault_sensor:
        base_raw=Path(sensor.__file__).read_bytes()
        if hashlib.sha256(base_raw).hexdigest()!=fault_sensor.BASE8_SHA256:raise ValueError('FAULT_BASE_DEPENDENCY_UNAVAILABLE')
        values['guard_lifecycle_base.py']=base_raw
        for filename,module in (('guard_termination.py',termination_sensor),('guard_rank_failure.py',rank_sensor),('guard_fault_control.py',fault_control),('guard_fault_runtime.py',fault_runtime)):
            raw=Path(module.__file__).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=fault_sensor.ASSETS[filename]:raise ValueError('FAULT_DEPENDENCY_UNAVAILABLE')
            values[filename]=raw
        control_scope={'schema':'byul-native-fault-settings/1','session':session,'target_sha256':fault_control_target_sha256,'directory':'/guard-lifecycle/fault-control'}
        values['fault-control-settings.json']=(json.dumps(control_scope,sort_keys=True,indent=2)+'\n').encode()
    elif selected is sglang_sensor:
        base_raw = Path(sensor.__file__).read_bytes()
        if hashlib.sha256(base_raw).hexdigest() != sglang_sensor.BASE8_SHA256:
            raise ValueError("LIFECYCLE_ATTEMPT_DEPENDENCY_UNAVAILABLE")
        values["guard_lifecycle_base.py"] = base_raw
    elif selected is configured:
        base_raw = Path(sensor.__file__).read_bytes()
        if hashlib.sha256(base_raw).hexdigest() != configured.runtime_spec(profile)['base_sha256']:
            raise ValueError('CONFIGURED_LIFECYCLE_BASE_UNAVAILABLE')
        values['guard_lifecycle_base.py'] = base_raw
        for filename, (logical, digest) in _configured_dependencies(profile).items():
            raw = Path(sensor.__file__).with_name(logical + '.py').read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError('BASELINE_STARTUP_DEPENDENCY_UNAVAILABLE')
            values[filename] = raw
    elif selected is init_release:
        for filename,module in (('guard_lifecycle_base.py',sensor),('guard_lifecycle_release.py',release_sensor),('guard_initialization.py',init_sensor)):
            raw=Path(module.__file__).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=init_release.ASSETS[filename]:raise ValueError('INITIALIZATION_RELEASE_DEPENDENCY_UNAVAILABLE')
            values[filename]=raw
    elif selected is release_observation:
        for filename, (module, digest) in _release_variant_dependencies(profile).items():
            raw = Path(module.__file__).read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                if module is fresh_binding:
                    raise ValueError('LIFECYCLE_OBSERVATION_KEY_READER_UNAVAILABLE')
                raise ValueError('LIFECYCLE_OBSERVATION_DEPENDENCY_UNAVAILABLE')
            values[filename] = raw
    if selected is sglang_sensor:
        launcher_raw=Path(sglang_launcher.__file__).read_bytes()
        if hashlib.sha256(launcher_raw).hexdigest()!='99856447cc643b4597851b6e330d6744e6ca11d3ff0c94493b6df07a8a5b7e4a'or hashlib.sha256(source).hexdigest()!=sglang_launcher.SENSOR_SHA256:
            raise ValueError('SGLANG_LAUNCH_DEPENDENCY_UNAVAILABLE')
        values['guard_sglang_launcher.py']=launcher_raw
        # SG-only: a fresh spawned interpreter must guard repr before imports.
        # sitecustomize exceptions are normally swallowed by Python, so refuse
        # startup explicitly if the pinned privacy guard cannot be installed.
        privacy = (
            "import hashlib,pathlib,sys,types,os\n"
            "try:\n"
            "    p=pathlib.Path('/guard-lifecycle/guard_sglang_launcher.py')\n"
            "    raw=p.read_bytes()\n"
            f"    if hashlib.sha256(raw).hexdigest()!={hashlib.sha256(launcher_raw).hexdigest()!r}: raise RuntimeError('SGLANG_PRIVACY_SOURCE')\n"
            "    m=types.ModuleType('guard_sglang_launcher');m.__file__=str(p)\n"
            "    exec(compile(raw,str(p),'exec'),m.__dict__);sys.modules[m.__name__]=m\n"
            "    m.install_private_args()\n"
            "except BaseException:\n"
            "    os._exit(78)\n"
        ).encode()
        values['sitecustomize.py']=privacy+values['sitecustomize.py']
    assets = {}
    for name, data in values.items():
        if name == 'request-binding-key.json':
            fd = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
        else:
            with (output/name).open("xb") as stream:
                stream.write(data)
        assets[name] = {"sha256":hashlib.sha256(data).hexdigest(),"bytes":len(data)}
    manifest = {"schema":"guard-lifecycle-assets/1", "profile":selected_profile, "session":session,
        "public_commit":profiles.public_commit(selected_profile), "assets":assets,
        "source_sha256":profiles.source_hashes(selected_profile), "engine_started":False,
        "image_contents_verified":False, "product_connected":False}
    if selected is fault_sensor:manifest['fault_control_target_sha256']=fault_control_target_sha256
    if selected_profile in _MODEL_READ_PROFILES:manifest['model_read_scope']=scope_contract.scope_summary(scope)
    with (output/"manifest.json").open("x",encoding="utf-8",newline="\n") as stream:
        json.dump(manifest,stream,sort_keys=True,indent=2)
        stream.write("\n")
    return manifest


def _prepared_asset_names(profile, *, fixed_dependencies, worker_dependencies, variant_dependencies):
    common = {'guard_lifecycle_sensor.py', 'sitecustomize.py', 'settings.json'}
    if variant_dependencies:
        return common | {'request-binding-key.json', *variant_dependencies}
    if configured.contains(profile):
        return configured.asset_names(profile)
    if profile == init_release.PROFILE:
        return common | {*init_release.ASSETS}
    with_base = common | {'guard_lifecycle_base.py'}
    if profile == fault_sensor.PROFILE:
        return with_base | {'fault-control-settings.json', *fault_sensor.ASSETS}
    if profile == sglang_sensor.PROFILE:
        return with_base | {'guard_sglang_launcher.py'}
    if profile in _MODEL_READ_PROFILES:
        names = with_base | {'guard_weight_iteration.py', 'model-read-scope.json', *worker_dependencies}
        return names | {'guard_file_read_scope.py'} if profile in _MAPPING_PROFILES else names
    return common | set(fixed_dependencies)


def _read_request_binding_key(path):
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or not hasattr(os, 'geteuid') or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600 or not 0 < before.st_size <= 256):
        raise ValueError('REQUEST_BINDING_KEY_INVALID')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        raw = os.read(fd, 257)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    def identity(status):
        return (status.st_dev, status.st_ino, status.st_mode, status.st_size, status.st_mtime_ns, status.st_ctime_ns)

    if len(raw) != before.st_size or len({identity(s) for s in (before, opened, after, path.lstat())}) != 1:
        raise ValueError('REQUEST_BINDING_KEY_INVALID')
    return raw


def _request_binding_key_hex(raw, session, *, status=None):
    key = strict_json(raw, limit=256)
    if ((status is not None and (not stat.S_ISREG(status.st_mode) or status.st_uid != os.geteuid()
            or stat.S_IMODE(status.st_mode) != 0o600))
            or type(key) is not dict or set(key) != {'schema', 'session', 'key_hex'}
            or key['schema'] != 'guard-lifecycle-request-key/1' or key['session'] != session
            or type(key['key_hex']) is not str or re.fullmatch('[0-9a-f]{64}', key['key_hex']) is None):
        raise ValueError('REQUEST_BINDING_KEY_INVALID')
    return key['key_hex']


def load_prepared(path):
    path = Path(path)
    with path.open("rb") as stream:
        raw_manifest = stream.read(262145)
        manifest = strict_json(raw_manifest, limit=262144)
    profile = manifest.get('profile') if type(manifest) is dict else None
    if type(profile) is not str:
        raise ValueError("LIFECYCLE_ASSETS_INVALID")
    expected_fields = {"schema", "profile", "session", "public_commit", "assets", "source_sha256",
                       "engine_started", "image_contents_verified", "product_connected"}
    if profile == fault_sensor.PROFILE:
        expected_fields.add('fault_control_target_sha256')
    if profile in _MODEL_READ_PROFILES:
        expected_fields.add('model_read_scope')
    if (set(manifest) != expected_fields
            or (profile not in _MODEL_READ_PROFILES and len(raw_manifest) > 16384)
            or manifest["schema"] != "guard-lifecycle-assets/1"
            or profile not in profiles.SCHEMAS
            or manifest["public_commit"] != profiles.public_commit(profile)
            or manifest["source_sha256"] != profiles.source_hashes(profile)
            or type(manifest["session"]) is not str or re.fullmatch(r"[0-9a-f]{32}", manifest["session"]) is None
            or manifest["engine_started"] is not False or manifest["image_contents_verified"] is not False
            or manifest["product_connected"] is not False
            or type(manifest["assets"]) is not dict):
        raise ValueError("LIFECYCLE_ASSETS_INVALID")
    release_variant = _is_release_variant(profile)
    variant_dependencies = _release_variant_dependencies(profile) if release_variant else {}
    fixed_dependencies = _fixed_dependencies(profile)
    worker_dependencies = _worker_dependencies(profile)
    if set(manifest["assets"]) != _prepared_asset_names(
        profile, fixed_dependencies=fixed_dependencies,
        worker_dependencies=worker_dependencies, variant_dependencies=variant_dependencies,
    ):
        raise ValueError("LIFECYCLE_ASSETS_INVALID")
    if profile == fault_sensor.PROFILE and not fault_control.fingerprint(manifest['fault_control_target_sha256']):
        raise ValueError('FAULT_CONTROL_TARGET_INVALID')
    configured_dependencies = _configured_dependencies(profile)
    requires_key = configured.request_binding(profile) or release_variant
    source_supported = False
    for name, expected in manifest["assets"].items():
        if (type(expected) is not dict or set(expected) != {"sha256","bytes"}
                or type(expected["bytes"]) is not int or not 0 < expected["bytes"] <= 262144
                or type(expected["sha256"]) is not str or re.fullmatch(r"[0-9a-f]{64}",expected["sha256"]) is None):
            raise ValueError("LIFECYCLE_ASSET_METADATA_INVALID")
        private_key = requires_key and name == 'request-binding-key.json'
        if private_key:
            key_path = path.parent / name
            raw = _read_request_binding_key(key_path)
        elif name in configured_dependencies:
            try:
                raw = configured.startup._startup_read(name,
                    configured_dependencies[name][1], directory_path=str(path.parent))
            except OSError:
                raise ValueError('BASELINE_STARTUP_DEPENDENCY_UNAVAILABLE') from None
        else:
            with (path.parent/name).open("rb") as stream:
                raw = stream.read(262145)
        actual_sha256 = hashlib.sha256(raw).hexdigest()
        if len(raw) != expected["bytes"] or actual_sha256 != expected["sha256"]:
            raise ValueError("LIFECYCLE_ASSET_BYTES_INVALID")
        if private_key:
            _request_binding_key_hex(raw, manifest['session'], status=key_path.lstat())
        if manifest['profile']==fault_sensor.PROFILE:
            if name in fault_sensor.ASSETS and actual_sha256!=fault_sensor.ASSETS[name]:raise ValueError('FAULT_DEPENDENCY_UNAVAILABLE')
            if name=='guard_lifecycle_base.py'and actual_sha256!=fault_sensor.BASE8_SHA256:raise ValueError('FAULT_BASE_DEPENDENCY_UNAVAILABLE')
            if name=='fault-control-settings.json':
                expected_scope={'schema':'byul-native-fault-settings/1','session':manifest['session'],'target_sha256':manifest['fault_control_target_sha256'],'directory':'/guard-lifecycle/fault-control'}
                if strict_json(raw,limit=16384)!=expected_scope:raise ValueError('FAULT_CONTROL_SCOPE_INVALID')
        if name in fixed_dependencies and actual_sha256 != fixed_dependencies[name][1]:
            raise ValueError(_FIXED_BASE_PINS[profile][1])
        if manifest['profile']==sglang_sensor.PROFILE and name=='guard_sglang_launcher.py'and (actual_sha256!='99856447cc643b4597851b6e330d6744e6ca11d3ff0c94493b6df07a8a5b7e4a'or raw!=Path(sglang_launcher.__file__).read_bytes()):
            raise ValueError('SGLANG_LAUNCH_DEPENDENCY_UNAVAILABLE')
        if profile == sglang_sensor.PROFILE and name == "guard_lifecycle_base.py" and actual_sha256 != termination_sensor.BASE8_SHA256:
            raise ValueError("LIFECYCLE_TERMINATION_DEPENDENCY_UNAVAILABLE")
        if configured.contains(manifest['profile']) and name == 'guard_lifecycle_base.py':
            if actual_sha256 != configured.runtime_spec(manifest['profile'])['base_sha256']:
                raise ValueError('CONFIGURED_LIFECYCLE_BASE_UNAVAILABLE')
        if name in configured_dependencies:
            logical, digest = configured_dependencies[name]
            if (actual_sha256 != digest
                    or raw != Path(sensor.__file__).with_name(logical + '.py').read_bytes()):
                raise ValueError('BASELINE_STARTUP_DEPENDENCY_UNAVAILABLE')
        if name in variant_dependencies and expected['sha256'] != variant_dependencies[name][1]:
            raise ValueError('LIFECYCLE_OBSERVATION_DEPENDENCY_UNAVAILABLE')
        if manifest["profile"]==init_release.PROFILE and name in init_release.ASSETS and actual_sha256!=init_release.ASSETS[name]:
            raise ValueError("INITIALIZATION_RELEASE_DEPENDENCY_UNAVAILABLE")
        if profile in _MODEL_READ_PROFILES:
            required={'guard_lifecycle_base.py':read_sensor.BASE8_SHA256,'guard_weight_iteration.py':read_sensor.ITERATION16_SHA256,**({'guard_file_read_scope.py':mapping_sensor.SCOPE17_SHA256} if profile in _MAPPING_PROFILES else {})}.get(name)
            if required is not None and actual_sha256!=required:raise ValueError('LIFECYCLE_FILE_READ_DEPENDENCY_UNAVAILABLE')
            if name=='model-read-scope.json':
                from guard.collectors.model_read_scope import scope_summary
                value=read_sensor.validate_scope(strict_json(raw,limit=262144),session=manifest['session'])
                if scope_summary(value)!=manifest['model_read_scope']:raise ValueError('MODEL_READ_SCOPE_MANIFEST_MISMATCH')
        if profile in _STARTUP_PROFILES:
            startup_pin = {
                'guard_lifecycle_base.py': startup_sensor.BASE8_SHA256,
                'guard_weight_iteration.py': startup_sensor.ITERATION16_SHA256,
                'guard_file_read_scope.py': startup_sensor.SCOPE17_SHA256,
            }.get(name)
            if startup_pin is not None and actual_sha256 != startup_pin:
                raise ValueError('WORKER_STARTUP_DEPENDENCY_UNAVAILABLE')
        if name in worker_dependencies:
            _, digest, error = worker_dependencies[name]
            if actual_sha256 != digest:
                raise ValueError(error)
        if name == "guard_lifecycle_sensor.py":
            source_supported = profiles.asset_supported(manifest["profile"], raw,
                **({'request_key_sha256': manifest['assets']['request-binding-key.json']['sha256']}
                   if requires_key else {}))
    if not source_supported:
        raise ValueError("LIFECYCLE_ASSET_VERSION_UNAVAILABLE")
    return manifest


def request_header_fingerprint(manifest_path, prepared, header):
    """Derive a new-profile ASGI header HMAC from the validated private key.

    This reopens and verifies the exact key after `load_prepared`; no key bytes
    or raw header leave the caller. A changed or unreadable key is a gap.
    """
    if (not (configured.request_binding(prepared.get('profile')) or _is_release_variant(prepared.get('profile'))) or type(header) is not str
            or not 0 < len(header.encode('utf-8', 'surrogatepass')) <= 512):
        raise ValueError('REQUEST_BINDING_HEADER_UNAVAILABLE')
    path = Path(manifest_path).parent / 'request-binding-key.json'
    try:
        raw = _read_request_binding_key(path)
        expected = prepared['assets']['request-binding-key.json']
        if len(raw) != expected['bytes'] or hashlib.sha256(raw).hexdigest() != expected['sha256']:
            raise ValueError
        key = _request_binding_key_hex(raw, prepared['session'])
        return hmac.new(bytes.fromhex(key),
            b'guard-native-request-binding/1\0asgi-header\0' + header.encode('utf-8', 'surrogatepass'),
            hashlib.sha256).hexdigest()
    except (OSError, KeyError, TypeError, ValueError):
        raise ValueError('REQUEST_BINDING_KEY_UNAVAILABLE') from None


def load_inputs(env_path, target_path, source, manifest_path):
    env = load_environment(Path(env_path))
    with Path(target_path).open("rb") as stream:
        target = strict_json(stream.read(65537))
    prepared = load_prepared(manifest_path)
    validate_binding(env,target,source,prepared)
    return env, target, prepared


def validate_binding(env,target,source,prepared):
    validate_owned_source(**source)
    prefix = "/guard-lifecycle/data/" + prepared["session"] + "/"
    if re.fullmatch(re.escape(prefix)+r"pid-[1-9][0-9]{0,9}-[1-9][0-9]{0,19}\.jsonl",source["path"]) is None:
        raise ValueError("LIFECYCLE_SOURCE_SESSION_INVALID")
    validate_session_binding(env,target,source,prepared)


def validate_session_binding(env,target,source,prepared):
    from guard.adapters.engine_profiles import require
    if prepared['profile']==release_observation.RANK_PROFILE:
        require(env.engine.name,env.engine.version,'rank_release_rows')
    if prepared['profile'] in {fault_sensor.PROFILE, rank_sensor.PROFILE, attempt.PROFILE, output_sensor.PROFILE, stream_sensor.PROFILE, online.PROFILE}:
        try:
            require(env.engine.name, env.engine.version, 'native_lifecycle')
        except ValueError:
            code = ('RANK' if prepared['profile'] in {fault_sensor.PROFILE, rank_sensor.PROFILE} else 'ONLINE' if prepared['profile'] == online.PROFILE else 'ATTEMPT')
            raise ValueError('LIFECYCLE_' + code + '_ENGINE_UNSUPPORTED') from None
    if prepared['profile'] in {release_sensor.PROFILE, init_release.PROFILE}:
        # Release rows are admitted per engine release by the profile catalog only.
        try:
            require(env.engine.name, env.engine.version, 'lifecycle_release_rows')
        except ValueError:
            raise ValueError('LIFECYCLE_RELEASE_ENGINE_UNSUPPORTED') from None
    if configured.contains(prepared['profile']):
        try:
            require(env.engine.name, env.engine.version, 'configured_lifecycle_rows')
            if not configured.matches(prepared['profile'], env.engine.name, env.engine.version,
                                      env.engine.image_digest, env.runtime.platform):
                raise ValueError('PROFILE_UNREVIEWED')
        except ValueError:
            raise ValueError('LIFECYCLE_CONFIGURED_ENGINE_UNSUPPORTED') from None
    validate_owned_source(**source)
    try:
        validate_engine_target(env,target,source["container"],source["image"])
    except ValueError:
        raise ValueError("LIFECYCLE_CAPTURE_INPUT_INVALID") from None


def collect(docker, env, target, source, prepared):
    validate_binding(env,target,source,prepared)
    from guard.executors.lifecycle_capture import capture
    value = capture(docker,target,source=source,prepared=prepared)
    origin = DataOrigin.EXAMPLE if env.data_origin == DataOrigin.EXAMPLE or target.get("lab") is True else DataOrigin.DECLARED
    return {**value, "data_origin":origin.value, "environment_ref":env.ref().model_dump(mode="json"),
        "origin_detail":"owned file and process context read; native producer assertions remain unauthenticated"}


def load_session_inputs(env_path,target_path,container,image,manifest_path):
    env=load_environment(Path(env_path))
    with Path(target_path).open("rb") as stream:
        target=strict_json(stream.read(65537))
    prepared=load_prepared(manifest_path)
    source={"container":container,"image":image,"path":"/guard-lifecycle/data/"+prepared["session"]}
    validate_session_binding(env,target,source,prepared)
    return env,target,prepared


def collect_session(docker,env,target,container,image,prepared):
    source={"container":container,"image":image,"path":"/guard-lifecycle/data/"+prepared["session"]}
    validate_session_binding(env,target,source,prepared)
    from guard.executors.lifecycle_session import capture_session
    def owner(client,topology,name,pin,timeout):
        return engine_identity(client,topology,env,name,pin,timeout)
    value=capture_session(docker,target,container=container,image=image,prepared=prepared,owner_identity=owner)
    origin=DataOrigin.EXAMPLE if env.data_origin==DataOrigin.EXAMPLE or target.get("lab") is True else DataOrigin.DECLARED
    return {**value,"data_origin":origin.value,"environment_ref":env.ref().model_dump(mode="json"),
        "origin_detail":"owned container census and recording bytes; producer assertions and fingerprint links remain unauthenticated"}
