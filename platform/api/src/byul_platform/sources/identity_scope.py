"""Same canonical generation reference as runtime's redacted timeline."""
import re
from ..contracts import canonical_sha256


def observation_scope_ref(record):
    identity, pod = record.get("identity"), record.get("pod")
    if not isinstance(identity, dict) or identity.get("state") != "confirmed" or not isinstance(pod, dict):
        return None
    values = [identity.get("context"), pod.get("namespace"), identity.get("pod_uid"), identity.get("container_id")]
    if any(not isinstance(value, str) or not value for value in values):
        return None
    values[-1] = values[-1].rsplit("://", 1)[-1]
    if not re.fullmatch(r"[0-9a-f]{64}", values[-1]):
        return None
    return canonical_sha256(values)
