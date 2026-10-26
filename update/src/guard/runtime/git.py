"""분석용 git 호출. 셸·작업 트리 변환·외부 diff/textconv를 사용하지 않는다."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


class GitError(ValueError):
    pass


def run_git(repo: Path | None, *args: str, input_bytes: bytes | None = None,
            no_lazy_fetch: bool = False) -> bytes:
    env = dict(os.environ)
    env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    if no_lazy_fetch:
        env["GIT_NO_LAZY_FETCH"] = "1"
    command = ["git", "-c", "protocol.ext.allow=never", "-c", "core.fsmonitor=false"]
    if repo is not None:
        command += ["-C", str(repo)]
    try:
        result = subprocess.run(command + list(args), input=input_bytes, capture_output=True, env=env,
                                check=False, timeout=300)
    except subprocess.TimeoutExpired as exc:
        raise GitError("git_command_timeout") from exc
    except OSError as exc:
        raise GitError("git_unavailable") from exc
    if result.returncode:
        # 원격 URL·토큰·개인 경로가 포함될 수 있는 stderr를 제품 오류에 복사하지 않는다.
        raise GitError(f"git_command_failed:{args[0] if args else 'unknown'}:{result.returncode}")
    return result.stdout


def ensure_repository(cache: Path, url: str, *, fetch: bool = True) -> Path:
    cache = Path(cache)
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        run_git(None, "clone", "--filter=blob:none", "--no-checkout", "--", url, str(cache))
    elif not (cache / ".git").exists() and not (cache / "HEAD").exists():
        raise GitError("cache_is_not_git_repository")
    elif fetch:
        run_git(cache, "fetch", "--filter=blob:none", "--tags", "origin")
    return cache


def resolve_commit(repo: Path, ref: str) -> str:
    if not ref or ref.startswith("-") or "\x00" in ref or "\n" in ref:
        raise GitError("invalid_revision")
    value = run_git(repo, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")
    sha = value.decode("ascii").strip()
    if len(sha) not in (40, 64) or any(c not in "0123456789abcdef" for c in sha):
        raise GitError("invalid_resolved_commit")
    return sha


def changed_paths(repo: Path, baseline: str, candidate: str) -> list[dict]:
    raw = run_git(repo, "diff", "--no-ext-diff", "--no-textconv", "--name-status", "-z", "-M",
                  f"{baseline}..{candidate}", "--")
    parts = raw.split(b"\0")
    result = []
    i = 0
    while i < len(parts) and parts[i]:
        status = parts[i].decode("ascii")
        i += 1
        first = parts[i].decode("utf-8", "surrogateescape")
        i += 1
        if status.startswith(("R", "C")):
            second = parts[i].decode("utf-8", "surrogateescape")
            i += 1
            old, new = first, second
        else:
            old, new = (None, first) if status == "A" else (first, None) if status == "D" else (first, first)
        result.append({"status": status, "before_path": old, "after_path": new})
    return result


def read_blob(repo: Path, commit: str, path: str) -> bytes:
    # commit은 resolve_commit 결과이며 path는 git diff가 반환한 원본 상대 경로다.
    return run_git(repo, "cat-file", "blob", f"{commit}:{path}")


def read_blobs(repo: Path, requests: list[tuple[str, str]]) -> dict[tuple[str, str], bytes]:
    """중복 원본 읽기를 한 cat-file 호출로 묶는다. 줄바꿈 경로는 별도 안전 인자로 읽는다."""
    requests = list(dict.fromkeys(requests))
    _prefetch_missing_blobs(repo, requests)
    plain = [pair for pair in requests if "\n" not in pair[1] and "\r" not in pair[1]]
    result = {pair: read_blob(repo, *pair) for pair in requests if pair not in plain}
    if not plain:
        return result
    raw = run_git(repo, "cat-file", "--batch", input_bytes=b"".join(
        f"{commit}:{path}\n".encode("utf-8", "surrogateescape") for commit, path in plain))
    position = 0
    for pair in plain:
        end = raw.index(b"\n", position)
        header = raw[position:end].split()
        if len(header) != 3 or header[1] != b"blob":
            raise GitError("git_blob_missing")
        size = int(header[2])
        position = end + 1
        result[pair] = raw[position:position + size]
        position += size + 1
    return result


def _prefetch_missing_blobs(repo: Path, requests: list[tuple[str, str]]) -> None:
    """blobless 캐시의 누락 blob을 묶어서 취득해 파일마다 원격 fetch하는 비용을 피한다."""
    try:
        promisor = run_git(repo, "config", "--bool", "--get", "remote.origin.promisor").strip()
    except GitError:
        return
    if promisor != b"true":
        return
    wanted = set()
    for commit in dict.fromkeys(commit for commit, _ in requests):
        paths = {path for sha, path in requests if sha == commit}
        for row in run_git(repo, "ls-tree", "-r", "-z", "--full-tree", commit).split(b"\0"):
            if not row:
                continue
            header, path = row.split(b"\t", 1)
            mode, kind, oid = header.split()
            if path.decode("utf-8", "surrogateescape") in paths and kind == b"blob":
                wanted.add(oid)
    if not wanted:
        return
    # GIT_NO_LAZY_FETCH 지원 git에서는 이미 있는 객체를 제외한다. 지원이 없더라도
    # 바이트/해시 의미는 같고 이후 읽기와 동일한 승인된 원격 취득만 발생한다.
    checks = run_git(repo, "cat-file", "--batch-check", no_lazy_fetch=True,
                     input_bytes=b"\n".join(sorted(wanted)) + b"\n")
    missing = [row.split()[0].decode("ascii") for row in checks.splitlines() if row.endswith(b" missing")]
    for offset in range(0, len(missing), 200):
        # promisor의 blob 요청은 일반 ref 협상을 하지 않는다. stdin은 Windows의
        # 명령행 길이 제한도 피하며 원본 blob 200개 묶음을 그대로 유지한다.
        run_git(repo, "-c", "fetch.negotiationAlgorithm=noop", "fetch", "--no-tags",
                "--no-write-fetch-head", "--recurse-submodules=no", "--filter=blob:none", "--stdin", "origin",
                input_bytes=("\n".join(missing[offset:offset + 200]) + "\n").encode("ascii"))
