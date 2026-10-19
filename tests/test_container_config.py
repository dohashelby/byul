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
from tests.builders import baseline_pack, environment, policy

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
    return [{"id": r["id"], "method": r["method"], "url": r["url"], "outcome": 200, "phase": "response"} for r in requests]


def _target(tmp_path, container="guard-lab-engine"):
    t = {"lab": False, "probe_image": "x", "origins": {"external": {"network": "ext"}, "trusted": {"network": "trusted"}},
         "gateway": {"url": "http://g:8080"}, "engine": {"trusted_url": "http://e:8000", "container": container},
         "direct_targets": [], "gateway_known_routes": []}
    p = tmp_path / "target.json"
    p.write_text(json.dumps(t), encoding="utf-8")
    return p


def _run(tmp_path, docker, values=None, cfg_extra=None):
    cfg = {"target": str(_target(tmp_path)), **(cfg_extra or {})}
    registry.EXECUTORS["container_config:sim"] = lambda raw: ContainerConfigExecutor(cfg, container_probe=_probe, docker=docker)
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
