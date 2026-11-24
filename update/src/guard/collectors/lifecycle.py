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
from guard.adapters import vllm_online_sensor as online
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
    selected = release_observation if profile in {v[0] for v in release_observation.PROFILES.values()} else configured if configured.contains(profile) else fault_sensor if profile==fault_sensor.PROFILE else sglang_sensor if profile==sglang_sensor.PROFILE else termination_sensor if profile==termination_sensor.PROFILE else sensor if profile in (None, sensor.PROFILE) else online if profile == online.PROFILE else attempt if profile == attempt.PROFILE else output_sensor if profile == output_sensor.PROFILE else stream_sensor if profile == stream_sensor.PROFILE else rank_sensor if profile == rank_sensor.PROFILE else init_sensor if profile == init_sensor.PROFILE else profiler_sensor if profile == profiler_sensor.PROFILE else weight_sensor if profile == weight_sensor.PROFILE else read_sensor if profile == read_sensor.PROFILE else mapping_sensor if profile == mapping_sensor.PROFILE else startup_sensor if profile == startup_sensor.PROFILE else joint_sensor if profile == joint_sensor.PROFILE else generation_sensor if profile == generation_sensor.PROFILE else endpoint_sensor if profile == endpoint_sensor.PROFILE else release_sensor if profile == release_sensor.PROFILE else init_release if profile == init_release.PROFILE else None
    if selected is None:
        raise ValueError("LIFECYCLE_PROFILE_UNAVAILABLE")
    if selected is fault_sensor:
        if not fault_control.fingerprint(fault_control_target_sha256):raise ValueError('FAULT_CONTROL_TARGET_REQUIRED')
    elif fault_control_target_sha256 is not None:raise ValueError('FAULT_CONTROL_PROFILE_REQUIRED')
    from guard.collectors import model_read_scope as scope_contract
    approved_file_read = configured.approved_file_read(profile)
    model_read = approved_file_read or selected in (read_sensor,mapping_sensor,startup_sensor,joint_sensor,generation_sensor,endpoint_sensor)
    if model_read:
        if model_read_scope is None:raise ValueError('MODEL_READ_SCOPE_REQUIRED')
        scope,scope_raw=scope_contract.load_scope(model_read_scope)
        if approved_file_read and not scope['hash_model_bytes']:
            raise ValueError('APPROVED_FILE_READ_HASH_REQUIRED')
    elif model_read_scope is not None:raise ValueError('MODEL_READ_SCOPE_PROFILE_MISMATCH')
    output = Path(output).absolute()
    if any(parent.is_symlink() or parent.is_junction() or (parent/".git").exists() for parent in output.parents):
        raise ValueError("LIFECYCLE_OUTPUT_IN_REPOSITORY")
    output = reserve_output(output, protected=[Path(repository)])
    selected_profile = profile if selected in (configured,release_observation) else selected.PROFILE
    session = uuid.uuid4().hex
    deployed_scope = scope_contract.deploy_scope(scope, scope_raw, session) if model_read else None
    key_raw = None
    if configured.request_binding(selected_profile) or selected is release_observation:
        key_raw = json.dumps({'schema': 'guard-lifecycle-request-key/1', 'session': session,
            'key_hex': secrets.token_hex(32)}, sort_keys=True, separators=(',', ':')).encode()
    source = (configured.render_source(profile,
        request_key_sha256=hashlib.sha256(key_raw).hexdigest() if key_raw is not None else None,
        model_read_scope_sha256=hashlib.sha256(deployed_scope).hexdigest() if approved_file_read else None)
        if selected is configured else
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
    if selected in (read_sensor,mapping_sensor,startup_sensor,joint_sensor,generation_sensor,endpoint_sensor):
        base_raw=Path(sensor.__file__).read_bytes();iteration_raw=Path(weight_sensor.__file__).read_bytes()
        if (hashlib.sha256(base_raw).hexdigest()!=read_sensor.BASE8_SHA256 or hashlib.sha256(iteration_raw).hexdigest()!=read_sensor.ITERATION16_SHA256):
            raise ValueError('LIFECYCLE_FILE_READ_DEPENDENCY_UNAVAILABLE')
        values.update({'guard_lifecycle_base.py':base_raw,'guard_weight_iteration.py':iteration_raw,'model-read-scope.json':deployed_scope})
        if selected in (mapping_sensor,startup_sensor,joint_sensor,generation_sensor,endpoint_sensor):
            scope_module=Path(read_sensor.__file__).read_bytes()
            if hashlib.sha256(scope_module).hexdigest()!=mapping_sensor.SCOPE17_SHA256:raise ValueError('SAFE_OPEN_MAPPING_SCOPE_DEPENDENCY_UNAVAILABLE')
            values['guard_file_read_scope.py']=scope_module
        if selected in (startup_sensor,joint_sensor,generation_sensor,endpoint_sensor):
            from guard.adapters import vllm_worker_readiness as startup_callback,vllm_slot_mask as startup_support
            for filename,module,digest in (
                ('guard_safe_open_mapping.py',mapping_sensor,startup_sensor.MAPPING18_SHA256),
                ('guard_worker_startup_contract.py',startup_contract,startup_sensor.CONTRACT_SHA256),
                ('guard_worker_readiness_callback.py',startup_callback,startup_sensor.CALLBACK_SHA256),
                ('guard_worker_readiness_support.py',startup_support,startup_sensor.SUPPORT_SHA256)):
                raw=Path(module.__file__).read_bytes()
                if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('WORKER_STARTUP_DEPENDENCY_UNAVAILABLE')
                values[filename]=raw
        if selected in (joint_sensor,generation_sensor,endpoint_sensor):
            for filename,module,digest in (('guard_worker_startup.py',startup_sensor,joint_sensor.STARTUP19_SHA256),('guard_initialization.py',init_sensor,joint_sensor.INITIALIZATION14_SHA256)):
                raw=Path(module.__file__).read_bytes()
                if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('WORKER_INITIALIZATION_DEPENDENCY_UNAVAILABLE')
                values[filename]=raw
        if selected in (generation_sensor,endpoint_sensor):
            for filename,module,digest in (('guard_worker_initialization.py',joint_sensor,generation_sensor.JOINT20_SHA256),('guard_worker_generation_contract.py',generation_contract,generation_sensor.CONTRACT_SHA256)):
                raw=Path(module.__file__).read_bytes()
                if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('WORKER_GENERATION_DEPENDENCY_UNAVAILABLE')
                values[filename]=raw
        if selected is endpoint_sensor:
            for filename,module,digest in (('guard_worker_generation.py',generation_sensor,endpoint_sensor.GENERATION21_SHA256),('guard_ready_endpoint_contract.py',endpoint_contract,endpoint_sensor.CONTRACT_SHA256)):
                raw=Path(module.__file__).read_bytes()
                if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('READY_ENDPOINT_DEPENDENCY_UNAVAILABLE')
                values[filename]=raw
    elif selected is online:
        from guard.adapters import vllm_online_input as callback
        base_raw, callback_raw = Path(sensor.__file__).read_bytes(), Path(callback.__file__).read_bytes()
        if (hashlib.sha256(base_raw).hexdigest() != online.BASE_SHA256
                or hashlib.sha256(callback_raw).hexdigest() != online.CALLBACK_SHA256):
            raise ValueError("LIFECYCLE_ONLINE_DEPENDENCY_UNAVAILABLE")
        values.update({"guard_lifecycle_base.py":base_raw, "guard_online_input_callback.py":callback_raw})
    elif selected is output_sensor:
        base_raw=Path(sensor.__file__).read_bytes();invocation_raw=Path(attempt.__file__).read_bytes()
        if (hashlib.sha256(base_raw).hexdigest()!=output_sensor.BASE8_SHA256
                or hashlib.sha256(invocation_raw).hexdigest()!=output_sensor.INVOCATION10_SHA256):
            raise ValueError("LIFECYCLE_OUTPUT_DEPENDENCY_UNAVAILABLE")
        values.update({"guard_lifecycle_base.py":base_raw,"guard_attempt_invocation.py":invocation_raw})
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
    elif selected in (sglang_sensor,termination_sensor,attempt,stream_sensor,rank_sensor,init_sensor,profiler_sensor,weight_sensor,release_sensor):
        base_raw=Path(sensor.__file__).read_bytes()
        if hashlib.sha256(base_raw).hexdigest()!=(selected.BASE_SHA256 if selected in (attempt,release_sensor) else selected.BASE8_SHA256):
            raise ValueError("LIFECYCLE_ATTEMPT_DEPENDENCY_UNAVAILABLE")
        values["guard_lifecycle_base.py"]=base_raw
    elif selected is configured:
        base_raw = Path(sensor.__file__).read_bytes()
        if hashlib.sha256(base_raw).hexdigest() != configured.runtime_spec(profile)['base_sha256']:
            raise ValueError('CONFIGURED_LIFECYCLE_BASE_UNAVAILABLE')
        values['guard_lifecycle_base.py'] = base_raw
        for filename, (logical, digest) in (configured.startup_assets(profile) | configured.stream_assets(profile)
                                             | configured.approved_file_read_assets(profile)).items():
            raw = Path(sensor.__file__).with_name(logical + '.py').read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError('BASELINE_STARTUP_DEPENDENCY_UNAVAILABLE')
            values[filename] = raw
        if approved_file_read:
            values['model-read-scope.json'] = deployed_scope
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
    if model_read:manifest['model_read_scope']=scope_contract.scope_summary(scope)
    with (output/"manifest.json").open("x",encoding="utf-8",newline="\n") as stream:
        json.dump(manifest,stream,sort_keys=True,indent=2)
        stream.write("\n")
    return manifest


def load_prepared(path, *, expected_sha256=None):
    path = Path(path)
    if expected_sha256 is None:
        with path.open("rb") as stream:
            raw_manifest = stream.read(262145)
    else:
        raw_manifest = read_sensor.dependency(path, expected_sha256)
    manifest = strict_json(raw_manifest,limit=262144)
    release_variant = (type(manifest) is dict and type(manifest.get('profile')) is str
                       and manifest['profile'] in {v[0] for v in release_observation.PROFILES.values()})
    profile = manifest.get('profile') if type(manifest) is dict else None
    approved_file_read = configured.approved_file_read(profile)
    model_read = approved_file_read or type(profile) is str and profile in {
        read_sensor.PROFILE,mapping_sensor.PROFILE,startup_sensor.PROFILE,joint_sensor.PROFILE,generation_sensor.PROFILE,endpoint_sensor.PROFILE}
    variant_dependencies = _release_variant_dependencies(manifest['profile']) if release_variant else {}
    variant_assets = {
        'guard_lifecycle_sensor.py', 'request-binding-key.json', 'sitecustomize.py', 'settings.json',
        *variant_dependencies,
    }
    if (type(manifest) is not dict or type(manifest.get("profile")) is not str
            or set(manifest) != ({"schema","profile","session","public_commit",
            "assets","source_sha256","engine_started","image_contents_verified","product_connected"} | ({'fault_control_target_sha256'}if manifest.get('profile')==fault_sensor.PROFILE else set()) | ({'model_read_scope'} if model_read else set()))
            or not model_read and len(raw_manifest)>16384
            or manifest["schema"] != "guard-lifecycle-assets/1"  or type(manifest["profile"]) is not str
            or manifest["profile"] not in profiles.SCHEMAS
            or manifest["public_commit"] != profiles.public_commit(manifest["profile"])
            or manifest["source_sha256"] != profiles.source_hashes(manifest["profile"])
            or type(manifest["session"]) is not str or re.fullmatch(r"[0-9a-f]{32}",manifest["session"]) is None
            or manifest["engine_started"] is not False or manifest["image_contents_verified"] is not False
            or manifest["product_connected"] is not False
            or type(manifest["assets"]) is not dict
            or set(manifest["assets"]) != (variant_assets if release_variant else configured.asset_names(manifest["profile"]) if configured.contains(manifest["profile"]) else {"guard_lifecycle_sensor.py","sitecustomize.py","settings.json",*init_release.ASSETS} if manifest["profile"]==init_release.PROFILE else {"guard_lifecycle_sensor.py","sitecustomize.py","settings.json","guard_lifecycle_base.py",*fault_sensor.ASSETS,"fault-control-settings.json"}if manifest["profile"]==fault_sensor.PROFILE else {"guard_lifecycle_sensor.py","sitecustomize.py","settings.json","guard_lifecycle_base.py","guard_sglang_launcher.py"}if manifest["profile"]==sglang_sensor.PROFILE else (({'model-read-scope.json', 'guard_file_read_scope.py', 'guard_worker_readiness_callback.py', 'guard_worker_readiness_support.py', 'guard_lifecycle_base.py', 'guard_worker_startup_contract.py', 'settings.json', 'guard_weight_iteration.py', 'guard_lifecycle_sensor.py', 'guard_safe_open_mapping.py', 'sitecustomize.py'} | ({'guard_worker_startup.py','guard_initialization.py'} if manifest['profile'] in {joint_sensor.PROFILE,generation_sensor.PROFILE,endpoint_sensor.PROFILE} else set()) | ({'guard_worker_initialization.py','guard_worker_generation_contract.py'} if manifest['profile'] in {generation_sensor.PROFILE,endpoint_sensor.PROFILE} else set()) | ({'guard_worker_generation.py','guard_ready_endpoint_contract.py'} if manifest['profile']==endpoint_sensor.PROFILE else set())) if manifest['profile'] in {startup_sensor.PROFILE,joint_sensor.PROFILE,generation_sensor.PROFILE,endpoint_sensor.PROFILE} else {'guard_lifecycle_sensor.py','sitecustomize.py','settings.json','guard_lifecycle_base.py','guard_weight_iteration.py','guard_file_read_scope.py','model-read-scope.json'} if manifest['profile']==mapping_sensor.PROFILE else {'guard_lifecycle_sensor.py','sitecustomize.py','settings.json','guard_lifecycle_base.py','guard_weight_iteration.py','model-read-scope.json'} if manifest['profile']==read_sensor.PROFILE else {"guard_lifecycle_sensor.py" ,"sitecustomize.py","settings.json",
                "guard_lifecycle_base.py","guard_online_input_callback.py"} if manifest["profile"] == online.PROFILE
                else {"guard_lifecycle_sensor.py","sitecustomize.py","settings.json","guard_lifecycle_base.py","guard_attempt_invocation.py"}
                    if manifest["profile"]==output_sensor.PROFILE else {"guard_lifecycle_sensor.py","sitecustomize.py","settings.json","guard_lifecycle_base.py"}
                    if manifest["profile"] in {sglang_sensor.PROFILE,termination_sensor.PROFILE,attempt.PROFILE,stream_sensor.PROFILE,rank_sensor.PROFILE,init_sensor.PROFILE,profiler_sensor.PROFILE,weight_sensor.PROFILE,release_sensor.PROFILE} else {"guard_lifecycle_sensor.py","sitecustomize.py","settings.json"}))):
        raise ValueError("LIFECYCLE_ASSETS_INVALID")
    if manifest['profile']==fault_sensor.PROFILE and not fault_control.fingerprint(manifest['fault_control_target_sha256']):raise ValueError('FAULT_CONTROL_TARGET_INVALID')
    dependencies = configured.startup_assets(profile) | configured.stream_assets(profile) | configured.approved_file_read_assets(profile)
    source_supported = False
    for name, expected in manifest["assets"].items():
        if (type(expected) is not dict or set(expected) != {"sha256","bytes"}
                or type(expected["bytes"]) is not int or not 0 < expected["bytes"] <= 262144
                or type(expected["sha256"]) is not str or re.fullmatch(r"[0-9a-f]{64}",expected["sha256"]) is None):
            raise ValueError("LIFECYCLE_ASSET_METADATA_INVALID")
        private_key = (configured.request_binding(manifest['profile']) or release_variant) and name == 'request-binding-key.json'
        if private_key:
            key_path = path.parent / name
            before = key_path.lstat()
            if (not stat.S_ISREG(before.st_mode) or not hasattr(os, 'geteuid') or before.st_uid != os.geteuid()
                    or stat.S_IMODE(before.st_mode) != 0o600 or not 0 < before.st_size <= 256):
                raise ValueError('REQUEST_BINDING_KEY_INVALID')
            fd = os.open(key_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                opened = os.fstat(fd); raw = os.read(fd, 257); after = os.fstat(fd)
            finally:
                os.close(fd)
            identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            if (len(raw) != before.st_size or len({identity(s) for s in (before, opened, after, key_path.lstat())}) != 1):
                raise ValueError('REQUEST_BINDING_KEY_INVALID')
        elif name in dependencies:
            try:
                raw = configured.startup._startup_read(name, dependencies[name][1], directory_path=str(path.parent))
            except OSError:
                raise ValueError('BASELINE_STARTUP_DEPENDENCY_UNAVAILABLE') from None
        else:
            with (path.parent/name).open("rb") as stream:
                raw = stream.read(262145)
        if len(raw) != expected["bytes"] or hashlib.sha256(raw).hexdigest() != expected["sha256"]:
            raise ValueError("LIFECYCLE_ASSET_BYTES_INVALID")
        if (configured.request_binding(manifest['profile']) or release_variant) and name == 'request-binding-key.json':
            status = (path.parent / name).lstat()
            value = strict_json(raw, limit=256)
            if (not stat.S_ISREG(status.st_mode) or not hasattr(os, 'geteuid') or status.st_uid != os.geteuid()
                    or stat.S_IMODE(status.st_mode) != 0o600 or type(value) is not dict
                    or set(value) != {'schema', 'session', 'key_hex'}
                    or value['schema'] != 'guard-lifecycle-request-key/1' or value['session'] != manifest['session']
                    or type(value['key_hex']) is not str or re.fullmatch('[0-9a-f]{64}', value['key_hex']) is None):
                raise ValueError('REQUEST_BINDING_KEY_INVALID')
        if manifest['profile']==fault_sensor.PROFILE:
            if name in fault_sensor.ASSETS and hashlib.sha256(raw).hexdigest()!=fault_sensor.ASSETS[name]:raise ValueError('FAULT_DEPENDENCY_UNAVAILABLE')
            if name=='guard_lifecycle_base.py'and hashlib.sha256(raw).hexdigest()!=fault_sensor.BASE8_SHA256:raise ValueError('FAULT_BASE_DEPENDENCY_UNAVAILABLE')
            if name=='fault-control-settings.json':
                expected_scope={'schema':'byul-native-fault-settings/1','session':manifest['session'],'target_sha256':manifest['fault_control_target_sha256'],'directory':'/guard-lifecycle/fault-control'}
                if strict_json(raw,limit=16384)!=expected_scope:raise ValueError('FAULT_CONTROL_SCOPE_INVALID')
        if manifest["profile"] == online.PROFILE and name in {"guard_lifecycle_base.py", "guard_online_input_callback.py"}:
            required = online.BASE_SHA256 if name == "guard_lifecycle_base.py" else online.CALLBACK_SHA256
            if hashlib.sha256(raw).hexdigest() != required:
                raise ValueError("LIFECYCLE_ONLINE_DEPENDENCY_UNAVAILABLE")
        if manifest['profile']==sglang_sensor.PROFILE and name=='guard_sglang_launcher.py'and (hashlib.sha256(raw).hexdigest()!='99856447cc643b4597851b6e330d6744e6ca11d3ff0c94493b6df07a8a5b7e4a'or raw!=Path(sglang_launcher.__file__).read_bytes()):
            raise ValueError('SGLANG_LAUNCH_DEPENDENCY_UNAVAILABLE')
        if manifest["profile"] in {sglang_sensor.PROFILE,termination_sensor.PROFILE} and name=="guard_lifecycle_base.py" and hashlib.sha256(raw).hexdigest()!=termination_sensor.BASE8_SHA256:
            raise ValueError("LIFECYCLE_TERMINATION_DEPENDENCY_UNAVAILABLE")
        if manifest["profile"]==attempt.PROFILE and name=="guard_lifecycle_base.py" and hashlib.sha256(raw).hexdigest()!=attempt.BASE_SHA256:
            raise ValueError("LIFECYCLE_ATTEMPT_DEPENDENCY_UNAVAILABLE")
        if configured.contains(manifest['profile']) and name == 'guard_lifecycle_base.py':
            if hashlib.sha256(raw).hexdigest() != configured.runtime_spec(manifest['profile'])['base_sha256']:
                raise ValueError('CONFIGURED_LIFECYCLE_BASE_UNAVAILABLE')
        if name in dependencies:
            logical, digest = dependencies[name]
            if (hashlib.sha256(raw).hexdigest() != digest
                    or raw != Path(sensor.__file__).with_name(logical + '.py').read_bytes()):
                raise ValueError('BASELINE_STARTUP_DEPENDENCY_UNAVAILABLE')
        if manifest["profile"]==release_sensor.PROFILE and name=="guard_lifecycle_base.py" and hashlib.sha256(raw).hexdigest()!=release_sensor.BASE_SHA256:
            raise ValueError("LIFECYCLE_RELEASE_DEPENDENCY_UNAVAILABLE")
        if name in variant_dependencies and expected['sha256'] != variant_dependencies[name][1]:
            raise ValueError('LIFECYCLE_OBSERVATION_DEPENDENCY_UNAVAILABLE')
        if manifest["profile"]==init_release.PROFILE and name in init_release.ASSETS and hashlib.sha256(raw).hexdigest()!=init_release.ASSETS[name]:
            raise ValueError("INITIALIZATION_RELEASE_DEPENDENCY_UNAVAILABLE")
        if manifest["profile"]==init_sensor.PROFILE and name=="guard_lifecycle_base.py" and hashlib.sha256(raw).hexdigest()!=init_sensor.BASE8_SHA256:
            raise ValueError("LIFECYCLE_INITIALIZATION_DEPENDENCY_UNAVAILABLE")
        if manifest["profile"]==weight_sensor.PROFILE and name=="guard_lifecycle_base.py" and hashlib.sha256(raw).hexdigest()!=weight_sensor.BASE8_SHA256:
            raise ValueError("LIFECYCLE_WEIGHT_ITERATION_DEPENDENCY_UNAVAILABLE")
        if manifest["profile"]==profiler_sensor.PROFILE and name=="guard_lifecycle_base.py" and hashlib.sha256(raw).hexdigest()!=profiler_sensor.BASE8_SHA256:
            raise ValueError("LIFECYCLE_PROFILER_DEPENDENCY_UNAVAILABLE")
        if manifest["profile"]==rank_sensor.PROFILE and name=="guard_lifecycle_base.py" and hashlib.sha256(raw).hexdigest()!=rank_sensor.BASE8_SHA256:
            raise ValueError("LIFECYCLE_RANK_DEPENDENCY_UNAVAILABLE")
        if manifest["profile"]==stream_sensor.PROFILE and name=="guard_lifecycle_base.py" and hashlib.sha256(raw).hexdigest()!=stream_sensor.BASE8_SHA256:
            raise ValueError("LIFECYCLE_STREAM_DEPENDENCY_UNAVAILABLE")
        if manifest["profile"]==output_sensor.PROFILE and name in {"guard_lifecycle_base.py","guard_attempt_invocation.py"}:
            required=output_sensor.BASE8_SHA256 if name=="guard_lifecycle_base.py" else output_sensor.INVOCATION10_SHA256
            if hashlib.sha256(raw).hexdigest()!=required:raise ValueError("LIFECYCLE_OUTPUT_DEPENDENCY_UNAVAILABLE")
        if manifest['profile'] in {read_sensor.PROFILE,mapping_sensor.PROFILE,startup_sensor.PROFILE,joint_sensor.PROFILE,generation_sensor.PROFILE,endpoint_sensor.PROFILE}:
            required={'guard_lifecycle_base.py':read_sensor.BASE8_SHA256,'guard_weight_iteration.py':read_sensor.ITERATION16_SHA256,**({'guard_file_read_scope.py':mapping_sensor.SCOPE17_SHA256} if manifest['profile'] in {mapping_sensor.PROFILE,startup_sensor.PROFILE,joint_sensor.PROFILE,generation_sensor.PROFILE,endpoint_sensor.PROFILE} else {})}.get(name)
            if required is not None and hashlib.sha256(raw).hexdigest()!=required:raise ValueError('LIFECYCLE_FILE_READ_DEPENDENCY_UNAVAILABLE')
        if model_read and name=='model-read-scope.json':
            from guard.collectors.model_read_scope import scope_summary
            value=read_sensor.validate_scope(strict_json(raw,limit=262144),session=manifest['session'])
            if (scope_summary(value)!=manifest['model_read_scope']
                    or approved_file_read and not value['hash_model_bytes']):
                raise ValueError('MODEL_READ_SCOPE_MANIFEST_MISMATCH')
        if manifest['profile'] in {startup_sensor.PROFILE,joint_sensor.PROFILE,generation_sensor.PROFILE,endpoint_sensor.PROFILE}:
            dependency={'guard_lifecycle_base.py':startup_sensor.BASE8_SHA256,'guard_weight_iteration.py':startup_sensor.ITERATION16_SHA256,
                'guard_file_read_scope.py':startup_sensor.SCOPE17_SHA256,'guard_safe_open_mapping.py':startup_sensor.MAPPING18_SHA256,
                'guard_worker_startup_contract.py':startup_sensor.CONTRACT_SHA256,'guard_worker_readiness_callback.py':startup_sensor.CALLBACK_SHA256,
                'guard_worker_readiness_support.py':startup_sensor.SUPPORT_SHA256}.get(name)
            if dependency is not None and hashlib.sha256(raw).hexdigest()!=dependency:raise ValueError('WORKER_STARTUP_DEPENDENCY_UNAVAILABLE')
        if manifest['profile'] in {joint_sensor.PROFILE,generation_sensor.PROFILE,endpoint_sensor.PROFILE}:
            expected_joint={'guard_worker_startup.py':joint_sensor.STARTUP19_SHA256,'guard_initialization.py':joint_sensor.INITIALIZATION14_SHA256}.get(name)
            if expected_joint is not None and hashlib.sha256(raw).hexdigest()!=expected_joint:raise ValueError('WORKER_INITIALIZATION_DEPENDENCY_UNAVAILABLE')
        if manifest['profile'] in {generation_sensor.PROFILE,endpoint_sensor.PROFILE}:
            required_generation={'guard_worker_initialization.py':generation_sensor.JOINT20_SHA256,'guard_worker_generation_contract.py':generation_sensor.CONTRACT_SHA256}.get(name)
            if required_generation is not None and hashlib.sha256(raw).hexdigest()!=required_generation:raise ValueError('WORKER_GENERATION_DEPENDENCY_UNAVAILABLE')
        if manifest['profile']==endpoint_sensor.PROFILE:
            expected_endpoint={'guard_worker_generation.py':endpoint_sensor.GENERATION21_SHA256,'guard_ready_endpoint_contract.py':endpoint_sensor.CONTRACT_SHA256}.get(name)
            if expected_endpoint is not None and hashlib.sha256(raw).hexdigest()!=expected_endpoint:raise ValueError('READY_ENDPOINT_DEPENDENCY_UNAVAILABLE')
        if name == "guard_lifecycle_sensor.py":
            source_supported = profiles.asset_supported(manifest["profile"], raw,
                **({'request_key_sha256': manifest['assets']['request-binding-key.json']['sha256']}
                   if configured.request_binding(manifest['profile']) or release_variant else {}),
                **({'model_read_scope_sha256': manifest['assets']['model-read-scope.json']['sha256']}
                   if approved_file_read else {}))
    if not source_supported:
        raise ValueError("LIFECYCLE_ASSET_VERSION_UNAVAILABLE")
    return manifest


def load_initialization_prepared(path, env, *, expected_sha256=None):
    """Validate locally prepared initialization assets against the declared ENV."""
    from guard.adapters.vllm_release_profiles import validate_initialization_preparation

    prepared = load_prepared(path, expected_sha256=expected_sha256)
    validate_initialization_preparation(env, prepared['profile'])
    return prepared


def request_header_fingerprint(manifest_path, prepared, header):
    """Derive a new-profile ASGI header HMAC from the validated private key.

    This reopens and verifies the exact key after `load_prepared`; no key bytes
    or raw header leave the caller. A changed or unreadable key is a gap.
    """
    if (not (configured.request_binding(prepared.get('profile')) or prepared.get('profile') in
            {v[0] for v in release_observation.PROFILES.values()}) or type(header) is not str
            or not 0 < len(header.encode('utf-8', 'surrogatepass')) <= 512):
        raise ValueError('REQUEST_BINDING_HEADER_UNAVAILABLE')
    path = Path(manifest_path).parent / 'request-binding-key.json'
    try:
        before = path.lstat()
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) != 0o600 or not 0 < before.st_size <= 256):
            raise ValueError
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            opened = os.fstat(fd)
            raw = os.read(fd, 257)
            after = os.fstat(fd)
        finally:
            os.close(fd)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        expected = prepared['assets']['request-binding-key.json']
        if (len(raw) != before.st_size or len({identity(s) for s in (before, opened, after, path.lstat())}) != 1
                or len(raw) != expected['bytes'] or hashlib.sha256(raw).hexdigest() != expected['sha256']):
            raise ValueError
        key = strict_json(raw, limit=256)
        if (type(key) is not dict or set(key) != {'schema', 'session', 'key_hex'}
                or key['schema'] != 'guard-lifecycle-request-key/1' or key['session'] != prepared['session']
                or type(key['key_hex']) is not str or re.fullmatch('[0-9a-f]{64}', key['key_hex']) is None):
            raise ValueError
        return hmac.new(bytes.fromhex(key['key_hex']),
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
            if configured.approved_file_read(prepared['profile']):
                if not configured.approved_file_read_matches(env, prepared):
                    raise ValueError('PROFILE_UNREVIEWED')
                from guard.collectors.model_read_scope import validate_binding as validate_read_scope
                validate_read_scope(prepared, env, target)
            else:
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
