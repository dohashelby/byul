"""EXAMPLE engine target/owner binding; no Docker service or credential reads."""
from copy import deepcopy
import json

import pytest

from guard.executors.owned_capture import engine_identity,validate_engine_target
from guard.collectors import lifecycle
from tests.builders import environment
from tests.test_lifecycle_capture import case,external
from tests.test_lifecycle_sensor import writer_factory

IMAGE="example.invalid/engine@sha256:"+"a"*64
CANARY="EXAMPLE_ENGINE_PRIVATE_ENV"


def inputs():
    env=environment(engine={"name":"vllm","version":"0.31.0","image_digest":IMAGE})
    target={"run_id":"EXAMPLE-owner","environment_ref":env.ref().model_dump(mode="json"),
        "engine":{"container":"EXAMPLE-engine"}}
    return env,target


class World:
    def __init__(self):
        self.calls=0
        self.failure=False
        self.row={"Id":"b"*64,"Name":"/EXAMPLE-engine","Image":"sha256:"+"c"*64,
            "Config":{"Image":IMAGE,"Labels":{"guard.run":"EXAMPLE-owner"},"Env":[CANARY]},
            "State":{"Running":True,"StartedAt":"2026-10-28T00:00:00Z"}}
    def inspect_json(self,container,*,timeout):
        assert container=="EXAMPLE-engine" and timeout==5
        self.calls+=1
        if self.failure:raise ValueError(CANARY)
        return deepcopy(self.row)


def test_exact_engine_declaration_and_owned_identity_return_only_minimal_fields():
    env,target=inputs();world=World()
    assert validate_engine_target(env,target,"EXAMPLE-engine",IMAGE) is None and world.calls==0
    value=engine_identity(world,target,env,"EXAMPLE-engine",IMAGE,5)
    assert value=={"container_id":"b"*64,"image_id":"sha256:"+"c"*64,
        "started_at":"2026-10-28T00:00:00Z","image":IMAGE}
    assert world.calls==1 and CANARY not in json.dumps(value)


@pytest.mark.parametrize("damage",["env-seal","no-target","no-ref","wrong-ref","no-run","bad-run","no-engine",
    "wrong-container","argument-container","wrong-image","unpinned-image"])
def test_invalid_declared_engine_is_rejected_before_inspection(damage):
    env,target=inputs();world=World();container="EXAMPLE-engine";image=IMAGE
    if damage=="env-seal":env=env.model_copy(update={"content_sha256":"0"*64})
    elif damage=="no-target":target=[]
    elif damage=="no-ref":del target["environment_ref"]
    elif damage=="wrong-ref":target["environment_ref"]["sha256"]="0"*64
    elif damage=="no-run":del target["run_id"]
    elif damage=="bad-run":target["run_id"]=CANARY+"/"
    elif damage=="no-engine":target["engine"]=None
    elif damage=="wrong-container":target["engine"]["container"]="EXAMPLE-other"
    elif damage=="argument-container":container="EXAMPLE-other"
    elif damage=="wrong-image":image="example.invalid/other@sha256:"+"d"*64
    else:image="unpinned:latest"
    with pytest.raises(ValueError,match="^CAPTURE_ENGINE_TARGET_INVALID$"):
        engine_identity(world,target,env,container,image,5)
    assert world.calls==0


@pytest.mark.parametrize("damage",["owner","image","name","container-id","image-id","stopped","started"])
def test_stable_wrong_instance_is_never_verified_by_repeated_inspection(damage):
    env,target=inputs();world=World()
    if damage=="owner":world.row["Config"]["Labels"]["guard.run"]="EXAMPLE-other"
    elif damage=="image":world.row["Config"]["Image"]="example.invalid/other@sha256:"+"d"*64
    elif damage=="name":world.row["Name"]="/EXAMPLE-other"
    elif damage=="container-id":world.row["Id"]="unverified"
    elif damage=="image-id":world.row["Image"]="unverified"
    elif damage=="stopped":world.row["State"]["Running"]=False
    else:world.row["State"]["StartedAt"]=None
    first=deepcopy(world.row)
    for _ in range(2):
        with pytest.raises(ValueError,match="^CAPTURE_OWNER_UNVERIFIED$"):
            engine_identity(world,target,env,"EXAMPLE-engine",IMAGE,5)
    assert world.row==first and world.calls==2


@pytest.mark.parametrize("damage",["none","list","config","state","labels","decode"])
def test_missing_or_invalid_inspect_metadata_has_fixed_unverified_diagnostic(damage):
    env,target=inputs();world=World()
    if damage=="none":world.row=None
    elif damage=="list":world.row=[]
    elif damage=="config":world.row["Config"]=None
    elif damage=="state":world.row["State"]=None
    elif damage=="labels":world.row["Config"]["Labels"]=None
    else:world.failure=True
    with pytest.raises(ValueError,match="^CAPTURE_OWNER_UNVERIFIED$") as caught:
        engine_identity(world,target,env,"EXAMPLE-engine",IMAGE,5)
    assert world.calls==1 and CANARY not in str(caught.value)


@pytest.mark.parametrize("change",["restart","replacement","image"])
def test_independent_before_after_identity_exposes_generation_change(change):
    env,target=inputs();world=World()
    before=engine_identity(world,target,env,"EXAMPLE-engine",IMAGE,5)
    if change=="restart":world.row["State"]["StartedAt"]="2026-10-28T00:00:01Z"
    elif change=="replacement":world.row["Id"]="d"*64
    else:world.row["Image"]="sha256:"+"d"*64
    after=engine_identity(world,target,env,"EXAMPLE-engine",IMAGE,5)
    assert before!=after and world.calls==2
    assert CANARY not in json.dumps([before,after])


def test_lifecycle_keeps_original_invalid_input_diagnostic(case):
    target=deepcopy(case.target);target["environment_ref"]["sha256"]="0"*64
    with pytest.raises(ValueError,match="^LIFECYCLE_CAPTURE_INPUT_INVALID$"):
        lifecycle.validate_session_binding(case.env,target,case.source,case.prepared)


def test_session_product_consumer_supplies_the_same_ENV_to_each_owner_observation(case,monkeypatch):
    seen=[];world=World();env,target=inputs()
    source={"container":"EXAMPLE-engine","image":IMAGE,"path":case.source["path"]}
    def fake_capture(client,topology,*,container,image,prepared,owner_identity):
        assert client is world and topology is target and prepared is case.prepared
        before=owner_identity(client,topology,container,image,5)
        after=owner_identity(client,topology,container,image,5)
        seen.extend([before,after])
        return {"security":"UNKNOWN","producer_authenticated":False,"recordings":[]}
    monkeypatch.setattr("guard.executors.lifecycle_session.capture_session",fake_capture)
    value=lifecycle.collect_session(world,env,target,source["container"],source["image"],case.prepared)
    assert len(seen)==2 and seen[0]==seen[1] and world.calls==2
    assert value["security"]=="UNKNOWN" and value["data_origin"]=="EXAMPLE"
    assert value["environment_ref"]==env.ref().model_dump(mode="json") and not value["producer_authenticated"]
    assert CANARY not in json.dumps(value)

