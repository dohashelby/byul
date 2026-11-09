"""Canonical execution conditions. Addresses and resource names are evidence, not conditions."""
from urllib.parse import urlsplit
from guard.models import canonical_json, sha256_hex


def model_request_conditions(mapping):
    if type(mapping) is dict and mapping.get('schema') == 'guard.model-request-mapping/2':
        return {key: mapping[key] for key in ('schema', 'artifact', 'served_model_name', 'container_mount')}
    # ENV Ref and host address bind provenance, not baseline/candidate conditions.
    if type(mapping) is dict and mapping.get("schema")=="guard.model-request-mapping/1":
        return {key:mapping[key] for key in ("schema","repository_id","revision","inventory_sha256","served_model_name","container_mount")}
    return mapping


def execution_conditions(target: dict, settings: dict, roles: list[str], *, profile="execution-v2") -> dict:
    if profile not in {'execution-v1', 'execution-v2'}:
        raise ValueError('COMPARISON_PROFILE_UNSUPPORTED')
    groups = {}
    origins = {}
    for name, item in sorted(target.get("origins", {}).items()):
        network = item.get("network")
        origins[name] = groups.setdefault(network, len(groups))

    def endpoint(url):
        parsed = urlsplit(url or "")
        return {"scheme": parsed.scheme, "path": parsed.path, "port": parsed.port}

    return {
        "profile": profile,
        "topology": target.get("topology"),
        "engine_kind": target.get("engine_kind"),
        "engine_profile": target.get("engine_profile"),
        "gateway_profile": target.get("gateway_profile"),
        "probe_image": target.get("probe_image"),
        "origins": origins,
        "engine_endpoint": endpoint(target.get("engine", {}).get("trusted_url")),
        "gateway_endpoint": endpoint(target.get("gateway", {}).get("url")),
        "direct_origins": sorted(t["origin"] for t in target.get("direct_targets", [])),
        "direct_paths": sorted((t["origin"], urlsplit(t.get("url", "")).path)
                               for t in target.get("direct_targets", [])),
        "gateway_known_routes": sorted(target.get("gateway_known_routes", []), key=str),
        "launch": target.get("comparison_config"),
        "roles": sorted(roles),
        **({"model_mapping_sha256":sha256_hex(canonical_json(model_request_conditions(target["model_mapping"])))} if "model_mapping" in target else {}),
        "settings": {k: v for k, v in settings.items()
                     if k not in {"target", "container", "roles", "wrong_key_env", "allowed_key_env", "denied_key_env",
                                   *( {"constraint_engine_version", "constraint_source"} if profile == "execution-v2" else set())}},
    }
