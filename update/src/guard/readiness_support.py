"""Static collection-profile guidance, separate from host readiness and verdicts.

No target file, Docker daemon, engine, network or store is read here. A supported
row means the declared inputs match an existing bounded collector; observation
coverage, engine identity and the security/functional result remain unmeasured.
"""
from __future__ import annotations

from guard.adapters.engine_provider import evidence_contract, get_provider

from collections.abc import Iterable
import re

from guard.automation_policy import (string_list, validate_log_integrity_policy,
                                     validate_request_binding_policy, validate_template_policy)
from guard.configuration import (GATEWAY_RECORD_SNAPSHOT_OPTION, KUBERNETES_BOUND_CHECK_OPTIONS,
                                 UPDATE_CHECK_OUTSIDE_SCOPE, for_check)
from guard.core.select import check_policy_value
from guard.executors import ipc_trust_scope, log_collection_gap, passive_api_hop, registry, request_field_probe, run_binding
from guard.executors.request_binding import SUPPORTED_POLICY as BINDING_POLICY
from guard.executors.log_integrity_parser import PROFILE as LOG_PROFILE, TRUSTED_FIELDS
from guard.executors.response_content import OPTIONS as RETURN_OPTIONS
from guard.executors.sampling_validation import SUPPORTED_OPTIONS as SAMPLING_OPTIONS
from guard.executors.log_analysis import (REQUIRED_BRANCHES as LOG_REQUIRED_BRANCHES,
                                          SUPPORTED_BRANCHES as LOG_SUPPORTED_BRANCHES,
                                          SUPPORTED_MARKER_CONTEXTS as LOG_MARKER_CONTEXTS)
from guard.executors.running_image_approval import PROFILE as IMAGE_APPROVAL_PROFILE, validate_image_approval_policy
from guard.executors.template_supply_approval import (PROFILE as TEMPLATE_SUPPLY_PROFILE,
                                                      validate_policy as validate_template_supply_policy)
from guard.input_policy import policy_context
from guard.reused_matrix_policy import PROFILE as REUSED_MATRIX, selected as reused_matrix_policy


# Descriptions of the existing collectors, not a second plugin registry.
_SCOPES = {
    "engine_sensor": "source-qualified native feature with exact runtime artifact and worker scope verification",
    "unix_ipc": "owned Linux Unix SOCK_STREAM zero-payload UID access only; no full IPC PASS",
    "direct_route": "registered route access and bounded normal inference controls",
    "role_route": "declared general/admin roles over registered route kinds",
    "container_config": "owned container/process permissions and bounded sentinel writes",
    "credential_exposure": "explicit environment/mount bans and separate bounded worker credential-candidate metadata",
    "input_validation": "one engine token-array endpoint, token ID and input-length challenges",
    "sampling_validation": "bounded sampling API validation; runner consumption unverified",
    "response_content": "declared JSON fields/return options and synthetic private chat context",
    "log_analysis": "declared source/branch inventory and trial-linked structured log content",
    "template_validation": "fixed-template rendering, consumed prompt IDs and pre-generation limits",
    "request_binding": "JSON/SSE input projections, connection overlap and response framing",
    "log_integrity": "fixed native INFO source through the reference parser and escaped display",
}
_COMMON_LIMIT = (
    "Static guidance only: support is not a verdict, observed identity or completed challenge. "
    "EVD COMPLETED/MEASURED alone does not establish challenge coverage or PASS."
)


