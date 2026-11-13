"""Versioned support descriptions; availability never certifies runtime evidence.

Source pins remain in reviewed engine implementations. Common orchestration
asks this registry instead of embedding engine release checks in transports.
New catalog entries cannot turn observed bytes into an approval.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EngineProfile:
    id: str
    engine: str
    versions: tuple[str, ...]
    features: frozenset[str]
    runtime_qualification_required: bool = True

    def matches(self, engine, version, feature):
        return (self.engine in ('*', engine) and (not self.versions or version in self.versions) and feature in self.features)


@dataclass(frozen=True)
class Support:
    available: bool
    profile: str | None
    reason: str
    runtime_qualified: bool = False


def _catalog():
    import json
    from pathlib import Path
    data = json.loads((Path(__file__).with_name('assets') / 'engine_profiles.json').read_text())
    if data.get('schema') != 'guard-engine-profile-catalog/1':
        raise ValueError('PROFILE_CATALOG_INVALID')
    profiles = tuple(EngineProfile(row['id'], row['engine'], tuple(row['versions']), frozenset(row['features']))
                     for row in data['profiles'])
    if len({p.id for p in profiles}) != len(profiles):
        raise ValueError('PROFILE_CATALOG_INVALID')
    return profiles


PROFILES = _catalog()

# Stable transport names describe a requested evidence feature, not an engine
# version. The catalog owns that feature's reviewed release availability.
TRANSPORT_FEATURES = {
    'kubernetes-owned-sglang-termination-http-v1': 'sglang-native-lifecycle-metadata/1',
    'kubernetes-owned-native-route-http-v1': 'native_route',
    'kubernetes-owned-validation-error-http-v1': 'validation_error',
    'kubernetes-owned-model-authorization-http-v1': 'model_authorization',
    'kubernetes-owned-online-input-http-v1': 'online_input',
    'kubernetes-owned-termination-http-v1': 'termination',
    'kubernetes-owned-request-attempts-http-v1': 'request_attempts',
    'kubernetes-owned-required-event-http-v1': 'required_events',
    'kubernetes-owned-observation-state-http-v1': 'observation_state',
    'kubernetes-owned-input-validation-http-v1': 'native_input',
}

TRANSPORT_FEATURES.update({'kubernetes-owned-sampling-http-v1': 'http_transport', 'kubernetes-owned-request-binding-http-v1': 'http_transport', 'kubernetes-owned-engine-callback-http-v1': 'native_lifecycle', 'kubernetes-owned-response-content-http-v1': 'http_transport', 'kubernetes-owned-log-integrity-http-v1': 'native_lifecycle', 'kubernetes-owned-usage-control-http-v1': 'native_lifecycle', 'kubernetes-owned-gateway-event-http-v1': 'http_transport'})


def resolve(engine: str, version: str | None, feature: str) -> Support:
    matches = [profile for profile in PROFILES if profile.matches(engine, version, feature)]
    if len(matches) != 1:
        return Support(False, None, 'PROFILE_UNREVIEWED' if not matches else 'PROFILE_AMBIGUOUS')
    profile = matches[0]
    return Support(True, profile.id, 'RUNTIME_QUALIFICATION_REQUIRED')


def require(engine, version, feature):
    result = resolve(engine, version, feature)
    if not result.available:
        raise ValueError(result.reason)
    return result


def require_transport(engine, version, transport_profile):
    feature = TRANSPORT_FEATURES.get(transport_profile)
    if feature is None:
        raise ValueError('TRANSPORT_PROFILE_UNREGISTERED')
    return require(engine, version, feature)


def register_profile(profile):
    """Explicit extension registration; it never issues a runtime receipt."""
    global PROFILES
    if not isinstance(profile, EngineProfile) or any(p.id == profile.id for p in PROFILES):
        raise ValueError('PROFILE_DUPLICATE_OR_INVALID')
    PROFILES = (*PROFILES, profile)


def supports(engine, version, feature='native_lifecycle'):
    return resolve(engine, version, feature).available


def implementation(feature, env, *, backend):
    """Select an exact artifact implementation; source verification still runs."""
    import hashlib, json
    from pathlib import Path
    root = Path(__file__).parent
    data = json.loads((root / 'assets/engine_profiles.json').read_bytes())
    matches = [item for item in data.get('implementations', []) if item['feature'] == feature
        and item['engine'] == env.engine.name and item['engine_version'] == env.engine.version
        and item['image_digest'] == env.engine.image_digest and item['backend'] == backend
        and item['model'] == env.model.id and item['model_revision'] == env.model.revision
        and item['model_inventory_sha256'] == env.model.inventory_sha256]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError('PROFILE_AMBIGUOUS')
    item = matches[0]
    path = root / item['data']
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != item['data_sha256']:
        raise ValueError('PROFILE_BYTES_MISMATCH')
    return dict(item)


def create_implementation(feature, ctx, *, backend):
    from importlib import import_module
    selected = implementation(feature, ctx.env, backend=backend)
    if selected is None:
        return None
    module, name = selected['factory'].split(':')
    return getattr(import_module(module), name)(ctx)
