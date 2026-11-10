"""Opaque reference for an observed Kubernetes container generation."""
import hashlib
import json
import re


def identity_scope_ref(context, namespace, pod_uid, container_id):
    values = [context, namespace, pod_uid, container_id]
    if any(not isinstance(value, str) or not value for value in values):
        return None
    cid = container_id.rsplit("://", 1)[-1]
    if not re.fullmatch(r"[0-9a-f]{64}", cid):
        return None
    values[-1] = cid
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
