"""OBS-04·OBS-05: 사용 통계 보고의 실제 생성·수신을 정상 추론 전후로 관측한다.

엔진 안의 보고 파일과 명시한 소유 수신지 기록만 본다. 파일 내용은 JSON 키 이름과 해시만 남긴다.
timeout이나 수신 실패만으로 비전송을 주장하지 않는다. 수신 기록이 없으면 송신 센서 없음으로 남긴다.
"""
from __future__ import annotations

import re
import secrets
import time
from typing import NamedTuple

from guard.models import canonical_json, sha256_hex
from guard.runtime.bounded import strict_json
from guard.runtime.docker import Docker
from .boundary_egress import BoundaryEgressExecutor
from .content_common import bounded_probe, control_state
from .http_common import HttpCheckExecutor, functional_requests
from .owned_capture import _read_owned_file
from .owned_helper import run_helper

SILENCE_PROFILE = "usage-report-silence-v1"
SCOPE_PROFILE = "usage-report-scope-v1"
PROFILES = frozenset({SILENCE_PROFILE, SCOPE_PROFILE})
VERSION = "0.1.0"
SETTINGS = ("VLLM_NO_USAGE_STATS", "DO_NOT_TRACK", "VLLM_USAGE_STATS_SERVER")
RECEIPT_SCHEMA = "guard-usage-sink-receipt/1"
START_SCHEMA = "guard-usage-sink-start/1"  # 빈 수신 기록도 읽을 수 있도록 첫 행에 둔다
_PATH = re.compile(r"/[A-Za-z0-9._/-]{1,255}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SETTINGS_READER = r'''env={}
with open('/proc/1/environ','rb') as stream:
 for item in stream.read(1048576).split(b'\0'):
  key,sep,value=item.partition(b'=')
  if sep and key.decode('utf-8','replace') in names:env[key.decode()]=value.decode('utf-8','replace')[:255]
stat=open('/proc/1/stat').read();start=int(stat.rsplit(')',1)[1].split()[19])
print(json.dumps(dict(files=rows,settings=env,process_start_ticks=start),sort_keys=True,separators=(',',':')))
'''
_READER = r'''
import hashlib,json,os,sys
names=sys.argv[1].split(',');paths=sys.argv[2:]
def fields(raw):
 out=set()
 for line in raw.splitlines()[-64:]:
  try:value=json.loads(line)
  except ValueError:continue
  if type(value) is dict:out.update(k for k in value if type(k) is str and len(k)<=64)
 return sorted(out)
rows=[]
for path in paths:
 try:
  fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 except FileNotFoundError:
  rows.append(dict(path=path,exists=False,readable=True));continue
 except OSError:
  rows.append(dict(path=path,exists=None,readable=False));continue
 try:
  raw=os.read(fd,4194305)
  rows.append(dict(path=path,exists=True,readable=True,bytes=len(raw),truncated=len(raw)>4194304,
   sha256=hashlib.sha256(raw).hexdigest(),lines=raw.count(b'\n'),fields=fields(raw)))
 finally:os.close(fd)
''' + _SETTINGS_READER
_SCOPE_READER = r'''
import hashlib,json,os,sys
from stat import S_ISREG

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

def reject_constant(_value):
    raise ValueError

def appended_fields(raw, prefix, generation):
    if prefix is None:
        return None
    size, expected, previous_generation = prefix
    if (size >= len(raw) or size and raw[size-1:size] != b'\n'
            or previous_generation not in (None, generation)
            or not raw.endswith(b'\n') or digest(raw[:size]) != expected):
        return None
    lines = raw[size:-1].split(b'\n')
    if len(lines) > 64:
        return None
    fields = set()
    try:
        for line in lines:
            row = json.loads(line.decode('utf-8'), parse_constant=reject_constant)
            if type(row) is not dict or any(not 0 < len(key.encode('utf-8')) <= 64 for key in row):
                return None
            fields.update(row)
    except (ValueError, UnicodeError, RecursionError):
        return None
    if len(fields) <= 64:
        return dict(prefix_bytes=size,prefix_sha256=expected,fields=sorted(fields))
    return None

def snapshot(path, prefix):
    try:
        fd = os.open(path, os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:
        return dict(path=path,exists=False,readable=True)
    except OSError:
        return dict(path=path,exists=None,readable=False)
    try:
        with os.fdopen(fd,'rb') as stream:
            before = os.fstat(stream.fileno())
            if not S_ISREG(before.st_mode):
                return dict(path=path,exists=None,readable=False)
            if before.st_size > 4194304:
                return dict(path=path,exists=True,readable=True,truncated=True)
            raw = stream.read(4194305)
            after = os.fstat(stream.fileno())
            current = os.stat(path, follow_symlinks=False)
        if len(raw) == before.st_size and identity(before) == identity(after) == identity(current):
            generation = digest(f'{before.st_dev}:{before.st_ino}'.encode('ascii'))
            return dict(path=path,exists=True,readable=True,truncated=False,bytes=len(raw),
                        sha256=digest(raw),lines=raw.count(b'\n'),generation_sha256=generation,
                        append=appended_fields(raw,prefix,generation))
    except OSError:
        pass
    return dict(path=path,exists=None,readable=False)

names = sys.argv[1].split(',')
prefixes = json.loads(sys.argv[2])
rows = [snapshot(path,prefixes.get(path)) for path in sys.argv[3:]]
''' + _SETTINGS_READER


def validate_policy(check, values):
    if type(values) is not dict or type(values.get("usage_allowed")) is not bool:
        raise ValueError("usage_allowed must be an explicit boolean")
    paths = values.get("usage_report_paths")
    if (type(paths) is not list or not 1 <= len(paths) <= 8 or len(set(map(str, paths))) != len(paths)
            or any(type(p) is not str or not _PATH.fullmatch(p) or ".." in p for p in paths)):
        raise ValueError("usage_report_paths requires 1..8 distinct absolute paths")
    if type(values.get("usage_settings_fresh_process")) is not bool:
        raise ValueError("usage_settings_fresh_process must be an explicit boolean")
    if check.collection_profile == SCOPE_PROFILE:
        for key in ("allowed_usage_fields", "allowed_usage_sinks"):
            value = values.get(key)
            if type(value) is not list or any(type(item) is not str or not item for item in value):
                raise ValueError(key + " must be an explicit list")


def receipts(rows, *, ordered=False):
    """닫힌 형식의 소유 수신지 기록만 받는다. 원문 보고 본문은 기록되지 않는다."""
    previous_ns = 0
    for row in rows:
        if (type(row) is not dict or set(row) != {"schema", "received_monotonic_ns", "sink", "fields", "bytes"}
                or row["schema"] != RECEIPT_SCHEMA or type(row["received_monotonic_ns"]) is not int
                or type(row["sink"]) is not str or type(row["bytes"]) is not int
                or type(row["fields"]) is not list or any(type(f) is not str for f in row["fields"])):
            raise ValueError("USAGE_SINK_RECORD_INVALID")
        if ordered and row["received_monotonic_ns"] < previous_ns:
            raise ValueError("USAGE_SINK_RECORD_INVALID")
        previous_ns = row["received_monotonic_ns"]
    return rows


class _SinkSnapshot(NamedTuple):
    rows: list
    raw: bytes
    generation: tuple


def _sink_snapshot(raw, owner, identity):
    lines = raw.split(b"\n")
    if lines[-1] == b"":
        lines.pop()
    if not 1 <= len(lines) <= 64 or any(not line.strip() or len(line) > 16384 for line in lines):
        raise ValueError("CAPTURE_JSONL_BOUND")
    rows = [strict_json(line, limit=16384) for line in lines]
    if any(type(row) is not dict for row in rows) or rows[0] != {"schema": START_SCHEMA}:
        raise ValueError("USAGE_SINK_RECORD_INVALID")
    return _SinkSnapshot(rows, raw, (owner, identity["device"], identity["inode"]))


def _snapshot_command(paths, prefixes):
    settings = ",".join(SETTINGS)
    if prefixes is None:
        return _READER, [settings, *paths]
    return _SCOPE_READER, [settings, canonical_json(prefixes).decode("utf-8"), *paths]


def _report_prefixes(snapshot, paths):
    prefixes = {}
    if snapshot is not None:
        for row in snapshot["files"]:
            if row["path"] not in paths or row["readable"] is not True:
                continue
            if row["exists"] is False:
                prefixes[row["path"]] = [0, sha256_hex(b""), None]
            elif (row["exists"] is True and row.get("truncated") is False and type(row.get("bytes")) is int
                  and 0 <= row["bytes"] <= 4194304 and type(row.get("sha256")) is str
                  and _SHA256.fullmatch(row["sha256"]) and type(row.get("generation_sha256")) is str
                  and _SHA256.fullmatch(row["generation_sha256"])):
                prefixes[row["path"]] = [row["bytes"], row["sha256"], row["generation_sha256"]]
    return prefixes


def new_reports(before, after, *, prefixes=None):
    """전후 파일 비교. 기존 파일 내용은 새 보고로 세지 않는다."""
    earlier = {row["path"]: row for row in before["files"]}
    rows, gaps = [], []
    for row in after["files"]:
        old = earlier.get(row["path"])
        if old is None or row.get("readable") is not True or old.get("readable") is not True or row.get("truncated"):
            gaps.append(row["path"])
            continue
        if prefixes is not None:
            prefix = prefixes.get(row["path"])
            generation = row.get("generation_sha256")
            if (prefix is None or old["exists"] and not row["exists"]
                    or row["exists"] and (type(generation) is not str or not _SHA256.fullmatch(generation)
                                          or prefix[2] not in (None, generation))):
                gaps.append(row["path"])
                continue
        if row["exists"] and (not old["exists"] or row["sha256"] != old["sha256"]):
            if prefixes is None:
                rows.append(row)
                continue
            size, digest, _ = prefix
            if size == row["bytes"] == 0 and row["sha256"] == digest:
                continue
            proof = row.get("append")
            if (type(proof) is not dict or proof.keys() != {"prefix_bytes", "prefix_sha256", "fields"}
                    or type(proof["prefix_bytes"]) is not int or proof["prefix_bytes"] != size
                    or proof["prefix_sha256"] != digest or type(row["bytes"]) is not int or row["bytes"] <= size
                    or type(proof["fields"]) is not list or len(proof["fields"]) > 64
                    or any(type(key) is not str or not 0 < len(key) <= 64 for key in proof["fields"])):
                gaps.append(row["path"])
                continue
            rows.append({**row, "fields": proof["fields"]})
    return rows, gaps


class UsageReportExecutor(HttpCheckExecutor):
    name = "boundary_egress"
    version = VERSION
    requires_roles = False
    requires_route_scope = False
    functional_via = "engine"

    def __init__(self, cfg, *args, docker=None, **kwargs):
        super().__init__(cfg, *args, **kwargs)
        self.docker = docker or Docker("usage" + secrets.token_hex(4))

    def validate_policy(self, check, values):
        validate_policy(check, values)
        wait = self.cfg.get("usage_wait_seconds", 5)
        if type(wait) not in (int, float) or not 0 <= wait <= 60:
            raise ValueError("usage_wait_seconds must be bounded")
        if "usage_sink" in self.cfg:
            sink = self.cfg["usage_sink"]
            if type(sink) is not dict or set(sink) != {"container", "image", "path"} or not all(type(v) is str and v for v in sink.values()):
                raise ValueError("usage_sink needs container, image and path")

    def _snapshot(self, ctx, *, prefixes=None):
        engine = ctx.target["engine"]
        program, args = _snapshot_command(ctx.policy_values["usage_report_paths"], prefixes)
        return run_helper(self.docker, engine["container"], program, args, timeout=10, max_bytes=262144)

    def _sink_rows(self, ctx):
        sink = self.cfg.get("usage_sink")
        if sink is None:
            return None
        raw, proof = _read_owned_file(self.docker, ctx.target, container=sink["container"],
                                      image=sink["image"], path=sink["path"])
        return _sink_snapshot(raw, proof["owner"], proof["file_identity"])

    def collect(self, ctx):
        self.validate_policy(ctx.check, ctx.policy_values)
        values = ctx.policy_values
        report_scope = ctx.check.collection_profile == SCOPE_PROFILE
        append_scope = report_scope and values["usage_allowed"] is True
        requests, probes = functional_requests(ctx, via="engine")
        if len(requests) != 1:
            raise ValueError("usage checks need one engine control")
        template, probe = requests[0], next(iter(probes.values()))
        expected = ctx.api_model or ctx.env.model.id
        diagnostics, before, after, controls = [], None, None, {}
        try:
            before = self._snapshot(ctx, prefixes={}) if append_scope else self._snapshot(ctx)
        except Exception:
            diagnostics.append("USAGE_REPORT_BASELINE_UNAVAILABLE")
        sink_before = None
        try:
            sink_before = self._sink_rows(ctx)
        except Exception:
            diagnostics.append("USAGE_SINK_BASELINE_UNAVAILABLE")
        result = bounded_probe(ctx, ctx.trusted_net, template)
        controls["inference"] = control_state(result, expected, probe.path)
        time.sleep(self.cfg.get("usage_wait_seconds", 5))
        try:
            prefixes = _report_prefixes(before, values["usage_report_paths"]) if append_scope else {}
            after = self._snapshot(ctx, prefixes=prefixes) if append_scope else self._snapshot(ctx)
        except Exception:
            diagnostics.append("USAGE_REPORT_AFTER_UNAVAILABLE")
        fresh, gaps = [], []
        if before is not None and after is not None:
            fresh, gaps = new_reports(before, after, prefixes=prefixes if append_scope else None)
            if report_scope and not append_scope:
                fresh = []
            if append_scope and gaps:
                diagnostics.append("USAGE_REPORT_APPEND_UNVERIFIED")
            if before["process_start_ticks"] != after["process_start_ticks"]:
                diagnostics.append("USAGE_ENGINE_PROCESS_CHANGED")
        arrived = None
        sensor = False
        try:
            sink_after = self._sink_rows(ctx)
            if sink_after is not None and sink_before is not None:
                seen = len(sink_before.rows)
                if append_scope:
                    unchanged_prefix = (sink_after.generation == sink_before.generation
                        and sink_before.raw.endswith(b"\n") and sink_after.raw.endswith(b"\n")
                        and sink_after.raw.startswith(sink_before.raw))
                else:
                    unchanged_prefix = sink_after.rows[:seen] == sink_before.rows
                if report_scope and not unchanged_prefix:
                    diagnostics.append("USAGE_SINK_PREFIX_CHANGED")
                else:
                    if append_scope:
                        # The owned writer appends in its own monotonic clock domain.
                        arrived = receipts(sink_after.rows[1:], ordered=True)[seen - 1:]
                    else:
                        arrived = receipts(sink_after.rows[seen:])
                    sensor = unchanged_prefix
        except Exception:
            diagnostics.append("USAGE_SINK_AFTER_UNAVAILABLE")
        forbidden = not values["usage_allowed"]
        local_seen = ((not report_scope or append_scope) and before is not None and after is not None
                      and (not gaps or append_scope and bool(fresh))
                      and "USAGE_ENGINE_PROCESS_CHANGED" not in diagnostics)
        report_rows = []
        for row in fresh:
            report = {"path_sha256": sha256_hex(row["path"].encode()), "bytes": row["bytes"], "lines": row["lines"],
                      "sha256": row["sha256"], "fields": row["fields"]}
            if report_scope:
                report.update(file_generation_sha256=row["generation_sha256"],
                              prefix_bytes=row["append"]["prefix_bytes"], prefix_sha256=row["append"]["prefix_sha256"])
            report_rows.append(report)
        settings = (after or before or {}).get("settings")
        obs = {"usage_policy_forbidden": {"value": forbidden, "collected": True},
               "usage_effective_settings": {"value": settings, "collected": settings is not None},
               "usage_settings_fresh_process": {"value": values["usage_settings_fresh_process"], "collected": True},
               "usage_new_local_reports": {"value": report_rows, "collected": local_seen},
               "usage_sink_receipts": {"value": arrived, "collected": sensor},
               "usage_send_sensor_available": {"value": sensor, "collected": True},
               "functional_probes_planned": {"value": ["inference"], "collected": True},
               "functional_probe_results": {"value": controls, "collected": True}}
        if report_scope:
            local_fields = {field for row in fresh for field in row["fields"]} if local_seen else set()
            received_fields = {field for row in arrived or [] for field in row["fields"]}
            sinks = sorted({row["sink"] for row in arrived or []})
            obs.update({"usage_fields_seen": {"value": sorted(local_fields | received_fields), "collected": local_seen or sensor},
                        "usage_sinks_seen": {"value": sinks, "collected": sensor},
                        "usage_report_observed": {"value": bool(arrived) if sensor else None, "collected": sensor}})
        normal = {"attempted": True, "succeeded": controls["inference"] == "pass",
                  "detail": "보고 전후 사이에 정상 추론 1회"}
        summary = {"profile": ctx.check.collection_profile, "diagnostic_codes": sorted(set(diagnostics)),
                   "unreadable_paths": [sha256_hex(path.encode()) for path in gaps], "controls": controls,
                   "fixture": {"profile": ctx.check.collection_profile,
                               "reader_sha256": sha256_hex((_SCOPE_READER if append_scope else _READER).encode()),
                               "control_sha256": sha256_hex(canonical_json(probe.model_dump(mode="json")))}}
        ctx._usage_gap = bool(diagnostics or gaps)
        return obs, normal, [{"step": step, "attempted": True} for step in ctx.check.challenge_steps], summary

    def _finish(self, ctx):
        from guard.models import ExecStatus
        draft = super()._finish(ctx)
        if ctx._usage_gap:
            draft.status = ExecStatus.ABORTED
        return draft


class BoundaryEgressDispatch(BoundaryEgressExecutor):
    """boundary_egress:real 진입점. boundary_egress.py bytes는 기존 고정값을 유지하므로 프로파일 분기를 여기 둔다."""

    def validate_policy(self, check, values):
        if getattr(check, "collection_profile", None) in PROFILES:
            validate_policy(check, values)

    def run(self, check, env, policy_values, adapter, workdir):
        if getattr(check, "collection_profile", None) not in PROFILES:
            return super().run(check, env, policy_values, adapter, workdir)
        executor = UsageReportExecutor(self.cfg, docker=self.docker)
        draft = executor.run(check, env, policy_values, adapter, workdir)
        self.version = executor.version
        return draft
