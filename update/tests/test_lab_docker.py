"""로컬 Docker 실험실: 두 위상을 guard select/run/judge/report CLI 전체 경로로 실행.

    uv run pytest -m docker -s

exposed → REQ-01 FAIL, isolated → PASS. 끝나면 guard.run 라벨의 컨테이너·네트워크가 남지 않아야 한다.
PLT-07은 별도 소유 Python helper의 실제 송신·수신을 EXAMPLE FAIL로 연결한다.
실제 엔진·worker 송신 실증으로 집계하지 않는다.
키는 실행 중 임의 생성해 이 프로세스 환경에만 둔다(파일·로그에 남기지 않음).
"""

from datetime import datetime, timedelta, timezone
import ipaddress
import json
from pathlib import Path
import re
import secrets
import subprocess
import time

import pytest
import yaml
from typer.testing import CliRunner

from guard.cli import app
from guard.executors import egress_receiver
from guard.runtime.docker import Docker
from guard.store import Store
from lab import lab
from tests.builders import ROOT, baseline_pack, environment, policy

pytestmark = pytest.mark.docker
runner = CliRunner()


def _docker_ok() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _labelled(kind: str) -> list[str]:
    cmd = ["docker", "ps", "-aq"] if kind == "containers" else ["docker", "network", "ls", "-q"]
    out = subprocess.run([*cmd, "--filter", "label=guard.run"], capture_output=True, timeout=30, text=True).stdout
    return out.split()


def _ok(args, env):
    res = runner.invoke(app, args, env=env)
    assert res.exit_code == 0, res.output + repr(res.exception)
    return res.output


def _api_transport_cli(docker, run_id, network, pinned, run_dir, env):
    """Actual existing owned Python stub → PLT-08 CLI, always EXAMPLE."""
    name = docker.name("api-transport-source")
    source = ROOT / "lab/stub_engine/server.py"
    docker._docker(["run", "-d", "--name", name, "--label", docker.label,
        "--network", network, "--cgroupns=private", "--pull=never",
        "--mount", f"type=bind,src={source},dst=/api/server.py,readonly",
        pinned, "python", "-u", "/api/server.py"], timeout=30)
    lab._wait(lambda: "lab-stub listening" in docker.logs(name, timeout=5), "owned API transport source")
    raw = docker.inspect_json(name, timeout=5)
    endpoint = raw["NetworkSettings"]["Networks"][network]
    directory = run_dir / "api-transport"
    directory.mkdir()
    target = {"lab": True, "run_id": run_id, "probe_image": pinned,
        "engine": {"container": name, "image_digest": pinned, "trusted_url": f"http://{endpoint['IPAddress']}:8000"},
        "origins": {"trusted": {"network": network}}}
    target_path = directory / "target.json"
    target_path.write_text(json.dumps(target), encoding="utf-8")
    pack = baseline_pack()
    inputs = environment(engine={"name": "EXAMPLE-python-api", "image_digest": pinned}, model={"id": "lab-stub"})
    now = datetime.now(timezone.utc)
    pol = policy(pack.ref(), scope={"include": ["PLT-08"]}, values={"PLT-08": {
        "api_hop_scope": "guard_probe_to_owned_direct_api", "api_container_id": raw["Id"],
        "api_network_id": endpoint["NetworkID"], "api_endpoint_id": endpoint["EndpointID"],
        "api_ip": endpoint["IPAddress"], "api_port": 8000, "plaintext_forbidden": True,
        "validity_start": (now - timedelta(seconds=10)).isoformat(),
        "validity_end": (now + timedelta(seconds=120)).isoformat()}})
    ep, pp = directory / "env.yaml", directory / "policy.yaml"
    ep.write_text(yaml.safe_dump(inputs.to_dict()), encoding="utf-8")
    pp.write_text(yaml.safe_dump(pol.to_dict()), encoding="utf-8")
    store_path, cfg = directory / ".guard", directory / "guard.toml"
    cfg.write_text(f'store = "{store_path.as_posix()}"\n[executors]\napi_transport = "real"\n'
        f'[api_transport]\ntarget = "{target_path.as_posix()}"\n[api_transport.settings]\n'
        'schema = "guard-stream-pressure-settings/1"\nmax_duration_seconds = 30\n'
        'request_timeout_seconds = 3\nmax_resource_samples = 3\nresource_interval_ms = 10\n'
        'post_close_wait_ms = 0\n', encoding="utf-8")
    common = ["--config", str(cfg), "--pack", str(ROOT / "packs/PACK-BASELINE")]
    plan = _ok(["select", "--env", str(ep), "--policy", str(pp), *common], env).strip().splitlines()[-1]
    _ok(["run", "--plan", plan, *common], env)
    _ok(["judge", "--plan", plan, *common], env)
    report = directory / "report.json"
    _ok(["report", "--plan", plan, "--json", str(report), *common], env)
    data = json.loads(report.read_text(encoding="utf-8"))
    row, = [r for r in data["by_check"] if r["check_id"] == "PLT-08"]
    assert row["single"]["security"] == "FAIL"
    assert row["single"]["functional"] == row["single"]["performance"] == "UNKNOWN"
    assert row["single"]["verification_level"] == "EXAMPLE" and data["official"] is False
    store = Store(store_path)
    evd, = store.find_latest("EVD", lambda e: e.check_id == "PLT-08")
    assert evd.execution.status.value == "COMPLETED" and evd.data_origin.value == "EXAMPLE"
    assert evd.observations["forbidden_plaintext_successes"].value == ["owned_direct_api_plaintext"]
    assert evd.observations["api_transport_scope_complete"].value is False
    trial, = evd.observations["api_transport_trials"].value
    assert trial["binding"]["container_id"] == raw["Id"] and trial["normal_response_verified"] is True
    assert trial["transport"] == "plain_http" and trial["cryptographic_peer_identity_verified"] is False
    assert store.verify() == []


