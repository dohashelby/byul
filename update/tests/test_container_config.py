"""PLT-01 container_config: 가짜 docker(inspect·exec)와 가짜 probe로 select→run→judge. Docker 없음."""

import json

import pytest

from guard.core.judge import judge_plan
from guard.core.run import RunConfig, run_plan
from guard.core.select import PolicyValueError, select
from guard.executors import registry
from guard.executors.container_config import ContainerConfigExecutor, decode_caps, parse_proc_status
from guard.executors.fake import DEFAULT_CAPS, SCENARIOS_BY_CHECK
from guard.models import ExecStatus, Verdict
from guard.runtime.docker import CmdResult
from guard.store import Store
from tests.builders import baseline_pack, environment, policy, completion_response

LAB_POLICY = {"allowed_capabilities": [], "allowed_mounts": ["/models:ro"], "allowed_devices": [], "require_non_root": True,
              "allowed_exec_writable_paths": ["/guard-cache"]}
DEFAULT_HEX = "00000000a80425fb"  # Docker 기본 capability 14개


def _hex(names):
    from guard.executors.container_config import CAP_NAMES

    return format(sum(1 << CAP_NAMES.index(n) for n in names), "016x")


class FakeDocker:
    """inspect_json·exec만 흉내. 프로필: hardened / default / overprivileged."""

    def __init__(self, profile: str, child_caps_hex: str | None = None):
        self.profile = profile
        self.child_caps_hex = child_caps_hex
        self.calls = []
        self.files = set()

    def inspect_json(self, container, *, timeout):
        self.calls.append(("inspect", container))
        mounts = [{"Type": "bind", "Destination": "/models", "RW": False, "Source": "/Users/x/guard-models"}]
        hc = {"Privileged": False, "CapAdd": None, "CapDrop": None, "Devices": [], "PidMode": "", "IpcMode": "private",
              "NetworkMode": "guard-net", "UTSMode": "", "ReadonlyRootfs": False, "SecurityOpt": None, "Tmpfs": None}
        cfg = {"User": "", "Image": "vllm/vllm-openai-cpu@sha256:x", "Env": ["SECRET_TOKEN=do-not-record"]}
        if self.profile in ("hardened", "tmp_exec"):
            hc.update(CapDrop=["ALL"], ReadonlyRootfs=True, SecurityOpt=["no-new-privileges:true"], Tmpfs={"/tmp": ""})
            cfg["User"] = "1000:1000"
        if self.profile == "overprivileged":
            hc.update(CapAdd=["SYS_ADMIN"])
            mounts.append({"Type": "bind", "Destination": "/guard-sentinel", "RW": True, "Source": "/x/sentinel"})
        return {"HostConfig": hc, "Mounts": mounts, "Config": cfg}

    def _caps(self):
        if self.profile in ("hardened", "tmp_exec"):
            return "0000000000000000"
        if self.profile == "overprivileged":
            return _hex(DEFAULT_CAPS + ["SYS_ADMIN"])
        return DEFAULT_HEX

    def _mounts(self):
        lines = ["proc /proc proc rw,nosuid,nodev,noexec,relatime 0 0",
                 "tmpfs /dev tmpfs rw,nosuid,size=65536k,mode=755 0 0",
                 "shm /dev/shm tmpfs rw,nosuid,nodev,noexec,relatime,size=1048576k 0 0",
                 "grpcfuse /models fakeowner ro,relatime 0 0",
                 "/dev/vda1 /etc/hosts ext4 rw,relatime 0 0"]
        rootfs_ro = self.profile in ("hardened", "tmp_exec")
        lines.insert(0, f"overlay / overlay {'ro' if rootfs_ro else 'rw'},relatime 0 0")
        if self.profile == "hardened":
            lines += ["tmpfs /tmp tmpfs rw,nosuid,nodev,noexec,relatime 0 0", "tmpfs /guard-cache tmpfs rw,nosuid,nodev,relatime 0 0"]
        if self.profile == "tmp_exec":
            lines += ["tmpfs /tmp tmpfs rw,nosuid,nodev,relatime 0 0"]
        if self.profile == "overprivileged":
            lines += ["grpcfuse /guard-sentinel fakeowner rw,relatime 0 0"]
        return "\n".join(lines) + "\n"

    def exec(self, container, argv, *, timeout):
        self.calls.append(("exec", container, argv[2][:20]))
        uid = 1000 if self.profile in ("hardened", "tmp_exec") else 0
        if argv[2] == "cat /proc/1/mounts":
            return CmdResult(0, self._mounts(), "")
        if "/proc/[0-9]*" in argv[2]:
            pid1 = f"PID 1\nName:\tvllm\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\nCapEff:\t{self._caps()}\n"
            child = f"PID 57\nName:\tpython3\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\nCapEff:\t{self.child_caps_hex or self._caps()}\n"
            return CmdResult(0, pid1 + child, "")
        path = argv[4]
        if path == "/guard-sentinel" and self.profile != "overprivileged":
            return CmdResult(0, "absent\n", "")
        if path == "/etc/hosts":
            return CmdResult(0, "absent\n", "")  # 파일 마운트(디렉토리 아님)
        if path == "/models":
            return CmdResult(0, "denied\n", "")
        if path in ("/", "/dev") and uid != 0 and self.profile in ("hardened", "tmp_exec"):
            return CmdResult(0, "denied\n", "")
        return CmdResult(0, "succeeded\n", "")