def _policy_shape(check, values, *, kubernetes_template=False):
    """Existing declared types and pure shape rules, without collector limits."""
    for param in check.policy_params:
        if param.required and param.key not in values:
            raise ValueError("required policy value is missing")
        if param.key in values:
            check_policy_value(check.id, param.key, param.type, values[param.key])
    profile = getattr(check, "collection_profile", None)
    if check.executor == "template_validation":
        if getattr(check, "collection_profile", None) == TEMPLATE_SUPPLY_PROFILE:
            validate_template_supply_policy(check, values, {})
        elif check.id != "SUP-13" or not kubernetes_template:
            validate_template_policy(values)
    elif check.executor == "unix_ipc":
        if profile == ipc_trust_scope.PROFILE:
            ipc_trust_scope.validate_policy(values)
        else:
            from guard.executors.unix_ipc import policy_shape
            policy_shape(values)
    elif check.executor == "container_config" and getattr(check, "collection_profile", None) == IMAGE_APPROVAL_PROFILE:
        validate_image_approval_policy(values)
    elif check.executor == "request_binding":
        validate_request_binding_policy(values)
    elif check.executor == "log_integrity":
        if profile == log_collection_gap.PROFILE:
            log_collection_gap.validate_policy_shape(values)
        else:
            validate_log_integrity_policy(values)
    elif check.executor == "input_validation" and request_field_probe.supported(check):
        request_field_probe.validate_policy(check, values)
    elif check.executor == "input_validation" and profile == REUSED_MATRIX:
        reused_matrix_policy(check, values)
    elif check.executor in {"input_validation", "sampling_validation", "response_content", "log_analysis"}:
        for param in check.policy_params:
            if param.type == "list[str]":
                string_list(values, param.key, empty=param.key in {
                    "forbidden_return_options", "forbidden_log_fields", "forbidden_marker_contexts"})
        if check.executor == "input_validation" and values.get("max_input_tokens", 0) < 1:
            raise ValueError("max_input_tokens must be positive")


