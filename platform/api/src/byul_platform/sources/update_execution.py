"""Run the existing guard CLI without a shell; keep preparation and execution distinct.

Only explicit POSTs start work. Direct inputs are frozen before preparation, and
rechecked before execution. Relative paths inside TOML keep guard's update-dir cwd.
The lock covers both preparation and execution, including other platform workers.
"""

from __future__ import annotations

import codecs
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import tomllib
from typing import Iterator
from uuid import uuid4

from pydantic import ValidationError

from ..config import Settings
from ..contracts import Contracts
from ..store import DocumentStore
from ..update_execution_models import LegacyUpdateRunRequest, UpdatePreparation, UpdateRun, UpdateRunRequest
from .registered_update_inputs import RegisteredInputError, RegisteredUpdateInputs
from .update import ReportDirUpdateSource, UpdateSource

LOG_LIMIT = 64 * 1024
_ID = re.compile(r"^RUN-[a-f0-9]{32}$")


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ExecutionError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


class ExecutionUpdateSource:
    """Use real local RPTs after the first run; never label example reports as live."""

    def __init__(self, base: UpdateSource, root: Path):
        self.base = base
        self.generated = ReportDirUpdateSource(root)

    def _has_reports(self) -> bool:
        # Source badges are read on every API request. Do not parse all RPTs for a badge.
        return next(self.generated.dir.rglob("report.json"), None) is not None

    @property
    def mode(self):
        return "live" if self._has_reports() else self.base.mode

    @property
    def detail(self):
        if not self._has_reports():
            return self.base.detail
        return str(self.generated.dir) if self.base.mode == "example" else f"{self.base.detail}; {self.generated.dir}"

    def reports(self):
        generated = self.generated.reports()
        if not generated:
            return self.base.reports()
        entries = ([] if self.base.mode == "example" else self.base.reports()) + generated
        unique = {}
        for entry in entries:
            ref = entry.candidate.get("report_ref", {})
            key = (ref.get("id"), ref.get("revision"), ref.get("sha256")) if ref else entry.path
            unique[key] = entry
        return list(unique.values())