def _native_weight_hashes(docker, target, pinned):
    """Real Linux file helper in the existing owned EXAMPLE source; never vLLM."""
    import hashlib
    from guard.executors.weight_file_capture import DockerWeightReader
    seed = r'''
import os
from pathlib import Path
root=Path('/guard-weights');root.mkdir(mode=0o700)
(root/'a.bin').write_bytes(b'EXAMPLE-model-bytes')
(root/'link.bin').symlink_to(root/'a.bin')
(root/'escape.bin').symlink_to('/etc/hostname')
os.mkfifo(root/'fifo.bin')
'''
    value=docker.exec_bounded(target["engine"]["container"],["python3","-I","-S","-B","-c",seed],timeout=5,max_bytes=4096)
    assert value.returncode==0 and value.capture["complete"]
    reader=DockerWeightReader(docker,target,duration=30)
    for relative in ("a.bin","link.bin"):
        packet,owner,stable=reader.read(target["engine"]["container"],pinned,"/guard-weights",relative,1024)
        assert packet["content_sha256"]==hashlib.sha256(b'EXAMPLE-model-bytes').hexdigest()
        assert packet["file_identity_stable"] and packet["logical_binding_stable"] and stable
        assert packet["path_sha256"]==hashlib.sha256(("/guard-weights/"+relative).encode()).hexdigest()
        assert "EXAMPLE-model-bytes" not in json.dumps(packet)
    for relative,cap in (("escape.bin",1024),("fifo.bin",1024),("a.bin",1)):
        with pytest.raises(ValueError):reader.read(target["engine"]["container"],pinned,"/guard-weights",relative,cap)


