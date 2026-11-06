"""Read-only dashboard views of node state and bounded trial evidence."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, field_validator
from .models import SourceInfo

Effect = Literal["confirmed", "failed", "unknown"]

class TetragonPolicy(BaseModel):
    node: str | None = None
    name: str
    namespace: str | None = None
    id: str
    state: str
    mode: Literal["monitor", "enforce", "monitor_only", "unknown"]
    method: str | None = None
    load_error: str | None = None

class TetragonCapability(BaseModel):
    node: str | None = None
    checked_at: str
    kernel: str | None = None
    apparmor: bool | None = None
    available: list[str] = Field(default_factory=list)
    unavailable: list[str] = Field(default_factory=list)
    unknown: list[str] = Field(default_factory=list)
    recommended: str | None = None
    tetragon_override_return: bool | None = None
    tetragon_lsm: bool | None = None
    tetragon_fmodret: bool | None = None

class TetragonModeState(BaseModel):
    id: str
    mode: str
    state: str

class TetragonTransition(BaseModel):
    at: str
    context: str
    namespace: str
    policy: str
    requested_mode: str
    ok: bool
    reason: str
    before: TetragonModeState | None = None
    after: TetragonModeState | None = None
    rollback: TetragonModeState | None = None
    rollback_ok: bool | None = None

class TetragonEffects(BaseModel):
    expected_effect: Literal["file_read_denial", "process_termination"]
    file_read_denial: Effect
    file_denial_basis: Literal["userspace_errno", "kernel_permission_return"] | None = None
    process_termination: Effect
    service_recovery: Effect
    monitor_restored: bool
    status: Literal["passed", "failed", "unknown", "unsupported"]

class TetragonKernelTask(BaseModel):
    pid: int
    start_ns: str
    pidns: int

    @field_validator("start_ns", mode="before")
    @classmethod
    def exact_nanoseconds(cls, value):
        # Preserve older integer reports without losing uint64 precision in JS.
        if type(value) is int and 0 < value < 2**64:
            return str(value)
        if isinstance(value, str) and value.isascii() and value.isdecimal() and 0 < int(value) < 2**64:
            return value
        raise ValueError("invalid kernel start time")

class TetragonTaskIdentity(BaseModel):
    pod_uid: str
    namespace: str
    container_id: str
    node_pid: int
    ns_pid: int
    pidns: int
    start_ticks: int
    clock_ticks: int

class TetragonProbeEvent(BaseModel):
    time: str
    policy: str
    pod_uid: str
    path: str | None = None
    access_mask: int | None = None
    pid: int | None = None
    exec_id: str | None = None
    ns_pid: int | None = None
    function: str | None = None
    action: str | None = None
    return_value: int | None = None
    identity_source: Literal["kernel_task", "tetragon_process_cache"] | None = None
    kernel_task: TetragonKernelTask | None = None

class TetragonTermination(BaseModel):
    exitCode: int | None = None
    signal: int | None = None
    reason: str | None = None
    finishedAt: str | None = None

class TetragonLoad(BaseModel):
    state: str | None = None
    id: str | None = None
    error: str | None = None

class TetragonReadResult(BaseModel):
    read: Literal["ok", "denied"] | None = None
    errno: int | None = None
    pid: int | None = None
    transport_error: str | None = None

class TetragonAction(BaseModel):
    action: str
    argError: int | None = None

class TetragonTrialStep(BaseModel):
    at: str
    target_mode: str
    ok: bool
    reason: str
    before: TetragonModeState | None = None
    after: TetragonModeState | None = None
    rollback: TetragonModeState | None = None
    rollback_ok: bool | None = None

class TetragonAgentSample(BaseModel):
    pod_uid: str
    restart_count: int

class TetragonVerification(BaseModel):
    id: str
    context: str
    node: str | None = None
    kernel: str | None = None
    namespace: str
    policy: str
    probe_path: str | None = None
    method: str
    variant: Literal["combined", "override_only_diagnostic"] = "combined"
    data_origin: Literal["real", "synthetic"]
    started_at: str
    finished_at: str
    pod_uid: str | None = None
    main_pid: int | None = None
    task_identity: TetragonTaskIdentity | None = None
    identity_probe_data: list[dict[str, int | str]] = Field(default_factory=list)
    policy_id: str | None = None
    verification: TetragonEffects
    facts: dict[str, bool | None] = Field(default_factory=dict)
    events: list[TetragonProbeEvent] = Field(default_factory=list)
    monitor_events: list[TetragonProbeEvent] = Field(default_factory=list)
    steps: list[TetragonTrialStep] = Field(default_factory=list)
    policy_actions: list[TetragonAction] = Field(default_factory=list)
    policy_spec_sha256: str | None = None
    manifest_sha256: str | None = None
    raw_event_sha256: str | None = None
    source_report_sha256: str | None = None
    reassessed_at: str | None = None
    reassessment_note: str | None = None
    read_result: TetragonReadResult | None = None
    monitor_read_result: TetragonReadResult | None = None
    restore_read_result: TetragonReadResult | None = None
    agent_before: TetragonAgentSample | None = None
    agent_after: TetragonAgentSample | None = None
    termination: TetragonTermination | None = None
    load: TetragonLoad | None = None
    error: str | None = None

class TetragonAgent(BaseModel):
    node: str | None = None
    restart_count: int | None = None
    last_exit_at: str | None = None
    last_exit_code: int | None = None
    last_exit_reason: str | None = None
    last_exit_summary: str | None = None

class TetragonSnapshot(BaseModel):
    schema_version: Literal["runtime-tetragon-state/1.0"]
    state: Literal["ok", "stale", "error", "not_configured"]
    fresh: bool
    generated_at: str | None = None
    context: str | None = None
    error: str | None = None
    policies: list[TetragonPolicy] = Field(default_factory=list)
    capabilities: list[TetragonCapability] = Field(default_factory=list)
    transitions: list[TetragonTransition] = Field(default_factory=list)
    verifications: list[TetragonVerification] = Field(default_factory=list)
    agents: list[TetragonAgent] = Field(default_factory=list)

class TetragonStatusResponse(BaseModel):
    item: TetragonSnapshot
    source: SourceInfo
