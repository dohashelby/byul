"""Owned Kubernetes reads restricted to a single record path per reader."""

from dataclasses import dataclass

from guard.runtime.bounded import capture_pipes
from .kubernetes_lifecycle_capture import KubernetesCaptureError, KubernetesLifecycleReader
from .owned_capture import MAX_BYTES, _FILE_READER


@dataclass(frozen=True, slots=True)
class FixedRecordProfile:
    path_template: str
    file_report_profile: str | None = None
    unverified_fields: tuple[str, ...] = ()
    program_error_code: str | None = None
    require_plain_argv: bool = False
    parent_cleanup_code: str | None = None


class FixedRecordReader(KubernetesLifecycleReader):
    def __init__(self, binding, target, session, *, profile, binary, duration, runner,
                 role=None, default_runner=capture_pipes):
        self._record_profile = profile
        self.role = role
        self.cleanup_errors = []
        read_runner = runner
        if profile.parent_cleanup_code is not None:
            read_runner = self._track_parent_cleanup(runner or default_runner, profile.parent_cleanup_code)
        self._initialize(binding, target, {"session": session}, binary=binary, duration=duration, runner=read_runner)
        self.injected = runner is not None

    def _track_parent_cleanup(self, runner, code):
        def bounded_runner(*args, **kwargs):
            result = runner(*args, **kwargs)
            pipe = result.get("capture", {})
            if pipe.get("pipes_closed") is False or "child_reap" in pipe.get("read_errors", []):
                if code not in self.cleanup_errors:
                    self.cleanup_errors.append(code)
                self.gap = True
            return result
        return bounded_runner

    def _record_path(self):
        return self._record_profile.path_template.format(session=self.session)

    def _program(self, container, argv, max_bytes):
        if (container != self.binding["engine"]["container"]
                or (self._record_profile.require_plain_argv and type(argv) is not list)
                or argv != ["python3", "-c", _FILE_READER, self._record_path(), str(MAX_BYTES)]
                or max_bytes != 2 * MAX_BYTES):
            if self._record_profile.program_error_code is not None:
                raise ValueError(self._record_profile.program_error_code)
            raise KubernetesCaptureError()
        return "file"

    def read_record(self, target):
        role = self.binding["engine"]
        return self.read_file(self, target, container=role["container"], image=role["image"], path=self._record_path())

    def report(self):
        result = super().report()
        profile = self._record_profile
        if profile.file_report_profile is not None:
            result["profile"] = profile.file_report_profile
            result["programs"] = {"file": result["programs"]["file"]}
        if self.role is not None:
            result["record_role"] = self.role
        if profile.parent_cleanup_code is not None:
            result["cleanup_errors"] = list(self.cleanup_errors)
        result.update(dict.fromkeys(profile.unverified_fields, False))
        return result