def _lifecycle_session_inventory(docker, target, pinned):
    """Real Linux /proc/fd census in the existing EXAMPLE Python source only."""
    from guard.executors.lifecycle_session import inventory
    session = "1" * 32
    seed = r'''
from pathlib import Path
raw=Path('/proc/1/stat').read_text();start=int(raw[raw.rfind(')')+2:].split()[19])
root=Path('/guard-lifecycle/data')/('1'*32)
root.mkdir(parents=True,mode=0o700)
with (root/('pid-1-'+str(start)+'.jsonl')).open('xb') as stream:stream.write(b'EXAMPLE seed metadata only\n')
(root/('pid-1-'+str(start)+'.jsonl')).chmod(0o600)
with (root/'private.txt').open('xb') as stream:stream.write(b'EXAMPLE_CENSUS_PRIVATE_LIFECYCLE')
(root/'pid-2-99.jsonl').symlink_to(root/'private.txt')
with (root/'SEALED').open('xb'):pass
(root/'SEALED').chmod(0o600)
'''
    seeded = docker.exec_bounded(target["engine"]["container"], ["python3", "-c", seed], timeout=5, max_bytes=4096)
    assert seeded.returncode == 0 and seeded.capture["complete"]
    value = inventory(docker,target,container=target["engine"]["container"],image=pinned,session=session)
    assert value["owner_stable"] and value["metadata"]["directory_stable"] and value["metadata"]["sealed"]
    assert value["metadata"]["unknown_entries"] == 1
    assert len(value["metadata"]["files"]) == 2
    assert [f["regular"] for f in value["metadata"]["files"]] == [True,False]
    assert any(p["pid"] == 1 and p["same_executable"] for p in value["metadata"]["processes"])
    assert value["metadata"]["unreadable_pids"] == []
    assert "EXAMPLE_CENSUS_PRIVATE_LIFECYCLE" not in json.dumps(value)
    assert value["owner"]["started_at_sha256"] and "started_at" not in value["owner"]


