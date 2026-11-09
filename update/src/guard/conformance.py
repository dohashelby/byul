"""Offline installation conformance. No engine qualification or receipt issuance."""
from pathlib import Path

from guard.adapters.base import EngineAdapter
from guard.adapters.engine_profiles import PROFILES, resolve
from guard.adapters.engine_provider import PROVIDERS
from guard.reference.integrity import verify_packaged


def inspect_installation():
    from guard.adapters.engine_provider import load_installed_providers
    from guard.adapters import engine_profiles
    load_installed_providers()
    providers, profiles = {}, []
    for name, provider in PROVIDERS.items():
        if not isinstance(provider.adapter(), EngineAdapter):
            raise ValueError('ADAPTER_PROTOCOL_NONCONFORMANT:' + name)
        providers[name] = {'http_adapter': True, 'managed_launch': provider.managed_available,
                           'runtime_qualified': False}
    for profile in engine_profiles.PROFILES:
        engines = PROVIDERS if profile.engine == '*' else (profile.engine,)
        versions = profile.versions or ('EXAMPLE-unreviewed-release',)
        for engine in engines:
            for version in versions:
                for feature in profile.features:
                    state = resolve(engine, version, feature)
                    if not state.available or state.profile != profile.id or state.runtime_qualified:
                        raise ValueError('PROFILE_CATALOG_NONCONFORMANT:' + profile.id)
        profiles.append(profile.id)
    components = {name: len(verify_packaged(name)) for name in ('slot', 'template')}
    from guard.reference import slot_cpu, template_cpu
    slot_cpu.verify_component()
    template_cpu.verify_component()
    asset = Path(__file__).parent / 'runtime/assets/callback_page.py.txt'
    compile(asset.read_bytes(), str(asset), 'exec')
    return {'schema': 'guard-installation-conformance/1', 'providers': providers, 'profiles': profiles,
            'source_cpu_components': components, 'bounded_page_reader': True,
            'source_cpu_external_engine_source_required': True,
            'runtime_qualified': False, 'engine_executed': False, 'receipt_issued': False}