def _probe(network, requests, timeout):
    return [{"id": r["id"], "method": r["method"], "url": r["url"], **completion_response(r), "phase": "response"} for r in requests]


def _target(tmp_path, container="guard-lab-engine"):
    t = {"lab": True, "probe_image": "x", "origins": {"external": {"network": "ext"}, "trusted": {"network": "trusted"}},
         "gateway": {"url": "http://g:8080"}, "engine": {"trusted_url": "http://e:8000", "container": container},
         "direct_targets": [], "gateway_known_routes": []}
    p = tmp_path / "target.json"
    p.write_text(json.dumps(t), encoding="utf-8")
    return p


def _run(tmp_path, docker, values=None, cfg_extra=None, *, probe=_probe):
    cfg = {"target": str(_target(tmp_path)), **(cfg_extra or {})}
    registry.EXECUTORS["container_config:sim"] = lambda raw: ContainerConfigExecutor(cfg, container_probe=probe, docker=docker)
    try:
        store = Store(tmp_path / ".guard")
        pack = baseline_pack()
        env = environment({}, engine={"name": "vllm", "version": "0.31.0"}, model={"id": "m"})
        pol = policy(pack.ref(), scope={"include": ["PLT-01"], "exclude": []}, values={"PLT-01": values or LAB_POLICY})
        store.put(env); store.put(pol)
        plan = select(pack, [env], pol); store.put(plan)
        (evd,) = run_plan(store, pack, plan, RunConfig(executors={"container_config": "sim"}, adapter="vllm"))
        (res,) = judge_plan(store, pack, plan, as_of=env.created_at)
        return store, evd, res
    finally:
        del registry.EXECUTORS["container_config:sim"]


def _summary(store, evd):
    return json.loads((store.root / evd.artifacts[0].path).read_text(encoding="utf-8").strip().splitlines()[-1])


def test_decode_caps():
    assert decode_caps("0000000000000000") == []
    assert decode_caps(DEFAULT_HEX) == sorted(DEFAULT_CAPS)
    assert "SYS_ADMIN" in decode_caps(_hex(["SYS_ADMIN"]))


def test_parse_proc_status_reads_every_process():
    text = "PID 1\nName:\ta\nUid:\t0\t0\t0\t0\nCapEff:\t0000000000000000\nPID 9\nName:\tb\nUid:\t5\t7\t7\t7\nCapEff:\t0000000000000001\n"
    procs = parse_proc_status(text)
    assert [(p["pid"], p["uid_effective"], p["caps"]) for p in procs] == [(1, 0, []), (9, 7, ["CHOWN"])]


def test_hardened_is_pass(tmp_path):
    store, evd, res = _run(tmp_path, FakeDocker("hardened"))
    assert evd.execution.status == ExecStatus.COMPLETED
    assert res.verdicts.security == Verdict.PASS, res.reasons
    assert res.verdicts.functional == Verdict.PASS
    s = _summary(store, evd)
    assert s["container_fs_writes"] == {"/": "denied"}  # 컨테이너 자체 파일시스템은 증거에만
    assert evd.observations["sentinel_results"].value == {"/models": "denied", "/guard-sentinel": "absent"}


def test_hardened_passes_only_because_cache_path_is_allowed(tmp_path):
    """hardened는 정책 허용 목록에 /guard-cache가 있어서만 PASS. 목록에서 빼면 FAIL."""
    (tmp_path / "a").mkdir()
    _, evd, res = _run(tmp_path / "a", FakeDocker("hardened"))
    assert evd.observations["exec_writable_mounts"].value == ["/guard-cache"]
    assert res.verdicts.security == Verdict.PASS
    (tmp_path / "b").mkdir()
    _, _, res2 = _run(tmp_path / "b", FakeDocker("hardened"), values={**LAB_POLICY, "allowed_exec_writable_paths": []})
    assert res2.verdicts.security == Verdict.FAIL


