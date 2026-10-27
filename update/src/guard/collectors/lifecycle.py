"""Prepare scoped lifecycle assets and bind inputs; no engine or client creation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import uuid

from guard.adapters import vllm_lifecycle_sensor as sensor
from guard.adapters import vllm_lifecycle_profiles as profiles
from guard.collectors.file import load_environment
from guard.collectors.route_observations import validate_output
from guard.executors.owned_capture import strict_json, validate_owned_source
from guard.models import DataOrigin
from guard.update_validation import reserve_output


def prepare_assets(output, repository):
    output = Path(output).absolute()
    if any(parent.is_symlink() or parent.is_junction() or (parent/".git").exists() for parent in output.parents):
        raise ValueError("LIFECYCLE_OUTPUT_IN_REPOSITORY")
    output = reserve_output(output, protected=[Path(repository)])
    source = Path(sensor.__file__).read_bytes()
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
    assets = {}
    for name, data in values.items():
        with (output/name).open("xb") as stream:
            stream.write(data)
        assets[name] = {"sha256":hashlib.sha256(data).hexdigest(),"bytes":len(data)}
    manifest = {"schema":"guard-lifecycle-assets/1", "profile":sensor.PROFILE, "session":session,
        "public_commit":sensor.COMMIT, "assets":assets,
        "source_sha256":sensor.SOURCE_SHA, "engine_started":False,
        "image_contents_verified":False, "product_connected":False}
    with (output/"manifest.json").open("x",encoding="utf-8",newline="\n") as stream:
        json.dump(manifest,stream,sort_keys=True,indent=2)
        stream.write("\n")
    return manifest


def load_prepared(path):
    path = Path(path)
    with path.open("rb") as stream:
        manifest = strict_json(stream.read(16385),limit=16384)
    if (type(manifest) is not dict or set(manifest) != {"schema","profile","session","public_commit",
            "assets","source_sha256","engine_started","image_contents_verified","product_connected"}
            or manifest["schema"] != "guard-lifecycle-assets/1" or type(manifest["profile"]) is not str
            or manifest["profile"] not in profiles.SCHEMAS
            or manifest["public_commit"] != sensor.COMMIT
            or manifest["source_sha256"] != profiles.source_hashes(manifest["profile"])
            or type(manifest["session"]) is not str or re.fullmatch(r"[0-9a-f]{32}",manifest["session"]) is None
            or manifest["engine_started"] is not False or manifest["image_contents_verified"] is not False
            or manifest["product_connected"] is not False
            or type(manifest["assets"]) is not dict
            or set(manifest["assets"]) != {"guard_lifecycle_sensor.py","sitecustomize.py","settings.json"}):
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
    validate_owned_source(**source)
    if (not env.hash_ok() or type(target) is not dict
            or target.get("environment_ref") != env.ref().model_dump(mode="json")
            or type(target.get("run_id")) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}",target["run_id"]) is None
            or type(target.get("engine")) is not dict
            or target["engine"].get("container") != source["container"]
            or source["image"] != env.engine.image_digest):
        raise ValueError("LIFECYCLE_CAPTURE_INPUT_INVALID")


def collect(docker, env, target, source, prepared):
    validate_binding(env,target,source,prepared)
    from guard.executors.lifecycle_capture import capture
    value = capture(docker,target,source=source,prepared=prepared)
    origin = DataOrigin.EXAMPLE if env.data_origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED
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
    value=capture_session(docker,target,container=container,image=image,prepared=prepared)
    origin=DataOrigin.EXAMPLE if env.data_origin==DataOrigin.EXAMPLE else DataOrigin.DECLARED
    return {**value,"data_origin":origin.value,"environment_ref":env.ref().model_dump(mode="json"),
        "origin_detail":"owned container census and recording bytes; producer assertions and fingerprint links remain unauthenticated"}
