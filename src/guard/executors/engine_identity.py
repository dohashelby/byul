"""Read only the managed fixed text fixture, retaining no raw inspect data.

This is a shared collector detail, not an engine-independent identity model.
The precise path, ownership and byte bounds deliberately match REQ-07.
"""
from __future__ import annotations

import json
import re
import time


_READ_TEMPLATE = """import hashlib,json,pathlib,sys
p=pathlib.Path(sys.argv[1]); size=p.stat().st_size
if p.is_symlink() or not p.is_file() or not 0<size<=8192: raise ValueError('unsupported template file')
b=p.read_bytes()
print(json.dumps({'sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b)}))
"""


def owned_template_snapshot(docker, ctx, identity, argv, deadline, *, monotonic=None):
    """Inspect one owned running container using the deadline owner's clock.

    A caller with an injected clock passes that same callable; production
    callers may use the default monotonic clock. Deadline epochs must match.
    """
    clock = time.monotonic if monotonic is None else monotonic
    try:
        container = ctx.target["engine"]["container"]
        path = identity.get("container_path")
        if path != "/guard-assets/chat/guard-text-chat-v1.jinja":
            return None
        remaining = deadline - clock()
        if remaining <= 0:
            return None
        inspected = docker.inspect_json(container, timeout=min(5, remaining))
        config = inspected.get("Config") or {}
        state = inspected.get("State") or {}
        labels = config.get("Labels") or {}
        readonly = any(m.get("Destination") == path and m.get("RW") is False
                       and m.get("Type") == "bind" for m in inspected.get("Mounts", []))
        if (not ctx.target.get("run_id") or labels.get("guard.run") != ctx.target["run_id"]
                or not state.get("Running") or config.get("Cmd") != argv or not readonly
                or config.get("Image") != ctx.env.engine.image_digest):
            return None
        remaining = deadline - clock()
        if remaining <= 0:
            return None
        result = docker.exec(container, ["python3", "-c", _READ_TEMPLATE, path], timeout=min(5, remaining))
        value = json.loads(result.stdout) if result.returncode == 0 else None
        if (type(value) is not dict or type(value.get("sha256")) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"])
                or type(value.get("bytes")) is not int or not 0 < value["bytes"] <= 8192):
            return None
        return {"container_id": inspected.get("Id"), "started_at": state.get("StartedAt"),
                "image": config["Image"], "selected_path": path,
                "readonly": True, "sha256": value["sha256"], "bytes": value["bytes"]}
    except Exception:
        return None
