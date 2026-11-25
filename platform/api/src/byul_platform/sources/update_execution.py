"""Run the existing guard CLI without a shell; keep preparation and execution distinct.

Only explicit POSTs start work. Direct inputs are frozen before preparation, and
rechecked before execution. Relative paths inside TOML keep guard's update-dir cwd.
The lock covers both preparation and execution, including other platform workers.
"""

from __future__ import annotations

import codecs
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, contextmanager
from dataclasses import replace
from datetime import UTC, datetime
import fcntl
from functools import cached_property
import hashlib
import json
import os
from pathlib import Path
import re
from selectors import DefaultSelector, EVENT_READ
import sqlite3
import subprocess
import threading
import tomllib
from typing import Any, Iterator
from uuid import uuid4

from pydantic import ValidationError

from ..config import Settings
from ..contracts import Contracts, canonical_sha256
from ..store import DocumentStore
from ..update_execution_models import (
    LegacyUpdateRunRequest, PreparationGuidance, PreparationIssue, UpdatePreparation, UpdateRun, UpdateRunRequest,
)
from .registered_update_inputs import RegisteredInputError, RegisteredUpdateInputs
from .update import ReportDirUpdateSource, ReportEntry, UpdateSource, report_content_sha256

LOG_LIMIT = 64 * 1024
_ID = re.compile(r"^RUN-[a-f0-9]{32}$")


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ExecutionError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def _bound_run_report(entry: ReportEntry, objects: Path) -> ReportEntry:
    """A generated RPT belongs to the stored attempt and PLAN for its output directory."""
    if entry.path is None or "report_ref" not in entry.candidate:
        return entry
    path = Path(entry.path)
    attempt_file = path.with_name("attempt.txt")
    if entry.execution_attempt_id is None and entry.as_of is None and not attempt_file.exists():
        return entry  # Legacy runs had no durable attempt marker.
    invalid = ReportEntry({"_report": path.name, "_error": "RPT_ATTEMPT_INVALID"}, None, entry.path)
    try:
        from guard.models import Ref
        from guard.store.integrity import IntegrityError
        from guard.store.sqlite import Store
    except ImportError:
        return invalid
    try:
        attempt = attempt_file.read_text(encoding="utf-8").strip() if attempt_file.exists() else None
        if attempt == "" or entry.execution_attempt_id != attempt:
            return invalid
        run_id = path.relative_to(objects.parent / "reports").parts[0]
        if not _ID.fullmatch(run_id):
            return invalid
        inputs = objects.parent / run_id / "inputs"
        env_names = ("baseline", "candidate") if entry.candidate["mode"] == "update" else ("environment",)
        expected_refs = []
        for name in ("policy", *env_names):
            frozen = json.loads((inputs / f"{name}.json").read_bytes())
            if report_content_sha256(frozen) != frozen["content_sha256"]:
                return invalid
            expected_refs.append(Ref(kind=frozen["kind"], id=frozen["id"], revision=frozen["revision"],
                                     sha256=frozen["content_sha256"]))
        store = Store.read_only(objects)
        plan_ref = Ref.model_validate(entry.candidate["plan_ref"])
        if plan_ref.kind != "PLAN" or entry.candidate["plan_ref"] not in entry.candidate["inputs_manifest"]:
            return invalid
        plan = store.resolve(plan_ref)
        saved = store.resolve(Ref.model_validate(entry.candidate["report_ref"]))
        if (saved.execution_attempt_id != attempt or plan.ref() not in saved.refs
                or plan.inputs.policy != expected_refs[0] or plan.inputs.environments != expected_refs[1:]):
            return invalid
        store.validate_object(saved)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, sqlite3.Error, IntegrityError):
        return invalid
    return entry