def assess_check_support(pack, env, policy, cfg: dict, *, selected_check_ids: Iterable[str],
                         managed: bool = True, allowed_checks=None, passive_hop: bool = False) -> list[dict]:
    """Describe each selected check without rejecting or rewriting its policy.

    The caller supplies the effective configuration for one side. Input shape,
    launch validation and host readiness stay with prepare-*; this assessment
    never removes selected checks or promotes declared inputs to observations.
    """
    rows = []
    try:
        provider = get_provider(env.engine.name)
        adapter = provider.adapter()
    except ValueError:
        provider, adapter = None, None
    for check_id in dict.fromkeys(selected_check_ids):
        check = pack.checks.get(check_id)
        executor = check.executor if check is not None else None
        row = {"check_id": check_id, "executor": executor, "status": "UNKNOWN",
               "policy_valid": None, "policy_supported": None,
               "code_available": False, "profile_available": False, "runtime_qualified": False,
               "scope": _SCOPES.get(executor, "manual evidence; no automatic collection profile"),
               "reasons": [], "limitations": [_COMMON_LIMIT]}
        rows.append(row)
        if check is not None and getattr(check, "collection_profile", None) == IMAGE_APPROVAL_PROFILE:
            row["scope"] = "owned running pinned image approval only; no sentinel writes or full package validation"
            row["limitations"].append("runtime identity and RepoDigest linkage remain unobserved; approved image alone cannot establish full supply PASS")
        template_supply = check is not None and getattr(check, "collection_profile", None) == TEMPLATE_SUPPLY_PROFILE
        if template_supply:
            row["scope"] = "owned readonly fixed-template identity and API input projection only"
            row["limitations"].append("observation-only profile: no independent worker consumption proof; approved and unapproved selections remain security UNKNOWN")
            row["limitations"].append("manual PASS is outside the existing bounded manual template contract; declared FAIL and UNKNOWN remain available")
        if check is None or check.retired:
            row["reasons"].append("check is absent from the active pack")
            continue
        if executor is None:
            row["status"] = "UNSUPPORTED"
            row["reasons"].append("no automatic executor; manual evidence import remains a separate path")
            continue
        profile = getattr(check, "collection_profile", None)
        scoped_out = allowed_checks is not None and check_id not in allowed_checks
        if scoped_out:
            row["reasons"].append(UPDATE_CHECK_OUTSIDE_SCOPE)
        if profile == log_collection_gap.PROFILE:
            row["scope"] = "configured deadlines and a source-bound log collection session"
            try:
                session = run_binding.run_config(cfg, check_id)
            except ValueError:
                row["reasons"].append("RUN_BINDING_CONFIG_INVALID")
            else:
                if type(session) is not dict or not session.get("record_session"):
                    row["reasons"].append("a source-bound collection session input is required")
        implementation = cfg.get("executors", {}).get(executor)
        kube = implementation == "kubernetes" and not managed
        kube_template = kube and executor == "template_validation"
        values = policy.values.get(check_id, {})
        try:
            _policy_shape(check, values, kubernetes_template=kube_template)
        except (ValueError, TypeError):
            row["policy_valid"] = False
            row["reasons"].append("required policy values are missing or malformed; support cannot be determined")
            continue
        row["policy_valid"] = True
        if scoped_out:
            continue
        if passive_hop and check_id == passive_api_hop.CHECK:
            row["scope"] = "passive evidence from the same side's owned PLT-01 normal control"
            row["limitations"].append("the deployed caller path, TLS, IPC, TP and per-hop policy remain unverified")
            try:
                passive_api_hop.validate_policy(check, values)
            except passive_api_hop.PassiveApiHopUnavailable as exc:
                row["policy_valid"] = False
                row["reasons"].append(str(exc))
            else:
                row.update(code_available=True, profile_available=True, policy_supported=True)
                row["reasons"].append("requires the exact committed PLT-01 control in this execution attempt")
            continue
        selected_factory = (registry.resolve_executor(executor, implementation, engine=env.engine.name, version=env.engine.version)
                            if implementation == "real" or kube else None)
        if selected_factory is None:
            row["status"] = "UNSUPPORTED"
            row["reasons"].append("a registered real collector is not selected; fake execution is not engine support")
            continue
        row['code_available'] = True
        if kube and check_id in {'OBS-01', 'OBS-02', 'PLT-02', 'PLT-07', 'PLT-12', 'LIF-01', 'LIF-03', 'LIF-04',
                                 'REQ-09', 'REQ-10', 'SUP-10'}:
            effective = for_check(cfg, executor, check_id).get(executor, {})
            setting = KUBERNETES_BOUND_CHECK_OPTIONS[check_id]
            if type(effective) is dict and effective.get(setting) is True:
                if check_id == 'OBS-01':
                    row["scope"] = ("one owned Runtime Gateway final row selected by the original request hash and session"
                                    if effective.get(GATEWAY_RECORD_SNAPSHOT_OPTION) is True else
                                    "owned audit record snapshot for the selected producer, actor hashes and session")
                    row["limitations"].append("independent event triggering, record delivery and timing remain unverified")
                elif check_id == 'OBS-02':
                    row["scope"] = "owned HTTP readiness status and an ordinary completion in the same container generation"
                    row["limitations"].append("request selection, worker state, profiler and termination require separate observations")
                elif check_id == 'PLT-12':
                    row["scope"] = "owned copy-inventory record snapshot bound to the selected policy and producer"
                    row["limitations"].append("copy membership, treatment, reuse and issuer state require independent evidence")
                elif check_id == 'PLT-02':
                    row["scope"] = "approved IPC path presence and mode in the owned Pod mount namespace"
                    row["limitations"].append("no IPC connections, process-to-socket attribution or whole-access verdict")
                elif check_id == 'PLT-07':
                    row["scope"] = "reported Cilium egress policy state for the selected owned endpoint"
                    row["limitations"].append("packet verdicts and process-level egress require separate observations")
                elif check_id == 'SUP-10':
                    row["scope"] = "installed vLLM plugin entry-point metadata from the selected owned container"
                    row["limitations"].append("plugin load, invocation and allowlist enforcement require separate observations")
                elif check_id == 'LIF-01':
                    row["scope"] = "source-checked normal model load timing and device memory delta from the owned startup log"
                    row["limitations"].append("fetch, resolve, verify, peak bytes and budget enforcement require separate evidence")
                elif check_id == 'LIF-03':
                    row["scope"] = "owned client timing for one ordinary completion"
                    row["limitations"].append("server budgets, contention and fairness require separate observations")
                elif check_id == 'LIF-04':
                    row["scope"] = "ordinary stream framing and fixed owned container memory observations"
                    row["limitations"].append("bounded normal stream only; backpressure, debug behavior and release stay unverified")
                elif check_id == 'REQ-09':
                    row["scope"] = "owned tokenization and one ordinary completion using the returned token IDs"
                    row["limitations"].append("rejection boundaries and native processor consumption stay unverified")
                else:
                    row["scope"] = "one ordinary completion with the approved sampling options"
                    row["limitations"].append("sampling limits, rejections, structured output and runner consumption stay unverified")
                row["profile_available"] = True
                row["reasons"].append("the exact target and policy inputs must pass static preparation")
                continue
        if executor == 'engine_sensor':
            selected_executor = selected_factory(for_check(cfg, executor, check.id))
            describe = getattr(selected_executor, 'support', None)
            state = describe(check.id, check.collection_profile, env) if callable(describe) else {'implemented': False, 'reason': 'PLUGIN_SUPPORT_CONTRACT_UNAVAILABLE'}
            row.update(code_available=state.get('code_available', state['implemented']),
                       profile_available=state['implemented'], runtime_qualified=False)
            row['status'] = 'SUPPORTED' if state['implemented'] else 'UNSUPPORTED'
            row['policy_supported'] = state['implemented']
            row['reasons'].append(state.get('reason') or 'runtime source, artifact and worker scope qualification is required')
            continue
        if executor not in _SCOPES:
            row["reasons"].append("no static support description exists for this collector")
            continue
        if provider is None or cfg.get("adapter", {}).get("name") != env.engine.name:
            row["reasons"].append("declared adapter/engine identity does not match the existing engine adapter")
            continue
        row["status"] = "SUPPORTED"
        row["policy_supported"] = True
        row["profile_available"] = True
        row["reasons"].append("declared configuration selects the existing bounded real collector")

        def unsupported(reason, *, policy_scope=False):
            row["status"] = "UNSUPPORTED"
            row["profile_available"] = False
            if policy_scope:
                row["policy_supported"] = False
            row["reasons"].append(reason)

        if kube_template:
            try:
                selected_factory(for_check(cfg, executor, check_id)).validate_policy(check, values)
            except (TypeError, ValueError):
                unsupported("the check or policy is outside the configured Kubernetes template collector's scope",
                            policy_scope=True)
            else:
                row["status"] = "UNKNOWN"
                row["reasons"].append("owned Pod identity and selected readonly template bytes require runtime verification")
                if check_id == "REQ-07":
                    row["scope"] = "owned fixed-template normal input projection within the declared token limit"
                    row["limitations"].append("limit enforcement, request template overrides and worker consumption require separate evidence")
            continue

        if profile == log_collection_gap.PROFILE:
            row["status"] = "UNKNOWN"
            row["limitations"].append("session identity, producer records and collection timing require runtime verification")
            continue

        if executor == "unix_ipc":
            if profile == ipc_trust_scope.PROFILE:
                row["scope"] = "owned process trust-group and identity metadata"
                row["status"] = "UNKNOWN"
                row["limitations"].append("worker group attribution, isolation and shared-resource scope require separate evidence")
                if kube:
                    try:
                        selected_factory(for_check(cfg, executor, check_id)).validate_policy(check, values)
                    except (ValueError, TypeError):
                        unsupported("the configured Kubernetes collector does not admit this worker trust profile")
                continue
            from guard.executors.unix_ipc import UnixIPCExecutor
            try:
                UnixIPCExecutor(cfg.get("unix_ipc", {}), docker=object()).validate_policy(check, values)
            except (ValueError, TypeError):
                row["status"] = "UNKNOWN"
                row["reasons"].append("explicit allowed/disallowed probe UID configuration and bounded IPC settings are required")
            row["limitations"].append("actual endpoint owner, namespaces and SO_PEERCRED require runtime collection; no SHM/TCP/command or full IPC PASS")

        if executor in {"template_validation", "request_binding", "log_integrity"}:
            if executor == "template_validation":
                if values.get("request_template_override_allowed") is True:
                    unsupported("valid policy allowing request-template overrides exceeds the restricted collector", policy_scope=True)
                limit = values.get("max_rendered_input_tokens")
                budget = cfg.get(executor, {}).get("max_probe_tokens", 4096)
                if type(limit) is int and type(budget) is int and limit + 1 > budget:
                    unsupported("valid policy boundary exceeds the bounded token-fixture budget; policy is not reduced", policy_scope=True)
            elif executor == "request_binding":
                if any(values.get(key) != expected for key, expected in BINDING_POLICY.items()):
                    unsupported("valid result-binding or request-ID trust policy exceeds originating-connection/untrusted-correlation support", policy_scope=True)
                row["limitations"].append("generated output has no independent binding oracle; input projection success cannot establish overall security PASS")
            else:
                if (values.get("collector_profile") != LOG_PROFILE
                        or values.get("client_request_id_trust") != "untrusted_correlation"
                        or set(values.get("required_trusted_fields", [])) - set(TRUSTED_FIELDS)):
                    unsupported("valid collector/trust/field policy exceeds the fixed native INFO reference collector", policy_scope=True)
                row["limitations"].append("source identity and exact trial events need runtime verification; enterprise collector/SIEM coverage is unverified")
            if not managed:
                if row["status"] != "UNSUPPORTED":
                    row["status"] = "UNKNOWN"
                row["reasons"].append("external target ownership and launch metadata require runtime verification")
                if kube:
                    if evidence_contract(adapter, "request_binding_contract", env.engine.image_digest,env.engine.version) is None:
                        unsupported("REQ-11 has no inspected consumer-source contract for the exact image/version")
                    row["limitations"].append("Kubernetes direct Pod JSON/SSE only; no Service/Gateway/TLS or GPU-release proof")
                continue
            launch = cfg.get(env.engine.name, {})
            if not provider.bounded_launch_evidence(launch, env):
                unsupported("this inspected collection profile requires an explicit bounded GPU launch (gpu-bounded with assigned GPUs)")
            image, version = env.engine.image_digest, env.engine.version
            if not image or not version:
                if row["status"] != "UNSUPPORTED":
                    row["status"] = "UNKNOWN"
                row["reasons"].append("pinned engine image/version is missing; version text alone cannot establish support")
            elif executor == "template_validation":
                # The adapter's template_rejection_evidence returns only this
                # image/version's inspected pre-generation rejection proofs.
                if not template_supply and not provider.template_rejection_supported(image, version):
                    unsupported("REQ-07 pre-generation source evidence is inspected only for the exact pinned image/version")
                if template_supply and (len(image) > 512 or re.fullmatch(
                        r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}", image) is None):
                    unsupported("template supply collection requires an explicit repository-pinned image; a bare manifest digest is not a launch reference")
            elif executor == "request_binding":
                if evidence_contract(adapter, "request_binding_contract", image, version) is None:
                    unsupported("REQ-11 has no inspected consumer-source contract for the exact image/version")
            elif evidence_contract(adapter, "native_log_contract", image, version) is None:
                unsupported("OBS-09 has no inspected native logging contract for the exact image/version")

            if executor in {"template_validation", "request_binding"}:
                metadata = {"profile": launch.get("chat_template_profile"), "version": "1",
                            "sha256": provider.template_profile().get("sha256"), "container_path": provider.template_profile().get("container_path"),
                            "content_format": "string", "request_overrides": False}
                if not evidence_contract(adapter, "supports_template_profile", metadata):
                    unsupported("the declared fixed chat-template profile is absent or unsupported")
                row["limitations"].append("readonly template bytes, launch argv and owned container identity need runtime verification")
            if executor == "template_validation":
                if provider.template_profile().get("sha256") not in values.get("allowed_template_sha256", []):
                    row["limitations"].append("the selected fixed template is outside the unchanged policy allowlist; a runtime violation may be observed")
            elif executor == "log_integrity":
                if launch.get("logging_profile") != "request-info-v1":
                    unsupported("the fixed native INFO logging launch profile is absent or unsupported")
        elif executor == "input_validation":
            if request_field_probe.supported(check) or profile == REUSED_MATRIX:
                row["scope"] = "declared request fields and exact engine-bound observation inputs"
                row["status"] = "UNKNOWN"
                row["limitations"].append("approved runtime assets and field consumption require separate verification")
                continue
            context = policy_context(check, values)
            if context is not None and context.blocked:
                unsupported("valid endpoint/input-format policy exceeds the single submitted token-array profile", policy_scope=True)
            if set(values.get("rejection_codes", [])) - {"400", "413", "422"}:
                unsupported("valid rejection policy names responses outside the bounded input profile", policy_scope=True)
            limit, budget = values.get("max_input_tokens"), cfg.get(executor, {}).get("max_probe_tokens", 4096)
            if type(limit) is int and type(budget) is int and limit + 1 > budget:
                unsupported("valid input limit exceeds the bounded probe budget; policy is not reduced", policy_scope=True)
            row["limitations"].append("submitted endpoint/format is a bounded fixture, not proof of all parser consumption paths")
            row["limitations"].append("HTTP success is not consumption proof; rejection needs an exact reviewed source/response contract")
        elif executor == "credential_exposure":
            row["limitations"].append("mount policy violations do not establish credential contents or use; candidate access needs the same observed worker subject")
            if "credential_candidate_paths" not in values:
                row["limitations"].append("candidate scope was not configured; security PASS is withheld by the new observation contract")
            elif not values["credential_candidate_paths"]:
                row["limitations"].append("explicit empty candidate scope covers environment/mount policy only; no credential file was tested")
        elif executor == "sampling_validation":
            if set(values.get("allowed_sampling_options", [])) - set(SAMPLING_OPTIONS):
                unsupported("valid sampling policy contains options outside the bounded API profile", policy_scope=True)
            if set(values.get("rejection_codes", [])) - {"400", "422"}:
                unsupported("valid rejection policy names responses outside the bounded sampling profile", policy_scope=True)
            if cfg.get(executor, {}).get("constraint_engine_version") != env.engine.version:
                unsupported("declared sampling constraint version differs from the selected engine")
            row["limitations"].append("runner/count consumption remains unverified; API collection alone cannot establish overall security PASS")
        elif executor == "response_content":
            if set(values.get("forbidden_return_options", [])) - RETURN_OPTIONS.keys():
                unsupported("valid return-option policy exceeds the existing bounded response profile", policy_scope=True)
            if set(values.get("required_targets", [])) - {"engine", "gateway"}:
                unsupported("valid target policy exceeds engine/gateway collection support", policy_scope=True)
            if cfg.get(executor, {}).get("endpoint", "/v1/completions") != "/v1/chat/completions":
                unsupported("completion-only collection cannot cover the required private-system chat branch; other observations remain useful")
            row["limitations"].append("actual response fields and private-context behavior remain unobserved; model instruction quality is outside this check")
        elif executor == "log_analysis":
            branches = set(values.get("required_branches", []))
            if kube:
                row["scope"] = "owned engine Pod normal inference and correlated structured stdout fields"
                row["status"] = "UNKNOWN"
                row["reasons"].append("owned Pod identity, structured log format and request correlation require runtime collection")
                row["limitations"].append("one normal engine control; gateway logs, error and exception-dump branches require separate evidence")
                if set(values.get("required_sources", [])) - {"engine"} or branches - {"normal"}:
                    unsupported("required source or branch coverage exceeds the normal engine log collector", policy_scope=True)
                continue
            if not LOG_REQUIRED_BRANCHES <= branches or branches - LOG_SUPPORTED_BRANCHES:
                unsupported("valid branch policy exceeds the required normal/error/exception-dump and supported debug/trace profile", policy_scope=True)
            if set(values.get("forbidden_marker_contexts", [])) - LOG_MARKER_CONTEXTS:
                unsupported("valid marker-context policy exceeds caller-input/private-system/error-input collection support", policy_scope=True)
            row["limitations"].append("normal/error/exception_dump coverage is unverified until trial-linked structured events are collected; a safe 4xx or successful log read is insufficient")
    return rows