def test_tmp_exec_is_fail(tmp_path):
    """반대 방향: /tmp에 exec를 허용한 구성(허용 목록에 없음)은 FAIL."""
    _, evd, res = _run(tmp_path, FakeDocker("tmp_exec"))
    assert evd.observations["exec_writable_mounts"].value == ["/tmp"]
    assert evd.observations["capabilities_effective"].value == []  # 다른 조건은 hardened와 같음
    assert res.verdicts.security == Verdict.FAIL


def test_exec_candidates_skip_noexec_ro_and_pseudo():
    from guard.executors.container_config import exec_candidates

    text = FakeDocker("hardened")._mounts()
    assert [c["target"] for c in exec_candidates(text)] == ["/dev", "/etc/hosts", "/guard-cache"]


def test_overprivileged_is_fail(tmp_path):
    _, evd, res = _run(tmp_path, FakeDocker("overprivileged"))
    assert res.verdicts.security == Verdict.FAIL
    assert "SYS_ADMIN" in evd.observations["capabilities_effective"].value
    assert "/guard-sentinel:rw" in evd.observations["mounts"].value
    assert evd.observations["sentinel_results"].value["/guard-sentinel"] == "succeeded"


def test_default_profile_is_fail_by_allowlist(tmp_path):
    _, evd, res = _run(tmp_path, FakeDocker("default"))
    assert res.verdicts.security == Verdict.FAIL  # 기본 capability 14개 ⊄ 허용 목록 []
    assert evd.observations["root_uid_present"].value is True


def test_child_process_caps_are_included(tmp_path):
    """PID 1에는 capability가 없어도 자식 프로세스에 있으면 합집합에 들어간다."""
    docker = FakeDocker("hardened", child_caps_hex=_hex(["NET_RAW"]))
    _, evd, res = _run(tmp_path, docker)
    assert evd.observations["capabilities_effective"].value == ["NET_RAW"]
    assert res.verdicts.security == Verdict.FAIL


