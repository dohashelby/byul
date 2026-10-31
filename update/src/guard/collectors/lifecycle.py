"""Prepare scoped lifecycle assets and bind inputs; no engine or client creation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import uuid

from guard.adapters import vllm_lifecycle_sensor as sensor
from guard.adapters import vllm_safe_open_mapping_sensor as mapping_sensor
from guard.adapters import vllm_ready_endpoint_sensor as endpoint_sensor,vllm_ready_endpoint_contract as endpoint_contract
from guard.adapters import vllm_worker_generation_sensor as generation_sensor,vllm_worker_generation_contract as generation_contract
from guard.adapters import vllm_worker_initialization_sensor as joint_sensor
from guard.adapters import vllm_worker_startup_sensor as startup_sensor,vllm_worker_startup_contract as startup_contract
from guard.adapters import vllm_lifecycle_profiles as profiles
from guard.adapters import vllm_online_sensor as online
from guard.adapters import vllm_attempt_sensor as attempt
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


def prepare_assets(output, repository, *, profile=None, model_read_scope=None):
    selected = sensor if profile in (None, sensor.PROFILE) else online if profile == online.PROFILE else attempt if profile == attempt.PROFILE else output_sensor if profile == output_sensor.PROFILE else stream_sensor if profile == stream_sensor.PROFILE else rank_sensor if profile == rank_sensor.PROFILE else init_sensor if profile == init_sensor.PROFILE else profiler_sensor if profile == profiler_sensor.PROFILE else weight_sensor if profile == weight_sensor.PROFILE else read_sensor if profile == read_sensor.PROFILE else mapping_sensor if profile == mapping_sensor.PROFILE else startup_sensor if profile == startup_sensor.PROFILE else joint_sensor if profile == joint_sensor.PROFILE else generation_sensor if profile == generation_sensor.PROFILE else endpoint_sensor if profile == endpoint_sensor.PROFILE else None
    if selected is None:
        raise ValueError("LIFECYCLE_PROFILE_UNAVAILABLE")
    from guard.collectors import model_read_scope as scope_contract
    if selected in (read_sensor,mapping_sensor,startup_sensor,joint_sensor,generation_sensor,endpoint_sensor):
        if model_read_scope is None:raise ValueError('MODEL_READ_SCOPE_REQUIRED')
        scope,scope_raw=scope_contract.load_scope(model_read_scope)
    elif model_read_scope is not None:raise ValueError('MODEL_READ_SCOPE_PROFILE_MISMATCH')
    output = Path(output).absolute()
    if any(parent.is_symlink() or parent.is_junction() or (parent/".git").exists() for parent in output.parents):
        raise ValueError("LIFECYCLE_OUTPUT_IN_REPOSITORY")
    output = reserve_output(output, protected=[Path(repository)])
    source = Path(selected.__file__).read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    session = uuid.uuid4().hex
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
    if selected in (read_sensor,mapping_sensor,startup_sensor,joint_sensor,generation_sensor,endpoint_sensor):
        base_raw=Path(sensor.__file__).read_bytes();iteration_raw=Path(weight_sensor.__file__).read_bytes()
        if (hashlib.sha256(base_raw).hexdigest()!=read_sensor.BASE8_SHA256 or hashlib.sha256(iteration_raw).hexdigest()!=read_sensor.ITERATION16_SHA256):
            raise ValueError('LIFECYCLE_FILE_READ_DEPENDENCY_UNAVAILABLE')
        deployed=scope_contract.deploy_scope(scope,scope_raw,session)
        values.update({'guard_lifecycle_base.py':base_raw,'guard_weight_iteration.py':iteration_raw,'model-read-scope.json':deployed})
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
    elif selected in (attempt,stream_sensor,rank_sensor,init_sensor,profiler_sensor,weight_sensor):
        base_raw=Path(sensor.__file__).read_bytes()
        if hashlib.sha256(base_raw).hexdigest()!=(attempt.BASE_SHA256 if selected is attempt else selected.BASE8_SHA256):
            raise ValueError("LIFECYCLE_ATTEMPT_DEPENDENCY_UNAVAILABLE")
        values["guard_lifecycle_base.py"]=base_raw
    assets = {}
    for name, data in values.items():
        with (output/name).open("xb") as stream:
            stream.write(data)
        assets[name] = {"sha256":hashlib.sha256(data).hexdigest(),"bytes":len(data)}
    manifest = {"schema":"guard-lifecycle-assets/1", "profile":selected.PROFILE, "session":session,
        "public_commit":sensor.COMMIT, "assets":assets,
        "source_sha256":profiles.source_hashes(selected.PROFILE), "engine_started":False,
        "image_contents_verified":False, "product_connected":False}
    if selected in (read_sensor,mapping_sensor,startup_sensor,joint_sensor,generation_sensor,endpoint_sensor):manifest['model_read_scope']=scope_contract.scope_summary(scope)
    with (output/"manifest.json").open("x",encoding="utf-8",newline="\n") as stream:
        json.dump(manifest,stream,sort_keys=True,indent=2)
        stream.write("\n")
    return manifest


def load_prepared(path):
    path = Path(path)
    with path.open("rb") as stream:
        raw_manifest=stream.read(262145)
        manifest = strict_json(raw_manifest,limit=262144)
    if (type(manifest) is not dict or type(manifest.get("profile")) is not str
            or set(manifest) != ({"schema","profile","session","public_commit",
            "assets","source_sha256","engine_started","image_contents_verified","product_connected"} | ({'model_read_scope'} if manifest.get('profile') in {read_sensor.PROFILE,mapping_sensor.PROFILE,startup_sensor.PROFILE,joint_sensor.PROFILE,generation_sensor.PROFILE,endpoint_sensor.PROFILE} else set()))
            or manifest.get('profile') not in {read_sensor.PROFILE,mapping_sensor.PROFILE,startup_sensor.PROFILE,joint_sensor.PROFILE,generation_sensor.PROFILE,endpoint_sensor.PROFILE} and len(raw_manifest)>16384
            or manifest["schema"] != "guard-lifecycle-assets/1"  or type(manifest["profile"]) is not str
            or manifest["profile"] not in profiles.SCHEMAS
            or manifest["public_commit"] != sensor.COMMIT
            or manifest["source_sha256"] != profiles.source_hashes(manifest["profile"])
            or type(manifest["session"]) is not str or re.fullmatch(r"[0-9a-f]{32}",manifest["session"]) is None
            or manifest["engine_started"] is not False or manifest["image_contents_verified"] is not False
            or manifest["product_connected"] is not False
            or type(manifest["assets"]) is not dict
            or set(manifest["assets"]) != (({'model-read-scope.json', 'guard_file_read_scope.py', 'guard_worker_readiness_callback.py', 'guard_worker_readiness_support.py', 'guard_lifecycle_base.py', 'guard_worker_startup_contract.py', 'settings.json', 'guard_weight_iteration.py', 'guard_lifecycle_sensor.py', 'guard_safe_open_mapping.py', 'sitecustomize.py'} | ({'guard_worker_startup.py','guard_initialization.py'} if manifest['profile'] in {joint_sensor.PROFILE,generation_sensor.PROFILE,endpoint_sensor.PROFILE} else set()) | ({'guard_worker_initialization.py','guard_worker_generation_contract.py'} if manifest['profile'] in {generation_sensor.PROFILE,endpoint_sensor.PROFILE} else set()) | ({'guard_worker_generation.py','guard_ready_endpoint_contract.py'} if manifest['profile']==endpoint_sensor.PROFILE else set())) if manifest['profile'] in {startup_sensor.PROFILE,joint_sensor.PROFILE,generation_sensor.PROFILE,endpoint_sensor.PROFILE} else {'guard_lifecycle_sensor.py','sitecustomize.py','settings.json','guard_lifecycle_base.py','guard_weight_iteration.py','guard_file_read_scope.py','model-read-scope.json'} if manifest['profile']==mapping_sensor.PROFILE else {'guard_lifecycle_sensor.py','sitecustomize.py','settings.json','guard_lifecycle_base.py','guard_weight_iteration.py','model-read-scope.json'} if manifest['profile']==read_sensor.PROFILE else {"guard_lifecycle_sensor.py" ,"sitecustomize.py","settings.json",
                "guard_lifecycle_base.py","guard_online_input_callback.py"} if manifest["profile"] == online.PROFILE
                else {"guard_lifecycle_sensor.py","sitecustomize.py","settings.json","guard_lifecycle_base.py","guard_attempt_invocation.py"}
                    if manifest["profile"]==output_sensor.PROFILE else {"guard_lifecycle_sensor.py","sitecustomize.py","settings.json","guard_lifecycle_base.py"}
                    if manifest["profile"] in {attempt.PROFILE,stream_sensor.PROFILE,rank_sensor.PROFILE,init_sensor.PROFILE,profiler_sensor.PROFILE,weight_sensor.PROFILE} else {"guard_lifecycle_sensor.py","sitecustomize.py","settings.json"})):
        raise ValueError("LIFECYCLE_ASSETS_INVALID")
    source_supported = False
    for name, expected in manifest["assets"].items():
        if (type(expected) is not dict or set(expected) != {"sha256","bytes"}
                or type(expected["bytes"]) is not int or not 0 < expected["bytes"] <= 262144
                or type(expected["sha256"]) is not str or re.fullmatch(r"[0-9a-f]{64}",expected["sha256"]) is None):
            raise ValueError("LIFECYCLE_ASSET_METADATA_INVALID")
        with (path.parent/name).open("rb") as stream:
            raw = stream.read(262145)
        if len(raw) != expected["bytes"] or hashlib.sha256(raw).hexdigest() != expected["sha256"]:
            raise ValueError("LIFECYCLE_ASSET_BYTES_INVALID")
        if manifest["profile"] == online.PROFILE and name in {"guard_lifecycle_base.py", "guard_online_input_callback.py"}:
            required = online.BASE_SHA256 if name == "guard_lifecycle_base.py" else online.CALLBACK_SHA256
            if hashlib.sha256(raw).hexdigest() != required:
                raise ValueError("LIFECYCLE_ONLINE_DEPENDENCY_UNAVAILABLE")
        if manifest["profile"]==attempt.PROFILE and name=="guard_lifecycle_base.py" and hashlib.sha256(raw).hexdigest()!=attempt.BASE_SHA256:
            raise ValueError("LIFECYCLE_ATTEMPT_DEPENDENCY_UNAVAILABLE")
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
            if name=='model-read-scope.json':
                from guard.collectors.model_read_scope import scope_summary
                value=read_sensor.validate_scope(strict_json(raw,limit=262144),session=manifest['session'])
                if scope_summary(value)!=manifest['model_read_scope']:raise ValueError('MODEL_READ_SCOPE_MANIFEST_MISMATCH')
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
            source_supported = profiles.asset_supported(manifest["profile"], raw)
    if not source_supported:
        raise ValueError("LIFECYCLE_ASSET_VERSION_UNAVAILABLE")
    return manifest


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
    if prepared["profile"]==rank_sensor.PROFILE and (env.engine.name!="vllm" or env.engine.version!="0.31.0"):
        raise ValueError("LIFECYCLE_RANK_ENGINE_UNSUPPORTED")
    if prepared["profile"] in {attempt.PROFILE,output_sensor.PROFILE,stream_sensor.PROFILE} and (env.engine.name!="vllm" or env.engine.version!="0.31.0"):
        raise ValueError("LIFECYCLE_ATTEMPT_ENGINE_UNSUPPORTED")
    if prepared["profile"] == online.PROFILE and (env.engine.name != "vllm" or env.engine.version != "0.31.0"):
        raise ValueError("LIFECYCLE_ONLINE_ENGINE_UNSUPPORTED")
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
