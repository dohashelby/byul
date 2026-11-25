"""Fixed benign exec probes, run directly by Python (never through a shell)."""

import time

PROBE = r"""
import datetime, errno, json, os, subprocess
start = datetime.datetime.now(datetime.timezone.utc).isoformat()
rows = []
for name, argv in [('absolute', ['/bin/sh', '-c', 'id']), ('path', ['sh', '-c', 'id']), ('bash', ['/usr/bin/bash', '-c', 'id']), ('allowed', ['/usr/bin/id'])]:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        rows.append(dict(name=name, returncode=result.returncode, stdout=result.stdout, stderr=result.stderr, errno=None))
    except OSError as error:
        rows.append(dict(name=name, returncode=None, errno=error.errno, error=str(error)))
rows.append(dict(name='os_system', returncode=os.system('id'), errno=None))
print(json.dumps(dict(pid=os.getpid(), started_at=start, finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat(), alive=True, cases=rows)))
"""

EXEMPT = r"""
import json, os, shutil, subprocess, sys
from pathlib import Path
path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
with path.open('xb') as target, open(sys.executable, 'rb') as source:
    shutil.copyfileobj(source, target)
os.chmod(path, 0o755)
env = dict(os.environ, PYTHONHOME=sys.base_prefix)
result = subprocess.run([str(path), '-c', 'import subprocess; raise SystemExit(subprocess.run(["/bin/sh", "-c", "id"]).returncode)'], env=env, capture_output=True, text=True, timeout=15)
print(json.dumps(dict(name='exempt', returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)))
"""


def probe_passed(result):
    cases = {row.get("name"): row for row in result.get("cases", [])}
    denied = all(cases.get(name, {}).get("errno") in {1, 13} for name in ("absolute", "path", "bash"))
    return bool(
        result.get("alive") is True
        and denied
        and type(cases.get("os_system", {}).get("returncode")) is int
        and cases["os_system"]["returncode"] != 0
        and cases.get("allowed", {}).get("returncode") == 0
    )


def bound_exec_events(lines, *, policy, pod_uid, start, end):
    from datetime import datetime
    import json

    left, right = datetime.fromisoformat(start), datetime.fromisoformat(end)
    matches = []
    for line in lines.splitlines():
        try:
            root = json.loads(line)
            event = root.get("process_lsm", {})
            process = event.get("process") or {}
            pod = process.get("pod") or {}
            stamp = datetime.fromisoformat(root["time"].replace("Z", "+00:00"))
            if (
                left <= stamp <= right
                and event.get("policy_name") == policy
                and event.get("function_name") == "bprm_check_security"
                and pod.get("uid") == pod_uid
                and event.get("action") == "KPROBE_ACTION_OVERRIDE"
            ):
                matches.append(root)
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    return matches


def safe_exec_events(events):
    return [
        {
            "time": root["time"],
            "policy": root["process_lsm"]["policy_name"],
            "function": "bprm_check_security",
            "action": root["process_lsm"]["action"],
            "pod_uid": root["process_lsm"]["process"]["pod"]["uid"],
            "path": root["process_lsm"]["args"][0]["linux_binprm_arg"]["path"],
        }
        for root in events
    ]


def pod_receipt(pod):
    return {
        "uid": pod["metadata"]["uid"],
        "containers": [
            {
                "name": c["name"],
                "id": c.get("containerID"),
                "restarts": c["restartCount"],
                "running": bool(c.get("state", {}).get("running")),
            }
            for c in pod["status"]["containerStatuses"]
        ],
    }


def wait_bound_events(fetch, binding, *, attempts=8):
    for attempt in range(attempts):
        events = bound_exec_events(fetch(), **binding)
        if events:
            return events
        if attempt + 1 < attempts:
            time.sleep(1)
    return []