class ExecutionUpdateSource:
    """Use real local RPTs after the first run; never label example reports as live."""

    def __init__(self, base: UpdateSource, root: Path, contracts: Contracts):
        self.base = base
        self.generated = ReportDirUpdateSource(root, root.parent / "objects" / "store.sqlite")
        self.contracts = contracts

    def _has_reports(self) -> bool:
        return any(self.contracts.is_valid("verification-result", entry.candidate)
                   for entry in self._generated_reports())

    def _generated_reports(self) -> Iterator[ReportEntry]:
        for entry in self.generated.iter_reports():
            yield _bound_run_report(entry, self.generated.dir.parent / "objects")

    @property
    def mode(self):
        return "live" if self._has_reports() else self.base.mode

    @property
    def detail(self):
        return self.source_info()["detail"]

    def source_info(self, *, has_generated: bool | None = None) -> dict[str, Any]:
        if has_generated is None:
            has_generated = self._has_reports()
        mode, detail = self.base.mode, self.base.detail
        if has_generated:
            detail = str(self.generated.dir) if mode == "example" else f"{detail}; {self.generated.dir}"
            mode = "live"
        return {"name": "update", "mode": mode, "detail": detail}

    def snapshot(self) -> tuple[list[ReportEntry], dict[str, Any]]:
        generated = list(self._generated_reports())
        has_generated = any(self.contracts.is_valid("verification-result", entry.candidate) for entry in generated)
        entries = [self._with_policy_source(entry) for entry in generated]
        if not has_generated or self.base.mode != "example":
            entries.extend(self.base.reports())
        return entries, self.source_info(has_generated=has_generated)

    def reports(self) -> list[ReportEntry]:
        return self.snapshot()[0]

    def _with_policy_source(self, entry: ReportEntry) -> ReportEntry:
        if entry.path is None or "report_ref" not in entry.candidate:
            return entry
        try:
            run_id = Path(entry.path).relative_to(self.generated.dir).parts[0]
            if not _ID.fullmatch(run_id):
                return entry
            run_dir = self.generated.dir.parent / run_id
            record = json.loads((run_dir / "run.json").read_bytes())
            files = run_dir / "inputs"
            policy = json.loads((files / "policy.json").read_bytes())
            source = json.loads((files / "source-policy.json").read_bytes())
            mapping = json.loads((files / "provenance.json").read_bytes())["import"]
            registered = record["source_refs"]["policy"]
            original = mapping["byul_policy_ref"]
            sha = canonical_sha256(source)
            guard_ref = {"kind": policy["kind"], "id": policy["id"], "revision": policy["revision"],
                         "sha256": policy["content_sha256"]}
            if (guard_ref["kind"] != "POL" or guard_ref != entry.candidate["policy_ref"]
                    or guard_ref != mapping["guard_policy_ref"] or report_content_sha256(policy) != guard_ref["sha256"]
                    or policy.get("source_document_sha256") != sha or registered["sha256"] != sha
                    or registered["id"] != source["policy_id"]
                    or original != {"policy_id": source["policy_id"], "policy_version": source["policy_version"], "sha256": sha}):
                return entry
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            return entry
        return replace(entry, candidate={**entry.candidate, "byul_policy_ref": original})