@pytest.mark.parametrize("topology, expected", [("exposed", "FAIL"), ("isolated", "PASS")])
def test_lab_topology_end_to_end(tmp_path, topology, expected):
    if not _docker_ok():
        pytest.fail("Docker daemon is not available; this test must not pass without running")
    allowed, denied = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    run_id = f"lab{secrets.token_hex(4)}"
    run_dir = tmp_path / "run"
    # Keep the lab's declared values and scope; bind this temporary fixture to
    # the exact pack used by the test without rewriting its historical input.
    policy_data = yaml.safe_load((ROOT / "lab/policy.yaml").read_text(encoding="utf-8"))
    policy_data["pack"] = baseline_pack().ref().model_dump(mode="json")
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(yaml.safe_dump(policy_data, allow_unicode=True), encoding="utf-8")
    env = {"GUARD_LAB_ALLOWED_KEY": allowed, "GUARD_LAB_DENIED_KEY": denied}
    with Docker(run_id) as docker:  # 실패해도 __exit__에서 cleanup
        lab.up(docker, topology, run_dir, allowed)
        # This existing isolated CI lab already has the helper image locally.
        # Exercise the pressure transport's no-pull boundary without vLLM.
        target = json.loads((run_dir / "target.json").read_text(encoding="utf-8"))
        no_pull = docker.run_once(lab.PYTHON_IMAGE, "no-pull-control",
            network=target["origins"]["trusted"]["network"], timeout=30,
            command=["python", "-c", "print('guard-local-probe')"], pull_never=True)
        assert no_pull.returncode == 0 and no_pull.stdout.strip() == "guard-local-probe"
        missing = docker.run_once("example.invalid/guard-never-pull@sha256:" + "0" * 64,
            "no-pull-missing", network=target["origins"]["trusted"]["network"], timeout=30,
            command=["python", "-V"], pull_never=True)
        assert missing.returncode != 0  # Missing local image fails instead of being fetched.
        # Exercise the actual read-only helper in a pinned owned Python source,
        # not vLLM. Explicit namespace setup matches its restricted profile.
        pinned = docker.image_info(lab.PYTHON_IMAGE, timeout=30)["digests"][0]
        resource_name = docker.name("resource-source")
        docker._docker(["run", "-d", "--name", resource_name, "--label", docker.label,
            "--network", target["origins"]["trusted"]["network"], "--cgroupns=private", "--pull=never",
            pinned, "python", "-c", "import socket,time; s=socket.socket(); s.bind(('0.0.0.0',18000)); "
            "s.listen(1); print('RESOURCE_READY',flush=True); time.sleep(300)"], timeout=30)
        lab._wait(lambda: "RESOURCE_READY" in docker.logs(resource_name, timeout=30), "owned resource fixture")
        from guard.executors.pressure_resources import capture_resource_sample
        resource_target = {"run_id": run_id, "engine": {"container": resource_name, "image_digest": pinned}}
        _lifecycle_session_inventory(docker, resource_target, pinned)
        _native_weight_hashes(docker, resource_target, pinned)
        observed = capture_resource_sample(docker, resource_target, environment(engine={"name": "engine-x", "image_digest": pinned}),
                                           deadline=time.monotonic() + 30, port=18000)
        assert observed["complete"] is True, observed["diagnostic_codes"]
        assert observed["owner"]["run_id"] == run_id
        assert observed["observations"]["clock"]["sample_finished_monotonic_ns"] >= observed["observations"]["clock"]["sample_started_monotonic_ns"]
        listeners = [row for row in observed["observations"]["tcp_connections"] if row["state"] == 10 and row["owners"]]
        assert listeners
        assert all(row["queue_kind"] == "listen_backlog_connections"
                   and row["tx_queue_bytes"] is row["rx_queue_bytes"] is None
                   and row["listen_pending_connections"] == 0 for row in listeners)

        # The policy explicitly includes this helper itself. It does not stand
        # in for an engine worker, SDK or usage producer. Both endpoints use the
        # lab's already-local pinned Python image; no image is pulled here.
        trusted_network = target["origins"]["trusted"]["network"]
        receiver_name = docker.name("egress-receiver")
        receiver_command = ["python3", "-u", "-c",
            Path(egress_receiver.__file__).read_text(encoding="utf-8"), "18001"]
        docker._docker(["run", "-d", "--name", receiver_name, "--label", docker.label,
            "--network", trusted_network, "--cgroupns=private", "--pull=never",
            pinned, *receiver_command], timeout=30)
        lab._wait(lambda: egress_receiver.READY in docker.logs(receiver_name, timeout=30),
                  "owned egress receiver fixture")
        source_inspect = docker.inspect_json(resource_name, timeout=5)
        receiver_inspect = docker.inspect_json(receiver_name, timeout=5)
        source_cid, receiver_cid = source_inspect["Id"], receiver_inspect["Id"]
        receiver_ip = receiver_inspect["NetworkSettings"]["Networks"][trusted_network]["IPAddress"]
        assert source_cid != receiver_cid
        assert str(ipaddress.IPv4Address(receiver_ip)) == receiver_ip
        source_ns = docker.exec_bounded(resource_name,
            ["python3", "-c", "import os; print(os.readlink('/proc/self/ns/net'))"],
            timeout=5, max_bytes=1024)
        assert source_ns.returncode == 0
        assert source_ns.capture["complete"] is True and source_ns.capture["bounded_at_source"] is True
        assert source_ns.capture["truncated"] is False and source_ns.capture["timed_out"] is False
        namespace = re.fullmatch(r"net:\[([1-9][0-9]*)\]", source_ns.stdout.strip())
        assert namespace is not None
        source_netns = int(namespace.group(1))

        egress_dir = run_dir / "egress"
        egress_dir.mkdir()
        source_target = {"lab": True, "run_id": run_id,
            "origins": {"trusted": {"network": trusted_network}},
            "engine": {"container": resource_name, "image_digest": pinned,
                       "trusted_url": f"http://{resource_name}:18000"}}
        receiver_target = {"lab": True, "run_id": run_id,
            "origins": {"trusted": {"network": trusted_network}},
            "engine": {"container": receiver_name, "image_digest": pinned,
                       "trusted_url": f"http://{receiver_ip}:18001"}}
        source_path, receiver_path = egress_dir / "target.json", egress_dir / "receiver-target.json"
        source_path.write_text(json.dumps(source_target), encoding="utf-8")
        receiver_path.write_text(json.dumps(receiver_target), encoding="utf-8")
        now = datetime.now(timezone.utc)
        egress_env = environment(id="ENV-EXAMPLE-EGRESS",
            engine={"name": "EXAMPLE-python-helper", "image_digest": pinned})
        egress_policy = policy(baseline_pack().ref(), id="POL-EXAMPLE-EGRESS", created_at=now,
            scope={"include": ["PLT-07"], "exclude": []}, values={"PLT-07": {
                "subject_scope": "all_processes_in_container_uid_netns",
                "subject_container_id": source_cid, "subject_uid": 0,
                "subject_net_namespace_inode": source_netns,
                "forbidden_receiver_container_id": receiver_cid,
                "forbidden_receiver_ip": receiver_ip, "forbidden_receiver_port": 18001,
                "forbidden_protocol": "tcp", "forbidden_operation": "tcp_connect",
                "validity_start": (now - timedelta(seconds=10)).isoformat(),
                "validity_end": (now + timedelta(seconds=60)).isoformat(),
            }})
        assert egress_env.hash_ok() and egress_policy.hash_ok()
        egress_env_path, egress_policy_path = egress_dir / "env.yaml", egress_dir / "policy.yaml"
        egress_env_path.write_text(yaml.safe_dump(egress_env.to_dict(), allow_unicode=True), encoding="utf-8")
        egress_policy_path.write_text(yaml.safe_dump(egress_policy.to_dict(), allow_unicode=True), encoding="utf-8")
        egress_store = egress_dir / ".guard"
        egress_cfg = egress_dir / "guard.toml"
        egress_cfg.write_text(
            f'store = "{egress_store.as_posix()}"\n[adapter]\nname = "vllm"\n'
            '[executors]\nboundary_egress = "real"\n[boundary_egress]\n'
            f'target = "{source_path.as_posix()}"\nreceiver_target = "{receiver_path.as_posix()}"\n'
            'connect_timeout = 0.5\nmax_duration_seconds = 30\n', encoding="utf-8")
        egress_c = ["--config", str(egress_cfg)]
        egress_pack = ["--pack", str(ROOT / "packs/PACK-BASELINE")]
        selected = _ok(["select", *egress_pack, "--env", str(egress_env_path),
                        "--policy", str(egress_policy_path), *egress_c], env)
        egress_plan = selected.strip().splitlines()[-1]
        _ok(["run", "--plan", egress_plan, *egress_pack, *egress_c], env)
        _ok(["judge", "--plan", egress_plan, *egress_pack, *egress_c], env)
        egress_report = egress_dir / "report.json"
        _ok(["report", "--plan", egress_plan, *egress_pack, *egress_c,
             "--json", str(egress_report)], env)
        egress_data = json.loads(egress_report.read_text(encoding="utf-8"))
        egress_row, = [r for r in egress_data["by_check"] if r["check_id"] == "PLT-07"]
        assert egress_row["single"]["security"] == "FAIL"
        assert egress_row["single"]["functional"] == egress_row["single"]["performance"] == "UNKNOWN"
        assert egress_row["single"]["verification_level"] == "EXAMPLE" and egress_data["official"] is False
        egress_evd, = Store(egress_store).find_latest("EVD", lambda e: e.check_id == "PLT-07")
        assert egress_evd.execution.status.value == "COMPLETED" and egress_evd.data_origin.value == "EXAMPLE"
        assert egress_evd.artifacts == []  # Canonical typed observations need no duplicate Store artifact.
        assert egress_evd.observations["forbidden_egress_successes"].collected is True
        assert len(egress_evd.observations["forbidden_egress_successes"].value) == 1
        assert egress_evd.observations["egress_scope_complete"].value is False
        egress_trial, = egress_evd.observations["egress_trials"].value
        assert egress_trial["verified"] is True and egress_trial["diagnostic_codes"] == []
        assert egress_trial["sender_identity"]["uid"] == 0
        assert egress_trial["sender_identity"]["net_namespace_inode"] == source_netns
        assert egress_trial["receiver_identity"]["net_namespace_inode"] != source_netns
        assert egress_trial["source_owner"]["container_id"] == source_cid
        assert egress_trial["receiver_owner"]["container_id"] == receiver_cid

        _api_transport_cli(docker, run_id, trusted_network, pinned, run_dir, env)

        cfg = run_dir / "guard.toml"
        cfg.write_text(
            f'store = "{(run_dir / ".guard").as_posix()}"\n[adapter]\nname = "vllm"\n[executors]\ndirect_route = "real"\n'
            f'[direct_route]\ntarget = "{(run_dir / "target.json").as_posix()}"\nallowed_key_env = "GUARD_LAB_ALLOWED_KEY"\n'
            f'denied_key_env = "GUARD_LAB_DENIED_KEY"\nrequest_timeout = 3.0\n', encoding="utf-8")
        c = ["--config", str(cfg)]
        out = _ok(["select", "--pack", str(ROOT / "packs/PACK-BASELINE"), "--env", str(run_dir / "env.yaml"),
                   "--policy", str(policy_path), *c], env)
        plan = out.strip().splitlines()[-1]
        pack = ["--pack", str(ROOT / "packs/PACK-BASELINE")]
        print(_ok(["run", "--plan", plan, *pack, *c], env))
        print(_ok(["judge", "--plan", plan, *pack, *c], env))
        _ok(["report", "--plan", plan, *pack, *c, "--json", str(run_dir / "report.json")], env)
        gw_logs = docker.logs(docker.name("gw"), timeout=30)
        engine_logs = docker.logs(docker.name("engine"), timeout=30)

    data = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    (row,) = [r for r in data["by_check"] if r["check_id"] == "REQ-01"]
    summary = None
    for p in (run_dir / ".guard/artifacts").iterdir():
        last = p.read_text(encoding="utf-8").strip().splitlines()[-1]
        if '"summary": true' in last:
            summary = json.loads(last)
    print(f"\n[{topology}] REQ-01 security={row['single']['security']} level={row['single']['verification_level']}")
    print(f"[{topology}] direct_outcomes={summary['direct_outcomes']}")
    print(f"[{topology}] direct_allowed_outcomes={summary['direct_allowed_outcomes']}")
    print(f"[{topology}] gateway_outcomes={summary['gateway_outcomes']} trusted={summary['trusted_origin_reachable']}")
    (run_dir / "observed.json").write_text(json.dumps({"topology": topology, **summary}, indent=2), encoding="utf-8")

    assert row["single"]["security"] == expected
    assert row["single"]["verification_level"] == "EXAMPLE"  # 실험실 증거는 EXAMPLE
    assert data["official"] is False

    # 키가 어떤 파일에도 남지 않았다 (저장소·증거·보고서·target·env·로그)
    for p in run_dir.rglob("*"):
        if p.is_file():
            text = p.read_bytes().decode("utf-8", "replace")
            assert allowed not in text and denied not in text, p
    assert allowed not in gw_logs + engine_logs and denied not in gw_logs + engine_logs

    # 정리 확인: guard.run 라벨 컨테이너·네트워크 0개
    assert _labelled("containers") == [] and _labelled("networks") == []
