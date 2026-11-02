"""PLT-06: core·worker 프로세스의 UID·namespace·공유 자원을 읽기 전용으로 지도화한다.

프로세스 이름 표식은 정책이 정한 신뢰 그룹을 가리킬 뿐 역할 인증이 아니다. 공유 경로는 해시로 남긴다.
같은 UID를 독립 경계로 가정하지 않으며, 편입된 worker의 실제 송신 영향은 이 수집으로 확인되지 않는다.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations
import re

from guard.models import sha256_hex
from .owned_helper import run_helper

PROFILE = "worker-trust-scope-v1"
VERSION = "0.2.0"
REQUIREMENTS = ("shared-trust-group", "independent-uid")
_MARKER = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_MAPPER = r'''
import json,os,re
rows=[]
for name in sorted(os.listdir('/proc')):
 if not name.isdigit():continue
 root='/proc/'+name
 try:
  comm=open(root+'/comm').read().strip()[:64]
  uid=None;gid=None
  for line in open(root+'/status'):
   if line.startswith('Uid:'):uid=line.split()[2]
   if line.startswith('Gid:'):gid=line.split()[2]
  ns={k:os.readlink(root+'/ns/'+k) for k in ('mnt','net','pid','ipc','user')}
  shared=set()
  for fd in os.listdir(root+'/fd')[:512]:
   try:link=os.readlink(root+'/fd/'+fd)
   except OSError:continue
   if link.startswith('/dev/shm/') or link.startswith('/memfd:') or re.match(r'/dev/(nvidia|dri|infiniband|kfd)',link) or link.startswith('socket:['):
    shared.add(link)
  rows.append(dict(pid=int(name),comm=comm,uid=uid,gid=gid,namespaces=ns,shared=sorted(shared),fd_complete=True))
 except PermissionError:
  rows.append(dict(pid=int(name),comm=None,uid=None,gid=None,namespaces=None,shared=[],fd_complete=False))
 except (FileNotFoundError,ProcessLookupError):continue
print(json.dumps(dict(processes=rows),sort_keys=True,separators=(',',':')))
'''


def validate_policy(values):
    if type(values) is not dict or values.get("worker_isolation_requirement") not in REQUIREMENTS:
        raise ValueError("worker_isolation_requirement must name a supported requirement")
    markers = values.get("worker_trust_group_markers")
    if (type(markers) is not list or not 1 <= len(markers) <= 8 or len(set(map(str, markers))) != len(markers)
            or any(type(m) is not str or not _MARKER.fullmatch(m) for m in markers)):
        raise ValueError("worker_trust_group_markers requires 1..8 distinct process markers")


def project(snapshot, values):
    """표식별 그룹과 경계 위반을 계산한다. 읽지 못한 프로세스는 범위 미완결로 남긴다."""
    groups, unreadable = defaultdict(list), 0
    for row in snapshot["processes"]:
        if not row["fd_complete"]:
            unreadable += 1
            continue
        for marker in values["worker_trust_group_markers"]:
            if marker in row["comm"]:
                groups[marker].append(row)
    independent = values["worker_isolation_requirement"] == "independent-uid"
    violations, shared = [], []
    for first, second in combinations(sorted(groups), 2):
        left, right = groups[first], groups[second]
        common_uid = {row["uid"] for row in left} & {row["uid"] for row in right}
        common = {item for row in left for item in row["shared"]} & {item for row in right for item in row["shared"]}
        if common:
            shared.append({"groups": [first, second], "resources_sha256": sorted(sha256_hex(i.encode()) for i in common)})
        if independent and common_uid:
            violations.append({"groups": [first, second], "shared_uid": sorted(common_uid)})
    mapping = [{"marker": marker, "pids": sorted(r["pid"] for r in rows), "uids": sorted({r["uid"] for r in rows}),
                "namespaces_sha256": sorted({sha256_hex(str(r["namespaces"]).encode()) for r in rows})}
               for marker, rows in sorted(groups.items())]
    missing = sorted(set(values["worker_trust_group_markers"]) - set(groups))
    return {"mapping": mapping, "violations": violations, "shared": shared, "missing": missing, "unreadable": unreadable}


def collect(ctx, docker, *, timeout=10):
    values = ctx.policy_values
    engine = ctx.target["engine"]["container"]
    snapshot, diagnostics = None, []
    try:
        snapshot = run_helper(docker, engine, _MAPPER, [], timeout=timeout, max_bytes=2 * 1024**2)
        projected = project(snapshot, values)
    except Exception:
        diagnostics.append("WORKER_TRUST_SCOPE_UNAVAILABLE")
        projected = None
    collected = projected is not None
    complete = bool(collected and not projected["missing"] and not projected["unreadable"] and projected["mapping"])
    obs = {"worker_trust_group_map": {"value": projected and projected["mapping"], "collected": collected},
           "worker_shared_resources": {"value": projected and projected["shared"], "collected": collected},
           "worker_isolation_violations": {"value": projected and projected["violations"], "collected": collected},
           "worker_trust_groups_missing": {"value": projected and projected["missing"], "collected": collected},
           "worker_trust_map_complete": {"value": complete, "collected": True},
           "worker_command_impact_verified": {"value": False, "collected": True}}
    summary = {"profile": PROFILE, "diagnostic_codes": diagnostics, "unreadable_processes": projected["unreadable"] if collected else None,
               "fixture": {"profile": PROFILE, "mapper_sha256": sha256_hex(_MAPPER.encode())},
               "not_exercised": ["worker_send_authority", "serialized_command_reader", "compensating_control_effect"]}
    normal = {"attempted": False, "succeeded": None, "detail": "실행 중 프로세스 지도만 수집했다. 시험 worker의 정상 편입은 수행하지 않았다."}
    return obs, normal, [{"step": step, "attempted": index == 0 and collected}
                         for index, step in enumerate(ctx.check.challenge_steps)], summary