class UpdateExecutionService:
    def __init__(self, settings: Settings, root: Path, contracts: Contracts, environments: DocumentStore, policies: DocumentStore):
        self.settings, self.root, self.contracts = settings, root.expanduser().resolve(), contracts
        self.update_dir = settings.update_dir.expanduser().resolve()
        self.python = (settings.update_python or self.update_dir / ".venv/bin/python").expanduser().absolute()
        self.registered_inputs = RegisteredUpdateInputs(settings, contracts, environments, policies)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="byul-update")
        self._mutex = threading.Lock()
        try:
            with self._exclusive():
                for record in self._records():
                    if record["status"] in {"preparing", "running"}:
                        record.update(status="interrupted", finished_at=_now(),
                                      error="플랫폼 실행이 중단됐습니다. 기존 검증 프로세스와 자원 정리 상태를 확인하세요")
                        self._save(record)
        except ExecutionError:
            pass  # Another worker owns the active operation.

    @property
    def available(self) -> bool:
        return self.unavailable_reason() is None

    def unavailable_reason(self) -> str | None:
        if not self.update_dir.is_dir() or not self.python.is_file() or not os.access(self.python, os.X_OK):
            return "서버의 검증 실행 환경이 준비되지 않았습니다. 관리자에게 문의하세요"
        return self.registered_inputs.unavailable_reason()

    def close(self) -> None:
        # Let guard's own finally/cleanup finish; do not kill engines on page/app exit.
        self._pool.shutdown(wait=True)

    def _folder(self, run_id: str) -> Path:
        if not _ID.fullmatch(run_id):
            raise ExecutionError("NOT_FOUND", "검증 실행 기록이 없습니다", 404)
        return self.root / run_id

    def _load(self, run_id: str) -> dict:
        try:
            return json.loads((self._folder(run_id) / "run.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ExecutionError("NOT_FOUND", "검증 실행 기록이 없습니다", 404) from None

    def _records(self) -> list[dict]:
        records = []
        for path in self.root.glob("RUN-*/run.json"):
            try:
                record = self._load(path.parent.name)
                UpdateRun.model_validate(record)
                records.append(record)
            except (ExecutionError, ValidationError):
                continue
        return sorted(records, key=lambda row: (row["created_at"], row["id"]), reverse=True)

    def _save(self, record: dict) -> None:
        folder = self._folder(record["id"])
        temporary = folder / "run.json.tmp"
        with temporary.open("w", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            json.dump(record, stream, ensure_ascii=False, indent=2)
        temporary.replace(folder / "run.json")

    def get(self, run_id: str, *, include_log: bool = True) -> UpdateRun:
        record = self._load(run_id)
        if include_log:
            try:
                record["log_tail"] = (self._folder(run_id) / "log.txt").read_text(encoding="utf-8")
            except OSError:
                pass
        return UpdateRun.model_validate(record)

    def list(self) -> list[UpdateRun]:
        return [UpdateRun.model_validate(row) for row in self._records()[:50]]

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        if not self._mutex.acquire(blocking=False):
            raise ExecutionError("UPDATE_BUSY", "다른 검증 작업이 진행 중입니다", 409)
        try:
            with (self.root / ".execution.lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ExecutionError("UPDATE_BUSY", "다른 검증 작업이 진행 중입니다", 409) from None
                try:
                    yield
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
        finally:
            self._mutex.release()

    def _claim(self):
        """Transfer the exclusive lock to a worker until the CLI and cleanup end."""
        lease = self._exclusive()
        lease.__enter__()
        for row in self._records():
            pid = row.get("process_id")
            if pid:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    continue
                except PermissionError:
                    pass
                lease.__exit__(None, None, None)
                raise ExecutionError("UPDATE_BUSY", "이전 검증 프로세스가 남아 있습니다. 실행·정리 상태를 확인하세요", 409)
        return lease

    def _inputs(self, request: LegacyUpdateRunRequest) -> dict[str, Path]:
        files = {}
        pack = Path(request.pack_path)
        if not pack.is_dir():
            raise ExecutionError("INPUT_FILES_INVALID", "검증 Pack 폴더를 찾을 수 없습니다")
        for path in sorted(pack.rglob("*")):
            if path.is_symlink():
                raise ExecutionError("INPUT_FILES_INVALID", "Pack은 심볼릭 링크 없는 폴더여야 합니다")
            if path.is_file():
                files[f"pack/{path.relative_to(pack).as_posix()}"] = path
        if not files:
            raise ExecutionError("INPUT_FILES_INVALID", "검증 Pack 폴더가 비어 있습니다")
        for key, name in (("config_path", "config.toml"), ("policy_path", "policy.json"),
                          ("environment_path", "environment.json"), ("baseline_path", "baseline.json"),
                          ("candidate_path", "candidate.json")):
            value = getattr(request, key)
            if value:
                files[name] = Path(value)
        # Preserve the canonical source documents, exact store refs and mapping alongside
        # converted ENV/POL. These files are not guard evidence or execution results.
        context = Path(request.policy_path).parent
        for name in ("source-policy.json", "source-environment.json", "source-baseline.json", "source-candidate.json", "provenance.json"):
            path = context / name
            if path.exists():
                files[name] = path
        return files

    def _fingerprint(self, request: LegacyUpdateRunRequest, destination: Path | None = None) -> str:
        digest = hashlib.sha256()
        try:
            for name, source in self._inputs(request).items():
                if not source.is_file():
                    raise ExecutionError("INPUT_FILES_INVALID", "입력은 서버에서 읽을 수 있는 일반 파일이어야 합니다")
                data = source.read_bytes()
                digest.update(name.encode() + b"\0" + hashlib.sha256(data).digest())
                if destination:
                    path = destination / name
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    path.write_bytes(data)
                    path.chmod(0o600)
        except (OSError, ValueError):
            raise ExecutionError("INPUT_FILES_INVALID", "입력 파일을 읽을 수 없습니다. 서버의 파일 경로·권한을 확인하세요") from None
        return digest.hexdigest()

    def prepare(self, request: UpdateRunRequest, actor: str) -> UpdateRun:
        if not self.available:
            raise ExecutionError("UPDATE_UNAVAILABLE", self.unavailable_reason(), 503)
        lease = self._claim()
        try:
            run_id = f"RUN-{uuid4().hex}"
            folder = self._folder(run_id)
            folder.mkdir(mode=0o700)
            try:
                files, refs, pack = self.registered_inputs.write(request, folder / "inputs")
            except RegisteredInputError as error:
                raise ExecutionError("REGISTERED_INPUT_INVALID", str(error)) from None
            fingerprint = self._fingerprint(files, folder / "inputs")
            record = UpdateRun(id=run_id, request=request, status="preparing", created_at=_now(),
                               requested_by=actor, input_sha256=fingerprint, source_refs=refs, pack=pack).model_dump(mode="json")
            record["file_inputs"] = files.model_dump(mode="json")
            self._save(record)
            self._pool.submit(self._work, record, False, lease)
            return UpdateRun.model_validate(record)
        except BaseException:
            lease.__exit__(None, None, None)
            raise

    def execute(self, run_id: str, actor: str) -> UpdateRun:
        if not self.available:
            raise ExecutionError("UPDATE_UNAVAILABLE", "update Python 환경을 확인하세요", 503)
        lease = self._claim()
        try:
            record = self._load(run_id)
            if "file_inputs" not in record:
                raise ExecutionError("REGISTERED_INPUT_REQUIRED", "이전 파일 입력 기록입니다. 등록한 환경·정책으로 준비 점검을 다시 진행하세요", 409)
            if record["status"] != "ready" or not record.get("preparation", {}).get("ready"):
                raise ExecutionError("PREPARATION_REQUIRED", "준비 점검을 통과한 기록만 실행할 수 있습니다", 409)
            self._check_registered(record)
            files = LegacyUpdateRunRequest.model_validate(record["file_inputs"])
            if self._fingerprint(files) != record["input_sha256"]:
                raise ExecutionError("INPUT_CHANGED", "준비 점검 후 입력 파일이 바뀌었습니다. 다시 준비 점검하세요", 409)
            record.update(status="running", started_at=_now(), finished_at=None, executed_by=actor,
                          error=None, exit_code=None)
            self._save(record)
            self._pool.submit(self._work, record, True, lease)
            return UpdateRun.model_validate(record)
        except BaseException:
            lease.__exit__(None, None, None)
            raise

    def _command(self, record: dict, prepare: bool) -> list[str]:
        request = UpdateRunRequest.model_validate(record["request"])
        folder = self._folder(record["id"])
        inputs = folder / "inputs"
        command = [str(self.python), "-B", "-u", "-m", "guard.cli",
                   ("prepare-" if prepare else "") + ("validation" if prepare and request.mode == "validate" else request.mode),
                   "--pack", str(inputs / "pack"), "--policy", str(inputs / "policy.json"),
                   "--config", str(inputs / "config.toml")]
        if request.mode == "validate":
            command += ["--env", str(inputs / "environment.json")]
        else:
            command += ["--baseline", str(inputs / "baseline.json"), "--candidate", str(inputs / "candidate.json"),
                        "--store", str(self.root / "objects")]
        if not prepare:
            command += ["--output", str(self.root / "reports" / record["id"])]
            if request.mode == "validate":
                command += ["--store", str(self.root / "objects")]
        return command

    def _snapshot_request(self, record: dict) -> LegacyUpdateRunRequest:
        values = dict(record["file_inputs"])
        inputs = self._folder(record["id"]) / "inputs"
        for key, name in (("pack_path", "pack"), ("config_path", "config.toml"), ("policy_path", "policy.json"),
                          ("environment_path", "environment.json"), ("baseline_path", "baseline.json"), ("candidate_path", "candidate.json")):
            if values.get(key):
                values[key] = str(inputs / name)
        return LegacyUpdateRunRequest.model_validate(values)

    def _check_registered(self, record: dict) -> None:
        try:
            self.registered_inputs.check(UpdateRunRequest.model_validate(record["request"]), record["source_refs"])
        except RegisteredInputError as error:
            raise ExecutionError("REGISTERED_INPUT_CHANGED", str(error), 409) from None

    def _redact(self, text: str, record: dict) -> str:
        # Known server credentials and common credential assignments must not reach the UI.
        secrets = [value for key, value in os.environ.items()
                   if re.search(r"(?:TOKEN|SECRET|PASSWORD|API_KEY|ACCESS_KEY|PRIVATE_KEY)", key, re.I) and len(value) >= 4]
        if self.settings.runtime_viewer_token:
            secrets.append(self.settings.runtime_viewer_token)
        try:
            config = tomllib.loads((self._folder(record["id"]) / "inputs/config.toml").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            config = {}

        def credentials(value, sensitive=False):
            if isinstance(value, dict):
                for key, item in value.items():
                    credentials(item, sensitive or bool(re.search(r"token|secret|password|api.?key|credential", key, re.I)))
            elif isinstance(value, list):
                for item in value:
                    credentials(item, sensitive)
            elif isinstance(value, str):
                if value in os.environ:
                    if len(os.environ[value]) >= 4:
                        secrets.append(os.environ[value])
                elif sensitive and len(value) >= 4:
                    secrets.append(value)

        credentials(config)
        for value in sorted(set(secrets), key=len, reverse=True):
            text = text.replace(value, "[REDACTED]")
        text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", text)
        return re.sub(r'''(?i)((?:password|token|api_key|secret|access_key)\s*[=:]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;]+)''',
                      r"\1[REDACTED]", text)

    def _invoke(self, record: dict, *, prepare: bool) -> tuple[int, str]:
        command = self._command(record, prepare)
        path = self._folder(record["id"]) / "log.txt"
        # Preparation JSON is small; only the log tail is retained for an actual run.
        captured = ""
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        pending = ""
        try:
            tail = path.read_text(encoding="utf-8")
        except OSError:
            tail = ""

        def append(text: str):
            nonlocal tail
            safe = (tail + self._redact(text, record)).encode("utf-8")
            record["log_truncated"] = record["log_truncated"] or len(safe) > LOG_LIMIT
            tail = safe[-LOG_LIMIT:].decode("utf-8", errors="ignore")
            temporary = path.with_suffix(".tmp")
            with temporary.open("w", encoding="utf-8") as stream:
                os.chmod(temporary, 0o600)
                stream.write(tail)
            temporary.replace(path)

        append("\n[준비 점검]\n" if prepare else "\n[검증 실행]\n")
        with subprocess.Popen(command, cwd=self.update_dir, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as process:
            record["process_id"] = process.pid
            self._save(record)
            assert process.stdout is not None
            while chunk := process.stdout.read1(4096):
                text = decoder.decode(chunk)
                if prepare:
                    captured += text
                    if len(captured) > 2 * 1024 * 1024:
                        # Keep draining so guard can finish its own cleanup.
                        captured = captured[-2 * 1024 * 1024:]
                # Hold incomplete lines: a credential split across pipe reads stays private.
                pending += text
                if "\n" in pending:
                    complete, _, pending = pending.rpartition("\n")
                    append(complete + "\n")
                if len(pending) > 2 * 1024 * 1024:
                    pending = "[출력이 너무 긴 행 생략]"
                    record["log_truncated"] = True
            append(pending + decoder.decode(b"", final=True))
            code = process.wait()
        record["process_id"] = None
        return code, captured

    def _save_preparation(self, record: dict, code: int, output: str) -> bool:
        def scrub(value):
            if isinstance(value, str):
                return self._redact(value, record)
            if isinstance(value, dict):
                return {key: scrub(item) for key, item in value.items()}
            if isinstance(value, list):
                return [scrub(item) for item in value]
            return value
        try:
            preparation = UpdatePreparation.model_validate(scrub(json.loads(output)))
        except (ValueError, ValidationError):
            raise ExecutionError("PREPARATION_FAILED", "준비 점검 응답을 읽을 수 없습니다. 실행 로그를 확인하세요") from None
        record["preparation"] = preparation.model_dump(mode="json")
        ready = code == 0 and preparation.ready
        if not ready:
            record.update(status="blocked", exit_code=code, error="준비 조건을 확인한 뒤 다시 준비 점검하세요", finished_at=_now())
        return ready

    def _work(self, record: dict, execute: bool, lease) -> None:
        try:
            self._check_registered(record)
            if self._fingerprint(self._snapshot_request(record)) != record["input_sha256"]:
                raise ExecutionError("INPUT_CHANGED", "보존한 입력이 바뀌었습니다. 다시 준비 점검하세요")
            # Recheck the host immediately before an explicit execution, without launching anything.
            code, output = self._invoke(record, prepare=True)
            if self._save_preparation(record, code, output):
                if execute:
                    self._check_registered(record)
                    if (self._fingerprint(LegacyUpdateRunRequest.model_validate(record["file_inputs"])) != record["input_sha256"]
                            or self._fingerprint(self._snapshot_request(record)) != record["input_sha256"]):
                        raise ExecutionError("INPUT_CHANGED", "준비 점검 후 입력이 바뀌었습니다. 다시 준비 점검하세요")
                    code, _ = self._invoke(record, prepare=False)
                    entries = ReportDirUpdateSource(self.root / "reports" / record["id"]).reports()
                    record["report_ids"] = [e.candidate["report_ref"]["id"] for e in entries
                                            if not self.contracts.errors("verification-result", e.candidate)]
                    record.update(status="completed" if code == 0 and record["report_ids"] else "failed",
                                  exit_code=code, finished_at=_now())
                    if record["status"] == "failed":
                        record["error"] = "검증 실행에 오류가 있습니다. 실행 로그와 생성된 보고서를 확인하세요"
                else:
                    record.update(status="ready", exit_code=code, finished_at=_now())
        except ExecutionError as error:
            record.update(status="failed", error=error.message, finished_at=_now())
        except Exception:
            # No exception text: parser/subprocess errors can include file contents or secrets.
            record.update(status="failed", error="검증 명령을 처리하지 못했습니다. 입력·update 설치와 실행 로그를 확인하세요", finished_at=_now())
        finally:
            try:
                self._save(record)
            finally:
                lease.__exit__(None, None, None)
