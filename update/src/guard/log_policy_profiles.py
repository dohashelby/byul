"""Pure versioned manual-evidence ceilings; no engine/runtime imports.

An existing contract never acquires newly supported collectors. New collectors
need a new contract ID in a new CheckDef/judge parameter revision.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class LogPolicyContract:
    collectors: frozenset[str]
    client_request_id_trust: str
    trusted_fields: frozenset[str]


CONTRACTS = {
    'legacy-native-info/1': LogPolicyContract(frozenset({'guard-vllm-native-info-v1'}),
        'untrusted_correlation', frozenset({'process', 'level', 'source'})),
}


def policy_supported(values, contract='legacy-native-info/1'):
    selected = CONTRACTS.get(contract)
    return (selected is not None and values.get('collector_profile') in selected.collectors
            and values.get('client_request_id_trust') == selected.client_request_id_trust
            and set(values.get('required_trusted_fields', ())) <= selected.trusted_fields)
