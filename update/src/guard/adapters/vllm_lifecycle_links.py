"""Declared opaque request links from the reviewed lifecycle profile only."""
from __future__ import annotations
import re


def _fingerprint(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def fingerprints(row):
    payload = row["payload"]
    if row["event"] in {"SG_NATIVE","SG_GPU_FENCE"}:
        values=payload.get("requests",[]);values=[v.get("request_sha256")for v in values]if row["event"]=="SG_NATIVE"else values
        return list(dict.fromkeys(v for v in [*values,payload.get("asgi_request_sha256")]if _fingerprint(v)))
    if row["event"] in {"ASGI","SG_ASGI"}:
        return [payload["request_sha256"]] if payload["request_sha256"] else []
    if row['event']=='TERMINATION':
        values=payload.get('request_sha256',[])
        values=list(values)if type(values)is list and all(_fingerprint(v)for v in values)else[]
        external=payload.get('asgi_request_sha256')
        if _fingerprint(external):values.append(external)
        return list(dict.fromkeys(values))
    if row["event"] == "NATIVE":
        values = [item["request_sha256"] for item in payload["requests"]]
        external = payload["metadata"].get("external_request_sha256")
        if _fingerprint(external):
            values.append(external)
        asgi = asgi_assignment(row)
        if asgi is not None:
            values.append(asgi)
        return list(dict.fromkeys(values))
    return []


def assignment(row):
    """CALL IDs may still be external; only the native RETURN supplies this pair."""
    payload = row["payload"]
    if row["event"]=="SG_NATIVE":
        values=payload.get("requests",[])
        if payload.get("point")=="TokenizerManager._init_req_state"and payload.get("phase")=="RETURN"and len(values)==1 and _fingerprint(payload.get("asgi_request_sha256"))and _fingerprint(values[0].get("request_sha256")):
            return payload["asgi_request_sha256"],values[0]["request_sha256"]
        return None
    if (row["event"] != "NATIVE" or payload["point"] != "InputProcessor.assign_request_id"
            or payload["phase"] != "RETURN" or len(payload["requests"]) != 1):
        return None
    external = payload["metadata"].get("external_request_sha256")
    if not _fingerprint(external):
        return None
    return external, payload["requests"][0]["request_sha256"]


def asgi_assignment(row):
    """An observed header-context association, never a unique HTTP identity."""
    if assignment(row) is None:
        return None
    value = row["payload"].get("asgi_request_sha256")if row["event"]=="SG_NATIVE"else row["payload"]["metadata"].get("asgi_request_sha256")
    return value if _fingerprint(value) else None
