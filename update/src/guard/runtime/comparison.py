"""Canonical execution conditions. Addresses and resource names are evidence, not conditions."""
from urllib.parse import urlsplit


def execution_conditions(target: dict, settings: dict, roles: list[str]) -> dict:
    groups = {}
    origins = {}
    for name, item in sorted(target.get("origins", {}).items()):
        network = item.get("network")
        origins[name] = groups.setdefault(network, len(groups))

    def endpoint(url):
        parsed = urlsplit(url or "")
        return {"scheme": parsed.scheme, "path": parsed.path, "port": parsed.port}

    return {
        "profile": "execution-v1",
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
        "settings": {k: v for k, v in settings.items()
                     if k not in {"target", "container", "roles", "wrong_key_env", "allowed_key_env", "denied_key_env"}},
    }
