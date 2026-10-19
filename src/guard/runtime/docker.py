"""`docker` CLI를 subprocess로 부르는 얇은 래퍼. adapters/·executors/(와 lab/)에서만 쓴다. 코어는 모른다.

- 모든 호출에 timeout 필수(키워드, 기본값 없음).
- 컨테이너·네트워크 이름은 `guard-<run_id>-<suffix>`, 라벨 `guard.run=<run_id>`. cleanup은 라벨로 전부 지운다.
- 포트는 127.0.0.1에만 공개한다.
- DOCKER_HOST 등 환경변수는 코드에서 설정하지 않는다. 부모 환경을 그대로 통과시킨다.
- 비밀 값은 `-e NAME`(이름만)으로 넘기고 값은 그 호출의 subprocess 환경에만 둔다(명령 인자에 남지 않음).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_RUN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{3,39}$")
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
LABEL_KEY = "guard.run"


@dataclass(frozen=True)
class CmdResult:
    returncode: int
    stdout: str
    stderr: str


class Runner(Protocol):
    def __call__(self, argv: list[str], *, input: bytes | None, timeout: float, env_extra: dict[str, str]) -> CmdResult: ...


def subprocess_runner(argv: list[str], *, input: bytes | None, timeout: float, env_extra: dict[str, str]) -> CmdResult:
    env = {**os.environ, **env_extra} if env_extra else None  # None = 부모 환경 그대로
    proc = subprocess.run(argv, input=input, capture_output=True, timeout=timeout, env=env, check=False)
    return CmdResult(proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace"))


class DockerError(RuntimeError):
    pass


@dataclass(frozen=True)
class Mount:
    source: Path
    target: str
    read_only: bool = True


class Docker:
    def __init__(self, run_id: str, runner: Runner = subprocess_runner, binary: str = "docker"):
        if not _RUN_ID.match(run_id):
            raise ValueError(f"invalid run id {run_id!r}")
        self.run_id = run_id
        self.prefix = f"guard-{run_id}"
        self.label = f"{LABEL_KEY}={run_id}"
        self._runner = runner
        self._bin = binary

    def name(self, suffix: str) -> str:
        return f"{self.prefix}-{suffix}"

    # --- 기본 호출 ---------------------------------------------------------------

    def _docker(self, args: list[str], *, timeout: float, input: bytes | None = None,
                env_extra: dict[str, str] | None = None, check: bool = True) -> CmdResult:
        argv = [self._bin, *args]
        try:
            res = self._runner(argv, input=input, timeout=timeout, env_extra=env_extra or {})
        except subprocess.TimeoutExpired as exc:
            raise DockerError(f"docker {' '.join(args[:2])} timed out after {timeout}s") from exc
        if check and res.returncode != 0:
            raise DockerError(f"docker {' '.join(args[:2])} failed ({res.returncode}): {res.stderr.strip()[:500]}")
        return res

    @staticmethod
    def _check_secret_env(secret_env: dict[str, str]) -> None:
        for k in secret_env:
            if not _ENV_NAME.match(k) or k.startswith("DOCKER"):
                raise ValueError(f"refusing env name {k!r}")

    # --- 네트워크 ----------------------------------------------------------------

    def network_create(self, suffix: str, *, internal: bool = False, timeout: float) -> str:
        name = self.name(suffix)
        args = ["network", "create", "--label", self.label]
        if internal:
            args.append("--internal")
        self._docker([*args, name], timeout=timeout)
        return name

    def network_connect(self, network: str, container: str, *, timeout: float) -> None:
        self._docker(["network", "connect", network, container], timeout=timeout)

    # --- 컨테이너 ----------------------------------------------------------------

    def _common_run_args(self, name: str, networks: list[str], mounts: list[Mount], public_env: dict[str, str],
                         secret_env: dict[str, str]) -> list[str]:
        self._check_secret_env(secret_env)
        args = ["--name", name, "--label", self.label]
        if networks:
            args += ["--network", networks[0]]
        for m in mounts:
            args += ["-v", f"{m.source}:{m.target}{':ro' if m.read_only else ''}"]
        for k, v in public_env.items():
            if k in secret_env or k.startswith("DOCKER"):
                raise ValueError(f"refusing env name {k!r}")
            args += ["-e", f"{k}={v}"]
        for k in secret_env:
            args += ["-e", k]  # 값은 subprocess 환경으로만
        return args

    def run_detached(self, image: str, suffix: str, *, networks: list[str], timeout: float,
                     publish: list[int] | None = None, mounts: list[Mount] | None = None,
                     public_env: dict[str, str] | None = None, secret_env: dict[str, str] | None = None,
                     command: list[str] | None = None, gpus: bool = False, shm_size: str | None = None,
                     cap_add: list[str] | None = None, cap_drop: list[str] | None = None, read_only: bool = False,
                     tmpfs: list[str] | None = None, user: str | None = None,
                     security_opt: list[str] | None = None) -> str:
        name = self.name(suffix)
        secret_env = secret_env or {}
        args = ["run", "-d", *self._common_run_args(name, networks, mounts or [], public_env or {}, secret_env)]
        if gpus:
            args += ["--gpus", "all"]
        if shm_size:
            args += ["--shm-size", shm_size]
        for c in cap_add or []:
            args += ["--cap-add", c]
        for c in cap_drop or []:
            args += ["--cap-drop", c]
        if read_only:
            args.append("--read-only")
        for t in tmpfs or []:
            if not t.startswith("/") or "privileged" in t:
                raise ValueError(f"tmpfs must be a container path: {t!r}")
            args += ["--tmpfs", t]
        if user:
            args += ["--user", user]
        for o in security_opt or []:
            if "unconfined" in o:
                raise ValueError(f"refusing security option {o!r}")
            args += ["--security-opt", o]
        for p in publish or []:
            if not isinstance(p, int):
                raise ValueError(f"publish takes container ports only (bound to 127.0.0.1): {p!r}")
            args += ["-p", f"127.0.0.1::{p}"]
        args += [image, *(command or [])]
        self._docker(args, timeout=timeout, env_extra=secret_env)
        for extra in networks[1:]:
            self.network_connect(extra, name, timeout=timeout)
        return name

    def run_once(self, image: str, suffix: str, *, network: str, timeout: float, command: list[str],
                 input: bytes | None = None, mounts: list[Mount] | None = None) -> CmdResult:
        """`docker run --rm -i`: stdin으로 입력을 주고 stdout을 받는다. 실패 코드는 호출자가 본다."""
        name = self.name(suffix)
        args = ["run", "--rm", "-i", *self._common_run_args(name, [network], mounts or [], {}, {}), image, *command]
        return self._docker(args, timeout=timeout, input=input, check=False)

    def exec(self, container: str, argv: list[str], *, timeout: float) -> CmdResult:
        """`docker exec`(대상 컨테이너 안 명령). 호출자는 읽기 전용 명령만 쓴다. 실패 코드는 호출자가 본다."""
        return self._docker(["exec", container, *argv], timeout=timeout, check=False)

    def inspect_json(self, container: str, *, timeout: float) -> dict:
        out = self._docker(["inspect", container], timeout=timeout).stdout
        data = json.loads(out)
        return data[0] if isinstance(data, list) else data

    def logs(self, container: str, *, timeout: float) -> str:
        res = self._docker(["logs", container], timeout=timeout, check=False)
        return res.stdout + res.stderr

    def is_running(self, container: str, *, timeout: float) -> bool:
        res = self._docker(["inspect", "--format", "{{.State.Running}}", container], timeout=timeout, check=False)
        return res.returncode == 0 and res.stdout.strip() == "true"

    # --- 조회 --------------------------------------------------------------------

    def published_port(self, container: str, container_port: int, *, timeout: float) -> int:
        out = self._docker(["port", container, str(container_port)], timeout=timeout).stdout.strip().splitlines()
        for line in out:
            host, _, port = line.rpartition(":")
            if host == "127.0.0.1":
                return int(port)
        raise DockerError(f"{container}:{container_port} is not published on 127.0.0.1 only: {out}")

    def ip(self, container: str, network: str, *, timeout: float) -> str:
        out = self._docker(["inspect", "--format", "{{json .NetworkSettings.Networks}}", container], timeout=timeout).stdout
        nets = json.loads(out)
        ip = (nets.get(network) or {}).get("IPAddress")
        if not ip:
            raise DockerError(f"{container} has no IP on {network}")
        return ip

    def image_digest(self, image: str, *, timeout: float) -> str | None:
        res = self._docker(["image", "inspect", "--format", "{{json .RepoDigests}}", image], timeout=timeout, check=False)
        if res.returncode != 0:
            return None
        digests = json.loads(res.stdout or "[]") or []
        return digests[0] if digests else None

    def container_image(self, container: str, *, timeout: float) -> str:
        return self._docker(["inspect", "--format", "{{.Config.Image}}", container], timeout=timeout).stdout.strip()

    def pull(self, image: str, *, timeout: float) -> None:
        self._docker(["pull", "--quiet", image], timeout=timeout)

    # --- 정리 --------------------------------------------------------------------

    def cleanup(self, *, timeout: float) -> list[str]:
        """이 실행 ID의 컨테이너·네트워크를 전부 지운다. 단계가 실패해도 다음 단계를 계속하고 문제를 돌려준다."""
        problems: list[str] = []
        try:
            ids = self._docker(["ps", "-aq", "--filter", f"label={self.label}"], timeout=timeout).stdout.split()
            if ids:
                self._docker(["rm", "-f", *ids], timeout=timeout)
        except DockerError as exc:
            problems.append(str(exc))
        try:
            nets = self._docker(["network", "ls", "-q", "--filter", f"label={self.label}"], timeout=timeout).stdout.split()
            for n in nets:
                self._docker(["network", "rm", n], timeout=timeout)
        except DockerError as exc:
            problems.append(str(exc))
        return problems

    def leftovers(self, *, timeout: float) -> dict[str, list[str]]:
        ids = self._docker(["ps", "-aq", "--filter", f"label={self.label}"], timeout=timeout).stdout.split()
        nets = self._docker(["network", "ls", "-q", "--filter", f"label={self.label}"], timeout=timeout).stdout.split()
        return {"containers": ids, "networks": nets}

    def __enter__(self) -> "Docker":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        problems = self.cleanup(timeout=60)
        if problems:
            message = f"cleanup incomplete for {self.label}; inspect labelled containers/networks"
            if exc is not None:
                exc.add_note(message)
            else:
                raise DockerError(message)
