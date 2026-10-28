"""Explicit owned Kubernetes lifecycle input binding and collection."""
from __future__ import annotations

from pathlib import Path
import math

from guard.collectors.file import load_environment
from guard.collectors.lifecycle import load_prepared
from guard.executors.owned_capture import strict_json
from guard.models import DataOrigin


def validate_options(binary,duration):
    if (type(binary) is not str or not binary.strip() or type(duration) not in (int,float)
            or not math.isfinite(duration) or not 0<duration<=300):
        raise ValueError("KUBERNETES_CAPTURE_OPTIONS_INVALID")


def load_inputs(env_path,target_path,prepared_path):
    from guard.executors.kubernetes_lifecycle_capture import validate_target

    env=load_environment(Path(env_path))
    with Path(target_path).open("rb") as stream:
        target=strict_json(stream.read(65537))
    prepared=load_prepared(prepared_path)
    validate_target(target,env)
    return env,target,prepared


def load_model_mapping(env,path):
    from guard.collectors.model_mapping import validate_model_mapping
    with Path(path).open("rb") as stream:
        value=strict_json(stream.read(65537))
    return validate_model_mapping(env,value)


def collect(env,target,prepared,*,binary="kubectl",duration=180,runner=None,model_mapping=None):
    from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader
    from guard.executors.lifecycle_session import capture_session
    if model_mapping is not None:
        from guard.collectors.model_mapping import validate_model_mapping
        validate_model_mapping(env,model_mapping)
    client=KubernetesLifecycleReader(target,env,prepared,binary=binary,duration=duration,runner=runner)
    before={}
    if model_mapping is not None:
        try:before=client.snapshot()
        except ValueError:pass
    engine=client.binding["engine"]
    result=capture_session(client,target,container=engine["container"],image=engine["image"],prepared=prepared,
        owner_identity=client.owner_identity,read_file=client.read_file,image_check=client.image_check)
    if model_mapping is not None:
        from guard.executors.model_mounts import analyze_model_mount
        try:after=client.snapshot()
        except ValueError:after={}
        result["model_mount"]=analyze_model_mount(env,target,model_mapping,before,after)
        result["model_mount"]["scope"]="session_snapshot_interval"
        result["model_mount"]["whole_run_coverage_verified"]=False
    origin=DataOrigin.EXAMPLE if client.injected or env.data_origin==DataOrigin.EXAMPLE else DataOrigin.DECLARED
    return {**result,"data_origin":origin.value,"environment_ref":env.ref().model_dump(mode="json"),
        "origin_detail":"Kubernetes API ownership and bounded exec metadata; native producer and runtime image ancestry remain unauthenticated",
        "transport":client.report()}