class UpdateExecutionService:
    def __init__(self, settings: Settings, root: Path, contracts: Contracts, environments: DocumentStore, policies: DocumentStore):
        self.settings, self.root, self.contracts = settings, root.expanduser().resolve(), contracts
        self.update_dir = settings.update_dir.expanduser().resolve()
        self.python = (settings.update_python or self.update_dir / ".venv/bin/python").expanduser().absolute()
        self.registered_inputs = RegisteredUpdateInputs(settings, contracts, environments, policies)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="byul-update")
        self._mutex = threading.Lock()
        self._recovery_pending = True
        self._recover_if_unowned()

    def _recover_if_unowned(self, *, has_active_record: bool = False) -> None:
        if not self._recovery_pending and not has_active_record:
            return
        try:
            with self._exclusive():
                self._recover_records()
        except ExecutionError:
            pass  # Another worker owns the active operation.

    def _recover_records(self) -> None:
        for record in self._records():
            if record["status"] not in {"preparing", "running"}:
                continue
            if record["status"] == "running" and record.get("last_process_phase") == "execute":
                self._record_reports(record, record["last_process_exit_code"])
            else:
                self._collect_report_refs(record)
                record.update(status="interrupted", finished_at=_now(),
                              error="플랫폼 실행이 중단됐습니다. 기존 검증 프로세스와 자원 정리 상태를 확인하세요")
            self._save(record)
        self._recovery_pending = False

    @property
    def available(self) -> bool:
        return self.unavailable_reason() is None

    def unavailable_reason(self) -> str | None:
        if not self.update_dir.is_dir() or not self.python.is_file() or not os.access(self.python, os.X_OK):
            return "서버의 검증 실행 환경이 준비되지 않았습니다. 관리자에게 문의하세요"
        reason = self.registered_inputs.unavailable_reason()
        if reason:
            return reason
        if not self._child_guard_matches_source:
            return "검증 실행용 Python의 Guard 설치가 서버 소스와 다릅니다. 설치를 확인하고 서버를 다시 시작하세요"
        return None

    @cached_property
    def _child_guard_matches_source(self) -> bool:
        # This is a package lookup, not CLI execution or a host readiness check.
        source_cli = self.update_dir / "src/guard/cli.py"
        source_package = source_cli.parent
        if not source_cli.is_file():
            return False
        probe = (
            "from importlib.util import find_spec; import json, sys; "
            "spec = find_spec('guard.cli'); "
            "print(json.dumps([list(sys.version_info[:2]), spec.origin if spec else None]))"
        )
        try:
            result = subprocess.run([str(self.python), "-B", "-c", probe], cwd=self.update_dir,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=True)
            version, origin = json.loads(result.stdout)
            installed = Path(origin).resolve().parent
            if version != [3, 12]:
                return False
            if installed == source_package.resolve():
                return True
            return all((installed / path.relative_to(source_package)).read_bytes() == path.read_bytes()
                       for path in source_package.rglob("*") if path.is_file()
                       and "__pycache__" not in path.parts and path.suffix != ".pyc")
        except (OSError, TypeError, ValueError, subprocess.SubprocessError):
            return False

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
        if record["status"] in {"preparing", "running"}:
            self._recover_if_unowned(has_active_record=True)
            record = self._load(run_id)
        if include_log:
            try:
                record["log_tail"] = (self._folder(run_id) / "log.txt").read_text(encoding="utf-8")
            except OSError:
                pass
        return UpdateRun.model_validate(record)

    def list(self) -> list[UpdateRun]:
        records = self._records()
        if any(row["status"] in {"preparing", "running"} for row in records):
            self._recover_if_unowned(has_active_record=True)
            records = self._records()
        return [UpdateRun.model_validate(row) for row in records[:50]]

    @contextmanager
    def _exclusive(self) -> Iterator[int]:
        if not self._mutex.acquire(blocking=False):
            raise ExecutionError("UPDATE_BUSY", "다른 검증 작업이 진행 중입니다", 409)
        try:
            with (self.root / ".execution.lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ExecutionError("UPDATE_BUSY", "다른 검증 작업이 진행 중입니다", 409) from None
                # Closing our descriptor leaves the lock held by a surviving CLI child.
                yield lock.fileno()
        finally:
            self._mutex.release()

    def _claim(self) -> tuple[AbstractContextManager, int]:
        """Transfer the exclusive lock to a worker until the CLI and cleanup end."""
        lease = self._exclusive()
        lock_fd = lease.__enter__()
        try:
            if self._recovery_pending:
                self._recover_records()
            for row in self._records():
                pid = row.get("process_id")
                if pid and not row.get("lease_inherited"):
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        continue
                    except PermissionError:
                        pass
                    raise ExecutionError("UPDATE_BUSY", "이전 검증 프로세스가 남아 있습니다. 실행·정리 상태를 확인하세요", 409)
        except BaseException:
            lease.__exit__(None, None, None)
            raise
        return lease, lock_fd

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
        from guard.configuration import requested_input_paths

        digest = hashlib.sha256()
        config = None
        try:
            for name, source in self._inputs(request).items():
                if not source.is_file():
                    raise ExecutionError("INPUT_FILES_INVALID", "입력은 서버에서 읽을 수 있는 일반 파일이어야 합니다")
                data = source.read_bytes()
                if name == "config.toml":
                    config = tomllib.loads(data.decode("utf-8"))
                digest.update(name.encode() + b"\0" + hashlib.sha256(data).digest())
                if destination:
                    path = destination / name
                    if source == path:
                        # Registered inputs already live in the run snapshot.
                        continue
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    path.write_bytes(data)
                    path.chmod(0o600)
            if config is not None:
                path_digests = {}
                for field, path, deferred in requested_input_paths(config, base_dir=self.update_dir):
                    if deferred:
                        continue
                    path = (self.update_dir / path).resolve()
                    if path not in path_digests:
                        if path.is_file():
                            with path.open("rb") as stream:
                                path_digests[path] = hashlib.file_digest(stream, "sha256").digest()
                        else:
                            path_digests[path] = b"missing"
                    digest.update(json.dumps(field, ensure_ascii=False).encode() + b"\0" + path_digests[path])
        except (OSError, ValueError, RuntimeError):
            raise ExecutionError("INPUT_FILES_INVALID", "입력 파일을 읽을 수 없습니다. 서버의 파일 경로·권한을 확인하세요") from None
        return digest.hexdigest()

    def prepare(self, request: UpdateRunRequest, actor: str) -> UpdateRun:
        if not self.available:
            raise ExecutionError("UPDATE_UNAVAILABLE", self.unavailable_reason(), 503)
        lease, lock_fd = self._claim()
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
                               requested_by=actor, input_sha256=fingerprint, source_refs=refs, pack=pack,
                               required_checks=list(self.settings.update_required_checks)).model_dump(mode="json")
            record["file_inputs"] = files.model_dump(mode="json")
            self._save(record)
            self._pool.submit(self._work, record, False, lease, lock_fd)
            return UpdateRun.model_validate(record)
        except BaseException:
            lease.__exit__(None, None, None)
            raise

    def execute(self, run_id: str, actor: str) -> UpdateRun:
        if not self.available:
            raise ExecutionError("UPDATE_UNAVAILABLE", "update Python 환경을 확인하세요", 503)
        lease, lock_fd = self._claim()
        try:
            record = self._load(run_id)
            if "file_inputs" not in record:
                raise ExecutionError("REGISTERED_INPUT_REQUIRED", "이전 파일 입력 기록입니다. 등록한 환경·정책으로 준비 점검을 다시 진행하세요", 409)
            if record["status"] != "ready" or not record.get("preparation", {}).get("ready"):
                raise ExecutionError("PREPARATION_REQUIRED", "준비 점검을 통과한 기록만 실행할 수 있습니다", 409)
            if tuple(record.get("required_checks", ())) != self.settings.update_required_checks:
                raise ExecutionError("PREPARATION_CHANGED", "필수 점검 범위가 바뀌었습니다. 다시 준비 점검하세요", 409)
            self._check_registered(record)
            files = LegacyUpdateRunRequest.model_validate(record["file_inputs"])
            try:
                same_inputs = self._fingerprint(files) == record["input_sha256"]
            except ExecutionError as error:
                if error.code != "INPUT_FILES_INVALID":
                    raise
                same_inputs = False
            if not same_inputs:
                raise ExecutionError("INPUT_CHANGED", "준비 점검 후 입력 파일이 바뀌었습니다. 다시 준비 점검하세요", 409)
            record.update(status="running", started_at=_now(), finished_at=None, executed_by=actor,
                          error=None, exit_code=None)
            self._save(record)
            self._pool.submit(self._work, record, True, lease, lock_fd)
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

    def _invoke(self, record: dict, lock_fd: int, *, prepare: bool) -> tuple[int, str]:
        command = self._command(record, prepare)
        path = self._folder(record["id"]) / "log.txt"
        # Preparation JSON is small; only the log tail is retained for an actual run.
        captured = bytearray()
        max_preparation_bytes = 2 * 1024 * 1024
        try:
            tail = path.read_text(encoding="utf-8")
        except OSError:
            tail = ""

        def append(text: str):
            nonlocal tail
            if not text:
                return
            safe = (tail + self._redact(text, record)).encode("utf-8")
            record["log_truncated"] = record["log_truncated"] or len(safe) > LOG_LIMIT
            tail = safe[-LOG_LIMIT:].decode("utf-8", errors="ignore")
            temporary = path.with_suffix(".tmp")
            with temporary.open("w", encoding="utf-8") as stream:
                os.chmod(temporary, 0o600)
                stream.write(tail)
            temporary.replace(path)

        append("\n[준비 점검]\n" if prepare else "\n[검증 실행]\n")
        phase = "prepare" if prepare else "execute"
        record.update(lease_inherited=True, last_process_phase=None, last_process_exit_code=None)
        self._save(record)
        with subprocess.Popen(command, cwd=self.update_dir, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE if prepare else subprocess.STDOUT,
                              pass_fds=(lock_fd,)) as process:
            assert process.stdout is not None
            streams = (process.stdout, process.stderr) if process.stderr else (process.stdout,)
            with DefaultSelector() as selector:
                for stream in streams:
                    selector.register(stream, EVENT_READ, (codecs.getincrementaldecoder("utf-8")("replace"), ""))
                while selector.get_map():
                    for key, _ in selector.select():
                        decoder, pending = key.data
                        chunk = key.fileobj.read1(4096)
                        if not chunk:
                            append(pending + decoder.decode(b"", final=True))
                            selector.unregister(key.fileobj)
                            continue
                        if prepare and key.fileobj is process.stdout and len(captured) <= max_preparation_bytes:
                            captured.extend(chunk[:max_preparation_bytes + 1 - len(captured)])
                        pending += decoder.decode(chunk)
                        if "\n" in pending:
                            complete, _, pending = pending.rpartition("\n")
                            append(complete + "\n")
                        if len(pending) > max_preparation_bytes:
                            pending = "[출력이 너무 긴 행 생략]"
                            record["log_truncated"] = True
                        selector.modify(key.fileobj, EVENT_READ, (decoder, pending))
            code = process.wait()
        record.update(process_id=None, last_process_phase=phase, last_process_exit_code=code)
        self._save(record)
        try:
            output = captured.decode("utf-8") if len(captured) <= max_preparation_bytes else ""
        except UnicodeDecodeError:
            output = ""
        return code, output

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
        if preparation.execution_ready is not None:
            preparation.ready = preparation.ready and preparation.execution_ready
        required = record.get("required_checks", ())
        if preparation.ready and required:
            wanted_sides = ("single",) if record["request"]["mode"] == "validate" else ("baseline", "candidate")
            for side in wanted_sides:
                for check_id in required:
                    matches = [r for r in preparation.check_support if r.get("side") == side and r.get("check_id") == check_id]
                    if len(matches) == 1 and matches[0].get("status") == "SUPPORTED":
                        continue
                    if preparation.guidance is None:
                        preparation.guidance = PreparationGuidance()
                    preparation.guidance.issues.append(PreparationIssue(
                        code="REQUIRED_SCOPE_UNSUPPORTED", side=side, check_id=check_id,
                        cause="필수 점검의 지원 상태가 확인되지 않았습니다",
                        action="해당 환경의 점검별 지원 근거와 필수 시연 범위를 확인하세요"))
                    preparation.ready = False
        record["preparation"] = preparation.model_dump(mode="json")
        ready = code == 0 and preparation.ready
        if not ready:
            record.update(status="blocked", exit_code=code, error="준비 조건을 확인한 뒤 다시 준비 점검하세요", finished_at=_now())
        return ready

    def _collect_report_refs(self, record: dict) -> None:
        directory = self.root / "reports" / record["id"]
        entries = (_bound_run_report(entry, self.root / "objects") for entry in ReportDirUpdateSource(directory).iter_reports())
        refs = [entry.candidate["report_ref"] for entry in entries if self.contracts.is_valid("verification-result", entry.candidate)]
        record["report_refs"] = refs
        record["report_ids"] = [ref["id"] for ref in refs]

    def _record_reports(self, record: dict, code: int) -> None:
        self._collect_report_refs(record)
        record.update(status="completed" if code == 0 and record["report_refs"] else "failed",
                      exit_code=code, finished_at=_now())
        if record["status"] == "failed":
            record["error"] = ("검증 실행에 오류가 있습니다. 실행 로그와 보존된 보고서를 확인하세요"
                               if record["report_refs"] else
                               "검증 실행에서 유효한 보고서를 받지 못했습니다. 실행 로그를 확인하세요")

    def _work(self, record: dict, execute: bool, lease, lock_fd: int) -> None:
        try:
            self._check_registered(record)
            if self._fingerprint(self._snapshot_request(record)) != record["input_sha256"]:
                raise ExecutionError("INPUT_CHANGED", "보존한 입력이 바뀌었습니다. 다시 준비 점검하세요")
            # Recheck the host immediately before an explicit execution, without launching anything.
            code, output = self._invoke(record, lock_fd, prepare=True)
            if self._save_preparation(record, code, output):
                if execute:
                    self._check_registered(record)
                    if (self._fingerprint(LegacyUpdateRunRequest.model_validate(record["file_inputs"])) != record["input_sha256"]
                            or self._fingerprint(self._snapshot_request(record)) != record["input_sha256"]):
                        raise ExecutionError("INPUT_CHANGED", "준비 점검 후 입력이 바뀌었습니다. 다시 준비 점검하세요")
                    code, _ = self._invoke(record, lock_fd, prepare=False)
                    self._record_reports(record, code)
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
