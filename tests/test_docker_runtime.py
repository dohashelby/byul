"""runtime/docker.py: 가짜 명령 실행기로 검증 (Docker 불필요)."""

import ast
import subprocess
from pathlib import Path

import pytest

from guard.runtime.docker import CmdResult, Docker, DockerError, Mount

SRC = Path(__file__).resolve().parents[1] / "src/guard"


class FakeRunner:
    def __init__(self, responses=None, fail_on=None):
        self.calls = []
        self.responses = responses or {}
        self.fail_on = fail_on

    def __call__(self, argv, *, input, timeout, env_extra):
        self.calls.append({"argv": argv, "input": input, "timeout": timeout, "env_extra": env_extra})
        if self.fail_on and self.fail_on in " ".join(argv):
            if self.fail_on == "TIMEOUT":
                raise subprocess.TimeoutExpired(argv, timeout)
            return CmdResult(1, "", "boom")
        for prefix, out in self.responses.items():
            if " ".join(argv[1:]).startswith(prefix):
                return CmdResult(0, out, "")
        return CmdResult(0, "", "")


def test_timeout_is_required():
    d = Docker("t1abc", runner=FakeRunner())
    with pytest.raises(TypeError):
        d.network_create("net")  # timeout 없음
    with pytest.raises(TypeError):
        d.cleanup()


def test_names_and_labels_have_run_prefix():
    r = FakeRunner()
    d = Docker("t1abc", runner=r)
    assert d.network_create("net", internal=True, timeout=5) == "guard-t1abc-net"
    argv = r.calls[0]["argv"]
    assert argv[:3] == ["docker", "network", "create"]
    assert "--internal" in argv and "--label" in argv and "guard.run=t1abc" in argv
    d.run_detached("img:1", "engine", networks=["guard-t1abc-net"], timeout=5)
    argv = r.calls[1]["argv"]
    assert "--name" in argv and argv[argv.index("--name") + 1] == "guard-t1abc-engine"
    assert "guard.run=t1abc" in argv


def test_bad_run_id_rejected():
    with pytest.raises(ValueError):
        Docker("../x", runner=FakeRunner())


def test_ports_bound_to_loopback_only():
    r = FakeRunner()
    d = Docker("t1abc", runner=r)
    d.run_detached("img", "gw", networks=["n"], publish=[8080], timeout=5)
    argv = r.calls[0]["argv"]
    assert argv[argv.index("-p") + 1] == "127.0.0.1::8080"
    with pytest.raises(ValueError):
        d.run_detached("img", "gw", networks=["n"], publish=["0.0.0.0:80:80"], timeout=5)


def test_secret_env_passed_by_name_only():
    r = FakeRunner()
    d = Docker("t1abc", runner=r)
    d.run_detached("img", "gw", networks=["n"], secret_env={"GUARD_ALLOWED_KEY": "s3cr3t"}, timeout=5)
    call = r.calls[0]
    assert "s3cr3t" not in " ".join(call["argv"])
    assert call["argv"][call["argv"].index("-e") + 1] == "GUARD_ALLOWED_KEY"
    assert call["env_extra"] == {"GUARD_ALLOWED_KEY": "s3cr3t"}


def test_code_never_sets_docker_env():
    d = Docker("t1abc", runner=FakeRunner())
    with pytest.raises(ValueError):
        d.run_detached("img", "x", networks=["n"], secret_env={"DOCKER_HOST": "tcp://x"}, timeout=5)
    text = (SRC / "runtime/docker.py").read_text(encoding="utf-8")
    assert "os.environ[" not in text and "putenv" not in text and "setdefault(\"DOCKER" not in text


def test_mounts_are_read_only():
    r = FakeRunner()
    d = Docker("t1abc", runner=r)
    d.run_once("img", "probe", network="n", mounts=[Mount(Path("/a/b.py"), "/probe/b.py")], command=["python"], input=b"{}", timeout=5)
    argv = r.calls[0]["argv"]
    assert argv[argv.index("-v") + 1] == "/a/b.py:/probe/b.py:ro"
    assert "--rm" in argv and "-i" in argv
    assert r.calls[0]["input"] == b"{}"


def test_failure_raises_docker_error():
    d = Docker("t1abc", runner=FakeRunner(fail_on="network create"))
    with pytest.raises(DockerError):
        d.network_create("net", timeout=5)


def test_subprocess_timeout_becomes_docker_error():
    d = Docker("t1abc", runner=FakeRunner(fail_on="TIMEOUT"))
    with pytest.raises(DockerError, match="timed out"):
        d._docker(["ps", "TIMEOUT"], timeout=1)


def test_cleanup_removes_labelled_resources_even_after_failure():
    r = FakeRunner(responses={"ps -aq --filter label=guard.run=t1abc": "c1\nc2\n",
                              "network ls -q --filter label=guard.run=t1abc": "n1\n"})
    with pytest.raises(RuntimeError):
        with Docker("t1abc", runner=r):
            raise RuntimeError("test body failed")
    joined = [" ".join(c["argv"]) for c in r.calls]
    assert "docker rm -f c1 c2" in joined
    assert "docker network rm n1" in joined


def test_cleanup_continues_when_one_step_fails():
    r = FakeRunner(responses={"ps -aq": "c1\n", "network ls -q": "n1\n"}, fail_on="rm -f")
    problems = Docker("t1abc", runner=r).cleanup(timeout=5)
    assert problems  # 컨테이너 삭제 실패를 보고
    assert any(" ".join(c["argv"]) == "docker network rm n1" for c in r.calls)  # 그래도 네트워크 삭제 시도


def test_published_port_and_ip_parsing():
    r = FakeRunner(responses={
        "port guard-t1abc-gw 8080": "127.0.0.1:55001\n",
        "inspect --format": '{"guard-t1abc-net": {"IPAddress": "172.30.0.5"}}',
        "image inspect": '["nginx@sha256:abc"]',
    })
    d = Docker("t1abc", runner=r)
    assert d.published_port("guard-t1abc-gw", 8080, timeout=5) == 55001
    assert d.ip("guard-t1abc-x", "guard-t1abc-net", timeout=5) == "172.30.0.5"
    assert d.image_digest("nginx:1", timeout=5) == "nginx@sha256:abc"


def test_published_port_refuses_non_loopback():
    d = Docker("t1abc", runner=FakeRunner(responses={"port": "0.0.0.0:55001\n"}))
    with pytest.raises(DockerError):
        d.published_port("x", 80, timeout=5)


def _imports(path: Path) -> set[str]:
    mods = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
        elif isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
    return mods


def test_runtime_used_only_by_adapters_and_executors():
    users = set()
    for p in SRC.rglob("*.py"):
        if any(m.startswith("guard.runtime") for m in _imports(p)) and "runtime" not in p.parts:
            users.add(p.relative_to(SRC).parts[0])
    assert users <= {"adapters", "executors"}