def test_policy_allowlist_changes_the_verdict(tmp_path):
    """정책 값(허용 목록)을 바꾸면 같은 구성의 판정이 바뀐다 (value_policy·subset_of·when_policy 소비)."""
    lenient = {"allowed_capabilities": DEFAULT_CAPS, "allowed_mounts": ["/models:ro"], "allowed_devices": [],
               "require_non_root": False, "allowed_exec_writable_paths": ["/", "/dev"]}
    (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir(); (tmp_path / "c").mkdir()
    assert _run(tmp_path / "a", FakeDocker("default"))[2].verdicts.security == Verdict.FAIL
    assert _run(tmp_path / "b", FakeDocker("default"), values=lenient)[2].verdicts.security == Verdict.PASS
    # require_non_root만 true로 → root 프로세스 때문에 FAIL
    assert _run(tmp_path / "c", FakeDocker("default"), values={**lenient, "require_non_root": True})[2].verdicts.security \
        == Verdict.FAIL


def test_policy_type_is_validated_before_running(tmp_path):
    with pytest.raises(PolicyValueError, match="PLT-01.require_non_root: expected bool"):
        _run(tmp_path, FakeDocker("hardened"), values={**LAB_POLICY, "require_non_root": "yes"})


def test_container_name_from_config_and_env_not_recorded(tmp_path):
    docker = FakeDocker("hardened")
    store, evd, _ = _run(tmp_path, docker, cfg_extra={"container": "prod-engine-1"})
    assert {c[1] for c in docker.calls} == {"prod-engine-1"}
    blob = "".join(p.read_bytes().decode("utf-8", "replace") for p in store.root.rglob("*") if p.is_file())
    assert "do-not-record" not in blob


def test_only_read_only_commands_and_sentinel_cleanup(tmp_path):
    """exec는 /proc 읽기와 sentinel(쓰기 성공 시 즉시 rm)뿐."""
    from guard.executors.container_config import PROC_CMD, SENTINEL_CMD

    assert "rm -f" in SENTINEL_CMD and ">" not in PROC_CMD.replace("2>/dev/null", "")
    docker = FakeDocker("hardened")
    _run(tmp_path, docker)
    assert all(c[0] in ("inspect", "exec") for c in docker.calls)


@pytest.mark.parametrize("scenario, expected", [("pass", Verdict.PASS), ("overprivileged", Verdict.FAIL),
                                                ("default_caps", Verdict.FAIL), ("root_user", Verdict.FAIL),
                                                ("tmp_exec", Verdict.FAIL)])
def test_fake_scenarios(tmp_path, scenario, expected):
    assert set(SCENARIOS_BY_CHECK["PLT-01"]) == {"pass", "overprivileged", "default_caps", "root_user", "tmp_exec"}
    store = Store(tmp_path / ".guard")
    pack = baseline_pack()
    env = environment({})
    pol = policy(pack.ref(), scope={"include": ["PLT-01"], "exclude": []}, values={"PLT-01": LAB_POLICY})
    store.put(env); store.put(pol)
    plan = select(pack, [env], pol); store.put(plan)
    run_plan(store, pack, plan, RunConfig(executors={"container_config": "fake"}, raw={"fake": {"scenarios": {"PLT-01": scenario}}}))
    (res,) = judge_plan(store, pack, plan, as_of=env.created_at)
    assert res.verdicts.security == expected


def test_report_shows_check_limitation(tmp_path):
    from guard.core.report import build_report

    store, evd, res = _run(tmp_path, FakeDocker("hardened"))
    pack = baseline_pack()
    plan = store.resolve(evd.plan)
    rpt = build_report(store, pack, plan, as_of=res.created_at)
    assert any(l.startswith("PLT-01: 이 점검은 컨테이너 설정") and "Docker Desktop의 리눅스 VM 기준" in l for l in rpt.limitations)
    assert any(l.startswith('PLT-01: 실행 가능한 쓰기 경로 1개가 정책으로 허용됨 ["/guard-cache"]') for l in rpt.limitations)


class PartialDocker(FakeDocker):
    """EXAMPLE source failures; no Docker daemon or actual engine is used."""

    def __init__(self, profile="hardened", *, fault=None, proc_text=None, inspect_change=None, mount_text=None):
        super().__init__(profile)
        self.fault = fault
        self.proc_text = proc_text
        self.inspect_change = inspect_change
        self.mount_text = mount_text

    def inspect_json(self, container, *, timeout):
        if self.fault in {"inspect", "all"}:
            raise OSError("NEVER_PERSIST_INSPECT_SECRET")
        result = super().inspect_json(container, timeout=timeout)
        if self.inspect_change:
            self.inspect_change(result)
        return result

    def exec(self, container, argv, *, timeout):
        from guard.executors.container_config import MOUNTS_CMD, PROC_CMD, SENTINEL_CMD

        command = argv[2]
        stage = "proc" if command == PROC_CMD else "mount" if command == MOUNTS_CMD else "sentinel"
        if self.fault in {stage, "all"} or command == SENTINEL_CMD and self.fault == argv[4]:
            raise OSError("NEVER_PERSIST_EXEC_SECRET")
        if command == PROC_CMD and self.proc_text is not None:
            return CmdResult(0, self.proc_text, "NEVER_PERSIST_PROC_STDERR")
        if command == MOUNTS_CMD and self.mount_text is not None:
            return CmdResult(0, self.mount_text, "NEVER_PERSIST_MOUNT_STDERR")
        return super().exec(container, argv, timeout=timeout)


def _failed_probe(*args):
    raise OSError("NEVER_PERSIST_FUNCTIONAL_SECRET")


@pytest.mark.parametrize("violation,later_fault", [
    ("privileged", "proc"), ("privileged", "sentinel"), ("privileged", "mount"), ("privileged", "functional"),
    ("root", "sentinel"), ("root", "mount"), ("root", "functional"),
    ("capability", "sentinel"), ("capability", "mount"), ("capability", "functional"),
])
def test_independent_permission_violation_survives_later_source_error(tmp_path, violation, later_fault):
    proc_text = {"root": "PID 1\nUid:\t0\t0\t0\t0\nCapEff:\t0\n",
                 "capability": "PID 1\nUid:\t1000\t1000\t1000\t1000\nCapEff:\t00200000\n"}.get(violation)
    change = (lambda ins: ins["HostConfig"].update(Privileged=True)) if violation == "privileged" else None
    docker = PartialDocker(fault=None if later_fault == "functional" else later_fault,
                           proc_text=proc_text, inspect_change=change)
    store, evd, result = _run(tmp_path, docker, probe=_failed_probe if later_fault == "functional" else _probe)
    assert evd.execution.status == ExecStatus.COMPLETED
    assert evd.data_origin.value == "EXAMPLE"
    assert result.verdicts.security == Verdict.FAIL, result.reasons
    assert evd.observations["permission_scan_complete"].value is (later_fault == "functional")
    if later_fault == "functional":
        assert result.verdicts.functional == Verdict.UNKNOWN
    summary = _summary(store, evd)
    assert summary["collection_errors"]
    assert "NEVER_PERSIST" not in json.dumps(summary)
    assert store.verify() == []


@pytest.mark.parametrize("profile,expected", [("tmp_exec", Verdict.FAIL), ("hardened", Verdict.UNKNOWN)])
def test_partial_exec_write_keeps_violation_but_cannot_establish_allowed_subset_pass(tmp_path, profile, expected):
    _, evd, result = _run(tmp_path, PartialDocker(profile, fault="/dev"))
    assert evd.execution.status == ExecStatus.COMPLETED
    assert evd.observations["permission_scan_complete"].value is False
    assert evd.observations["exec_writable_mounts"].collected is True
    assert evd.observations["exec_writable_mounts"].value == ["/tmp" if profile == "tmp_exec" else "/guard-cache"]
    assert result.verdicts.security == expected, result.reasons


def test_partial_host_sentinel_write_retains_confirmed_success(tmp_path):
    class WritableHostSentinelDocker(PartialDocker):
        def exec(self, container, argv, *, timeout):
            from guard.executors.container_config import SENTINEL_CMD

            if argv[2] == SENTINEL_CMD and argv[4] == "/guard-sentinel":
                return CmdResult(0, "succeeded\n", "")
            return super().exec(container, argv, timeout=timeout)

    def add_mounts(ins):
        ins["Mounts"] += [{"Type": "bind", "Destination": "/guard-sentinel", "RW": True},
                          {"Type": "bind", "Destination": "/later", "RW": False}]
    values = {**LAB_POLICY, "allowed_mounts": ["/models:ro", "/guard-sentinel:rw", "/later:ro"]}
    _, evd, result = _run(tmp_path, WritableHostSentinelDocker(fault="/later", inspect_change=add_mounts), values=values,
                          cfg_extra={"sentinel_paths": ["/guard-sentinel", "/later"]})
    assert evd.observations["sentinel_results"].collected is True
    assert evd.observations["sentinel_results"].value == {"/guard-sentinel": "succeeded", "/later": "error"}
    assert evd.observations["permission_scan_complete"].value is False
    assert result.verdicts.security == Verdict.FAIL


@pytest.mark.parametrize("field", ["HostConfig", "Mounts"])
def test_missing_inspect_fields_are_unknown_instead_of_empty_safe_inventory(tmp_path, field):
    _, evd, result = _run(tmp_path, PartialDocker(inspect_change=lambda ins: ins.pop(field)))
    assert evd.execution.status == ExecStatus.COMPLETED
    assert evd.observations["permission_scan_complete"].value is False
    assert result.verdicts.security == Verdict.UNKNOWN
    if field == "HostConfig":
        assert evd.observations["privileged"].collected is False
        assert evd.observations["privileged"].value is None
    else:
        assert evd.observations["mounts"].collected is False


@pytest.mark.parametrize("change", [
    lambda ins: ins["HostConfig"].pop("Privileged"),
    lambda ins: ins["HostConfig"].update(Privileged="false"),
    lambda ins: ins["HostConfig"].pop("Devices"),
    lambda ins: ins["HostConfig"].update(Devices={}),
    lambda ins: ins["HostConfig"].pop("PidMode"),
    lambda ins: ins.update(Mounts={}),
    lambda ins: ins["Mounts"][0].update(Type=""),
    lambda ins: ins["Mounts"][0].update(Type="unreviewed-kind"),
])
def test_malformed_permission_inventory_does_not_claim_completeness(tmp_path, change):
    _, evd, result = _run(tmp_path, PartialDocker(inspect_change=change))
    assert evd.observations["permission_scan_complete"].value is False
    assert result.verdicts.security == Verdict.UNKNOWN


def test_valid_mount_violation_survives_another_malformed_mount(tmp_path):
    def partial(ins):
        ins["Mounts"] += [{"Type": "bind", "Destination": "/forbidden", "RW": True}, None]
    _, evd, result = _run(tmp_path, PartialDocker(inspect_change=partial))
    assert evd.observations["permission_scan_complete"].value is False
    assert "/forbidden:rw" in evd.observations["mounts"].value
    assert result.verdicts.security == Verdict.FAIL


@pytest.mark.parametrize("field,value", [("Tmpfs", ["unexpected"]), ("CapAdd", {"unexpected": "NEVER_PERSIST_CONFIG_SECRET"}),
                                        ("SecurityOpt", True)])
def test_optional_diagnostic_shape_cannot_erase_privileged_violation(tmp_path, field, value):
    def partial(ins):
        ins["HostConfig"].update(Privileged=True, **{field: value})
    store, evd, result = _run(tmp_path, PartialDocker(inspect_change=partial))
    assert evd.execution.status == ExecStatus.COMPLETED
    assert result.verdicts.security == Verdict.FAIL
    assert "NEVER_PERSIST" not in json.dumps(_summary(store, evd))


@pytest.mark.parametrize("fault", ["inspect", "proc", "sentinel", "mount", "all"])
def test_permission_source_failure_without_violation_is_unknown(tmp_path, fault):
    store, evd, result = _run(tmp_path, PartialDocker(fault=fault))
    assert evd.execution.status == ExecStatus.COMPLETED
    assert evd.observations["permission_scan_complete"].value is False
    assert result.verdicts.security == Verdict.UNKNOWN
    assert "NEVER_PERSIST" not in json.dumps(_summary(store, evd))


@pytest.mark.parametrize("header", ["PID broken", "PID -1", "PID 0", "PID\t9", "PID", "PID " + "9" * 30])
def test_malformed_pid_does_not_attribute_following_fields_to_previous_process(header):
    rows = parse_proc_status("PID 1\nUid:\t1000\t1000\t1000\t1000\nCapEff:\t0\n"
                             + header + "\nUid:\t0\t0\t0\t0\nCapEff:\t00200000\n")
    assert rows[0]["uid_effective"] == 1000 and rows[0]["caps"] == []
    assert rows[1] == {"read_error": True}


@pytest.mark.parametrize("fields", ["Uid:\t-1\t-1\t-1\t-1\nCapEff:\t0\n",
                                   "Uid:\t0\t0\nCapEff:\t0\n",
                                   "Uid:\t1000\t1000\t1000\t1000\nCapEff:\t-1\n",
                                   "Uid:\t1000\t1000\t1000\t1000\nCapEff:\t10000000000000000\n"])
def test_invalid_uid_or_caps_cannot_create_phantom_permission_values(tmp_path, fields):
    _, evd, result = _run(tmp_path, PartialDocker(proc_text="PID 1\n" + fields))
    assert evd.observations["process_scan_complete"].value is False
    assert evd.observations["root_uid_present"].collected is False
    assert result.verdicts.security == Verdict.UNKNOWN


def test_known_root_survives_malformed_later_process(tmp_path):
    _, evd, result = _run(tmp_path, PartialDocker(proc_text=
        "PID 1\nUid:\t0\t0\t0\t0\nCapEff:\t0\nPID broken\nUid:\t1000\t1000\t1000\t1000\nCapEff:\t0\n"))
    assert evd.observations["process_scan_complete"].value is False
    assert evd.observations["root_uid_present"].value is True
    assert result.verdicts.security == Verdict.FAIL


def test_duplicate_process_header_keeps_scan_incomplete(tmp_path):
    _, evd, result = _run(tmp_path, PartialDocker(proc_text=
        ("PID 1\nUid:\t1000\t1000\t1000\t1000\nCapEff:\t0\n") * 2))
    assert evd.observations["process_scan_complete"].value is False
    assert result.verdicts.security == Verdict.UNKNOWN


def test_saved_partial_permission_evidence_remains_verifiable_after_restore(tmp_path):
    from guard.models import canonical_json
    from guard.store.bundle import export_bundle, restore_bundle
    from guard.store.packs import get_pack, put_pack

    store, evd, result = _run(tmp_path, PartialDocker("tmp_exec", fault="/dev"))
    pack = baseline_pack()
    put_pack(store, pack.path)
    original = canonical_json(evd.to_dict())
    export_bundle(store.root, tmp_path / "partial.zip")
    restore_bundle(tmp_path / "partial.zip", tmp_path / "restored")
    restored = Store(tmp_path / "restored")
    assert restored.verify() == []
    assert canonical_json(restored.resolve(evd.ref()).to_dict()) == original
    replayed, = judge_plan(restored, get_pack(restored, pack.ref()), restored.resolve(evd.plan), as_of=evd.created_at)
    assert replayed.ref() == result.ref() and replayed.verdicts.security == Verdict.FAIL
