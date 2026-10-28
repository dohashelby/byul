"""EXAMPLE supplied-template rendering witness; no engine or Docker startup."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from guard.adapters.vllm import (VllmAdapter, fixed_chat_template, TYPE_TEMPLATE_PROFILE, TYPE_TEMPLATE_PATH,
                                 TYPE_TEMPLATE_SHA256, TYPE_TEMPLATE_ASSET, CHAT_REVIEWED_IMAGE)
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.core.judge import judge_plan
from guard.executors import template_sandbox as sandbox
from guard.executors.template_validation import TemplateValidationExecutor
from guard.judges.rule_table import RuleTableJudge
from guard.models import CheckDef, DataOrigin, Verdict
from guard.store import Store
from tests.builders import baseline_pack, environment, evidence, policy, T0

ROOT = Path(__file__).resolve().parents[1]
VALUES = {sandbox.POLICY: ["__class__"]}
PRIVATE = "EXAMPLE_PRIVATE_RENDER_BODY_AND_EXCEPTION"
CID, LOCAL, NET, EP = "a" * 64, "sha256:" + "b" * 64, "c" * 64, "d" * 64
IMAGE = CHAT_REVIEWED_IMAGE


def definition():
    return CheckDef.model_validate(yaml.safe_load((ROOT / "packs/PACK-BASELINE/checks/SUP-13.yaml").read_text(encoding="utf-8")))


class World:
    def __init__(self, mode="reference"):
        self.mode, self.calls, self.after, self.inspects = mode, [], False, 0
        _mount, metadata = fixed_chat_template(TYPE_TEMPLATE_PROFILE)
        self.target = {"lab": True, "run_id": "EXAMPLE-run", "observed_version": "0.31.0",
            "origins": {"trusted": {"network": "EXAMPLE-trusted"}}, "engine": {"container": "EXAMPLE-engine",
            "image_digest": CHAT_REVIEWED_IMAGE, "trusted_url": "http://172.18.0.2:8000"},
            "comparison_config": {"launch": {"argv": ["--model", "/models", "--chat-template", TYPE_TEMPLATE_PATH],
                "model": "fixture-model", "max_model_len": 128, "chat_template": metadata}}}

    def inspect_json(self, container, timeout):
        self.inspects += 1
        return {"Id": CID, "Name": "/EXAMPLE-engine", "Image": LOCAL,
            "Config": {"Image": CHAT_REVIEWED_IMAGE, "Cmd": self.target["comparison_config"]["launch"]["argv"],
                       "Entrypoint": ["other"] if self.mode == "entrypoint" else ["EXAMPLE-native"],
                       "Labels": {"guard.run": "other" if self.mode == "owner" else "EXAMPLE-run"}, "Env": [PRIVATE]},
            "State": {"Running": True, "StartedAt": "2026-10-26T01:00:00Z" if self.after else "2026-10-26T00:00:00Z"},
            "Mounts": [{"Destination": TYPE_TEMPLATE_PATH, "Type": "bind", "RW": self.mode == "writable", "Source": PRIVATE}],
            "NetworkSettings": {"Networks": {"EXAMPLE-trusted": {"NetworkID": NET, "EndpointID": EP,
                "IPAddress": "172.18.0.2", "GlobalIPv6Address": ""}}}}

    def exec(self, container, argv, timeout):
        assert argv[-1] == TYPE_TEMPLATE_PATH
        return SimpleNamespace(returncode=0, stdout=json.dumps({"sha256": "0" * 64 if self.mode == "file" else TYPE_TEMPLATE_SHA256,
            "bytes": TYPE_TEMPLATE_ASSET.stat().st_size}))

    def image_info(self, image, timeout):
        assert image == LOCAL and timeout > 0
        return {"os": "linux", "digests": [] if self.mode == "altered_image" else [CHAT_REVIEWED_IMAGE],
                "entrypoint": ["EXAMPLE-native"]}

    def network_info(self, network, timeout):
        return {"Id": NET, "Name": "EXAMPLE-trusted", "Driver": "bridge", "Scope": "local",
            "Labels": {"guard.run": "EXAMPLE-run"}, "Containers": {CID: {"Name": "EXAMPLE-engine",
            "EndpointID": EP, "IPv4Address": "172.18.0.3/16" if self.mode == "endpoint" else "172.18.0.2/16", "IPv6Address": ""}}}

    def probe(self, network, requests, timeout):
        request, = requests
        assert network == "EXAMPLE-trusted" and request["url"].startswith("http://172.18.0.2:8000/")
        assert 0 < timeout <= 1
        self.calls.append(request)
        rid = request["id"]
        if self.mode == "raise": raise RuntimeError(PRIVATE)
        outcome = 400 if rid == "type-boundary-render" and self.mode == "blocked" else 200
        normal = [11, 12, 13]
        tokens = [21, 22] if rid in {"type-reference-result", "type-boundary-render"} else normal
        if self.mode == "identical_refs" or self.mode == "ignored" and rid == "type-boundary-render": tokens = normal
        if self.mode == "bool_tokens" and rid == "type-boundary-render": tokens = [True, True]
        if self.mode == "bad_count": outcome = 200
        if rid in {"type-before", "type-after"}:
            value = {"model": "other" if self.mode == "model" else "fixture-model", "prompt_token_ids": normal,
                "choices": [{"message": {"role": "assistant", "content": PRIVATE}, "finish_reason": "length"}],
                "usage": {"completion_tokens": 1}}
        else:
            value = {"tokens": tokens, "count": len(tokens) + (1 if self.mode == "bad_count" else 0), "max_model_len": 128}
        if self.mode == "late_change" and rid == "type-after" or self.mode == "change_during_boundary" and rid == "type-boundary-render": self.after = True
        return [{"id": "other" if self.mode == "wrong_id" else rid, "outcome": outcome,
                 "body": json.dumps(value), "body_preview": PRIVATE, "detail": PRIVATE}]


def run(tmp_path, world=None, values=None, mutate=None):
    world = world or World()
    data = deepcopy(world.target)
    if mutate: mutate(data)
    path = tmp_path / "target.json"; path.write_text(json.dumps(data), encoding="utf-8")
    env = environment(engine={"name": "vllm", "version": "0.31.0", "image_digest": CHAT_REVIEWED_IMAGE}, model={"id": "fixture-model"})
    executor = TemplateValidationExecutor({"target": str(path), "request_timeout": 1}, docker=world,
                                           container_probe=world.probe, environ={})
    draft = executor.run(definition(), env, deepcopy(VALUES if values is None else values), VllmAdapter(), tmp_path / "work")
    return draft, world


def judge(draft, values=None):
    check = definition()
    evd = evidence(check_id=check.id, normal=draft.normal_control.model_dump(),
                   observations={k:v.model_dump() for k,v in draft.observations.items()})
    return RuleTableJudge().judge(check, evd, check.judge.params, VALUES if values is None else values)


def test_forbidden_rendered_type_reference_is_one_independent_fail_without_raw_content(tmp_path):
    draft, world = run(tmp_path)
    assert len(world.calls) == 6
    assert draft.data_origin == DataOrigin.EXAMPLE and draft.normal_control.succeeded
    assert draft.observations["forbidden_template_type_reference_verified"].value is True
    assert draft.observations["template_sandbox_scope_complete"].value is False
    assert judge(draft).verdicts == {"security": Verdict.FAIL, "functional": Verdict.UNKNOWN, "performance": Verdict.UNKNOWN}
    raw = draft.artifacts[0].path.read_text(encoding="utf-8")
    assert PRIVATE not in raw and '"prompt_token_ids"' not in raw and '"tokens"' not in raw
    assert '"normal_rendered"' not in raw and '"reference_rendered"' not in raw


@pytest.mark.parametrize("mode", ["blocked", "ignored", "identical_refs", "wrong_id", "raise", "model", "bad_count", "bool_tokens",
    "owner", "writable", "file", "endpoint", "entrypoint", "altered_image", "change_during_boundary"])
def test_rejection_gaps_or_different_owner_do_not_create_pass_or_fail(tmp_path, mode):
    draft, _ = run(tmp_path, World(mode))
    assert set(judge(draft).verdicts.values()) == {Verdict.UNKNOWN}
    assert PRIVATE not in draft.artifacts[0].path.read_text(encoding="utf-8")


def test_later_normal_or_identity_gap_does_not_erase_verified_reference(tmp_path):
    draft, _ = run(tmp_path, World("late_change"))
    assert draft.normal_control.succeeded is False
    assert judge(draft).verdicts["security"] == Verdict.FAIL


def test_explicit_empty_policy_is_not_filled_with_a_default_forbidden_attribute(tmp_path):
    values = {sandbox.POLICY: []}
    draft, _ = run(tmp_path, values=values)
    assert judge(draft, values).verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("values", [{}, {sandbox.POLICY: None}, {sandbox.POLICY: "__class__"},
    {sandbox.POLICY: ["__class__", "__class__"]}, {sandbox.POLICY: ["__globals__"]}])
def test_invalid_policy_stops_before_inspection_or_http(tmp_path, values):
    world = World()
    with pytest.raises(ValueError, match="TEMPLATE_TYPE_POLICY_REQUIRED"): run(tmp_path, world, values)
    assert not world.calls and world.inspects == 0


@pytest.mark.parametrize("change", ["model", "profile", "endpoint", "image"])
def test_unsupported_target_is_unknown_without_dispatch(tmp_path, change):
    def mutate(target):
        if change == "model": target["comparison_config"]["launch"]["model"] = "other"
        elif change == "profile": target["comparison_config"]["launch"]["chat_template"]["profile"] = "other"
        elif change == "endpoint": target["engine"]["trusted_url"] = "http://unrelated:9000"
        else: target["engine"]["image_digest"] = "example/other@sha256:" + "f"*64
    draft, world = run(tmp_path, mutate=mutate)
    assert not world.calls and judge(draft).verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("url", ["http://unrelated:invalid", "http://unrelated:99999", "http://[invalid"])
def test_malformed_target_is_fixed_error_before_io(tmp_path, url):
    world = World()
    with pytest.raises(ValueError, match="^TEMPLATE_TYPE_TARGET_UNAVAILABLE$"):
        run(tmp_path, world, mutate=lambda target: target["engine"].update(trusted_url=url))
    assert world.inspects == 0 and not world.calls


def test_existing_template_profile_cannot_be_replaced_by_boundary_fixture():
    mount, metadata = fixed_chat_template(TYPE_TEMPLATE_PROFILE)
    assert mount.read_only and hashlib.sha256(TYPE_TEMPLATE_ASSET.read_bytes()).hexdigest() == TYPE_TEMPLATE_SHA256
    adapter = VllmAdapter()
    assert not adapter.supports_template_profile(metadata)
    assert adapter.template_type_boundary_contract(metadata, CHAT_REVIEWED_IMAGE, "0.31.0")
    assert adapter.template_type_boundary_contract(metadata, CHAT_REVIEWED_IMAGE, "other") is None


def test_product_plan_run_judge_preserves_the_independent_example_fail(tmp_path, monkeypatch):
    from guard.executors.registry import EXECUTORS
    world = World()
    path=tmp_path/'target.json'; path.write_text(json.dumps(world.target),encoding='utf-8')
    cfg={"target":str(path),"request_timeout":1}
    monkeypatch.setitem(EXECUTORS,"template_validation:real",lambda _: TemplateValidationExecutor(cfg,docker=world,container_probe=world.probe,environ={}))
    pack=baseline_pack(); env=environment(engine={"name":"vllm","version":"0.31.0","image_digest":CHAT_REVIEWED_IMAGE},model={"id":"fixture-model"})
    pol=policy(pack.ref(),scope={"include":["SUP-13"]},values={"SUP-13":VALUES})
    store=Store(tmp_path/'store'); store.put_many([env,pol]); plan=select(pack,[env],pol);store.put(plan)
    evd,=run_plan(store,pack,plan,RunConfig(executors={"template_validation":"real"},adapter="vllm",raw={"template_validation":cfg}))
    result,=judge_plan(store,pack,plan,as_of=T0)
    assert evd.data_origin==DataOrigin.EXAMPLE and result.verdicts.security==Verdict.FAIL
    assert store.verify()==[]


def test_new_http_lifecycle_bounds_keep_credentials_in_stdin_and_disable_pull():
    import time
    from guard.executors.http_common import docker_container_probe
    class Client:
        def run_once(self, image, suffix, **kwargs):
            assert 0 < kwargs["timeout"] <= .5 and kwargs["pull_never"] is True
            assert PRIVATE in kwargs["input"].decode() and PRIVATE not in str(kwargs["command"])
            return SimpleNamespace(returncode=0, stdout='[{"id":"r","outcome":200}]')
    probe=docker_container_probe(Client(),IMAGE,pull_never=True,parent_deadline=time.monotonic()+.5)
    assert probe('EXAMPLE-trusted',[{"id":"r","headers":{"Authorization":PRIVATE}}],1)[0]['outcome']==200


def test_expired_http_parent_never_starts_a_probe():
    from guard.executors.http_common import docker_container_probe
    class Client:
        def run_once(self,*args,**kwargs):raise AssertionError('must not run')
    with pytest.raises(Exception,match='HTTP probe parent deadline expired'):
        docker_container_probe(Client(),IMAGE,parent_deadline=0)('n',[{}],1)


@pytest.mark.parametrize("cfg", [{"max_total_requests": True}, {"max_total_requests": 7},
    {"max_output_tokens": 2}, {"max_duration_seconds": 0}, {"request_timeout": float("nan")},
    {"request_timeout": 31}, {"allow_mutating_routes": True}, {"unknown": 1}])
def test_invalid_technical_bounds_are_rejected_before_io(cfg):
    with pytest.raises(ValueError, match="TEMPLATE_TYPE_"):
        sandbox.validate_policy(definition(), VALUES, cfg)


def test_default_snapshot_path_still_rejects_the_new_fixture(tmp_path):
    import time
    from guard.executors.engine_identity import owned_template_snapshot
    world = World()
    ctx = SimpleNamespace(target=world.target, env=SimpleNamespace(engine=SimpleNamespace(image_digest=IMAGE)))
    launch = world.target["comparison_config"]["launch"]
    assert owned_template_snapshot(world,ctx,launch["chat_template"],launch["argv"],time.monotonic()+1) is None
    assert world.inspects == 0


def test_only_sup13_definition_changes_after_pack29(tmp_path):
    from tests.pack_lineage_support import historical_pack
    frozen = historical_pack("prior30", tmp_path / "prior30")
    previous = json.loads((ROOT/'tests/fixtures/pack29-check-hashes.json').read_text())
    current = {p.stem:hashlib.sha256(p.read_bytes()).hexdigest() for p in (frozen.path/'checks').glob('*.yaml')}
    assert len(previous) == len(current) == 77 and set(previous) == set(current)
    assert {key for key in current if current[key] != previous[key]} == {'SUP-13'}
    check = frozen.checks["SUP-13"]
    assert check.revision == 3 and check.executor == 'template_validation'
    assert not check.judge.params['pass_when_all']
    assert all(not rules['pass_when_all'] for rules in check.judge.params['axes'].values())
