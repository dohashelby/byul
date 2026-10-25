"""Fixed inputs must not hide actual route coverage or safety differences."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from guard.adapters.base import ProbeRequest
from guard.adapters.vllm import VllmAdapter
from guard.executors.http_common import RouteSet, probe_templates
from guard.executors.route_fixture import MAX_ROUTES, manifest, resolve_routes, verify_requests
from guard.models import canonical_json, sha256_hex
from tests.builders import baseline_pack, environment


def context(check_id="REQ-01", version="0.31.0"):
    return SimpleNamespace(
        check=baseline_pack().checks[check_id],
        env=environment(engine={"name": "vllm", "version": version}, model={"id": "EXAMPLE-model"}),
        adapter=VllmAdapter(),
        policy_values={"general_allowed_kinds": ["inference"]},
        allow_mutating=False,
        keys={"none": None, "wrong": "EXAMPLE-wrong-secret", "allowed": "EXAMPLE-allowed-secret",
              "general": "EXAMPLE-general-secret", "admin": "EXAMPLE-admin-secret"},
        target={"run_id": "EXAMPLE-transient"},
    )


def discovered(ctx, registered=("GET /v1/models",)):
    paths = {}
    for item in registered:
        method, path = item.split(" ", 1)
        paths.setdefault(path, {})[method.lower()] = {}
    routes = {f"{r.method} {r.path}": r for r in ctx.adapter.routes_from_openapi({"paths": paths})}
    return RouteSet(routes, list(registered), True, "EXAMPLE-openapi-sha", 200)


def resolved_manifest(ctx, rs, fixed, *, roles=("none", "wrong", "allowed"), mode="direct"):
    resolved = resolve_routes(ctx, rs, fixed)
    templates, skipped = probe_templates(ctx, resolved.routes)
    return manifest(ctx, resolved, templates, skipped, roles=roles, mode=mode)


def test_absent_configuration_is_exact_legacy_identity():
    rs = RouteSet({}, [], False, None, 500)
    assert resolve_routes(object(), rs, None) is rs


def test_fixed_union_keeps_observed_registration_and_original_objects():
    ctx = context()
    rs = discovered(ctx)
    before = deepcopy(rs)
    fixed = ["GET /v1/models", "POST /v1/completions"]
    actual = resolve_routes(ctx, rs, fixed)
    assert list(actual.routes) == fixed
    assert actual.registered == ["GET /v1/models"]
    assert actual.registered is not rs.registered
    assert actual.openapi_sha256 == rs.openapi_sha256 and actual.collected
    assert rs == before and fixed == ["GET /v1/models", "POST /v1/completions"]


@pytest.mark.parametrize("fixed", [
    "GET /v1/models", ("GET /v1/models",), [], [None], [True], [1],
    ["GET /v1/models", "GET /v1/models"], ["POST /v1/completions", "GET /v1/models"],
    ["get /v1/models"], ["GET  /v1/models"], ["GET /v1/models\n"],
    ["GET http://example.invalid/v1/models"], ["GET //v1/models"], ["GET /v1/../models"],
    ["GET /v1/./models"], ["GET /v1/models?secret=x"], ["GET /v1/models#fragment"],
    ["GET /v1/%2fmodels"], ["GET /v1/{bad"], ["GET /v1/models\\other"],
    ["GET /" + "x" * 2048], ["GET /v1/models"] * (MAX_ROUTES + 1),
])
def test_invalid_fixed_inputs_never_shrink_or_normalize_the_scope(fixed):
    ctx = context()
    with pytest.raises(ValueError):
        resolve_routes(ctx, discovered(ctx), fixed)


def test_new_actual_route_cannot_be_omitted_from_fixed_manifest():
    ctx = context()
    rs = discovered(ctx, ["GET /v1/models", "POST /v1/new-route"])
    with pytest.raises(ValueError, match="omit actually registered"):
        resolve_routes(ctx, rs, ["GET /v1/models"])
    assert "POST /v1/new-route" in rs.registered


def test_openapi_collection_is_required_even_with_known_gateway_routes():
    ctx = context()
    rs = discovered(ctx)
    rs.collected = False
    with pytest.raises(ValueError, match="collected route registrations"):
        resolve_routes(ctx, rs, ["GET /v1/models"])


@pytest.mark.parametrize("check_id,registered,fixed", [
    ("REQ-01", ["GET /v1/models"], ["GET /metrics", "GET /v1/models"]),
    ("REQ-03", ["GET /metrics"], ["GET /metrics", "GET /v1/models"]),
])
def test_policy_adjusted_scope_cannot_be_expanded(check_id, registered, fixed):
    ctx = context(check_id)
    with pytest.raises(ValueError, match="policy-adjusted scope"):
        resolve_routes(ctx, discovered(ctx, registered), fixed)


def test_adapter_must_classify_every_fixed_method():
    ctx = context()
    with pytest.raises(ValueError, match="classify the complete"):
        resolve_routes(ctx, discovered(ctx), ["GET /v1/models", "OPTIONS /v1/models"])


def test_fixed_profile_never_enables_unverified_mutating_requests():
    ctx = context(version="0.30.0")
    fixed = ["GET /v1/models", "POST /scale_elastic_ep"]
    rs = resolve_routes(ctx, discovered(ctx), fixed)
    templates, skipped = probe_templates(ctx, rs.routes)
    assert rs.routes["POST /scale_elastic_ep"].mutating
    assert "POST /scale_elastic_ep" not in templates
    assert "POST /scale_elastic_ep" in skipped
    result = manifest(ctx, rs, templates, skipped, roles=["none", "wrong", "allowed"], mode="direct")
    assert result["skipped"] == skipped
    ctx.allow_mutating = True
    with pytest.raises(ValueError, match="prohibit allow_mutating"):
        resolve_routes(ctx, discovered(ctx), fixed)


def test_parameterized_paths_remain_registered_templates_not_fabricated_ids():
    ctx = context()
    fixed = ["GET /v1/models", "GET /v1/responses/{response_id}"]
    result = resolved_manifest(ctx, discovered(ctx), fixed)
    assert result["fixed_routes"] == fixed
    assert result["requests"][fixed[1]]["path"] == "/v1/responses/guard-probe-nonexistent-id"


def test_manifest_is_independent_of_side_observations_clock_source_and_registration_difference():
    first, second = context(), context(version="0.32.0")
    second.target["run_id"] = "EXAMPLE-other-owned-run"
    fixed = ["GET /v1/models", "POST /v1/completions"]
    a = resolved_manifest(first, discovered(first), fixed)
    rs = discovered(second, fixed)
    rs.openapi_sha256 = "EXAMPLE-different-observed-openapi"
    b = resolved_manifest(second, rs, fixed)
    assert a == b
    assert sha256_hex(canonical_json(a)) == sha256_hex(canonical_json(b))
    assert a["protocol"] == "fixed-route-fixture/1"
    assert "EXAMPLE-allowed-secret" not in canonical_json(a).decode()


def test_manifest_captures_headers_safety_basis_expectations_and_normal_controls():
    ctx = context()
    fixed = ["GET /v1/models", "POST /scale_elastic_ep"]
    rs = resolve_routes(ctx, discovered(ctx), fixed)
    templates, skipped = probe_templates(ctx, rs.routes)
    result = manifest(ctx, rs, templates, skipped, roles=["none", "wrong", "allowed"], mode="direct")
    scale = result["requests"]["POST /scale_elastic_ep"]
    assert scale["body"] == {} and scale["headers"] == {"Content-Type": "application/json"}
    assert scale["basis"] == "safe_probe"
    assert scale["expected_handler_reached"] == {"status": 400, "body_contains": "new_data_parallel_size is required"}
    control, = result["functional_probes"]
    assert control["body"]["model"] == "EXAMPLE-model"
    assert control["expect"] == [200] and control["as_role"] == "allowed" and control["via"] == "gateway"
    assert result["coverage"]["tcp"].startswith("connect-first")


@pytest.mark.parametrize("field,value", [
    ("body", {"EXAMPLE": 1}), ("headers", {"Content-Type": "application/other"}),
    ("basis", "safe_probe"), ("expected_handler_reached", {"status": 400, "body_contains": "EXAMPLE other"}),
    ("path", "/EXAMPLE-other-prepared-path"),
])
def test_real_request_differences_always_change_manifest_hash(field, value):
    ctx = context()
    rs = resolve_routes(ctx, discovered(ctx), ["GET /v1/models"])
    templates, skipped = probe_templates(ctx, rs.routes)
    before = manifest(ctx, rs, templates, skipped, roles=["none", "allowed"], mode="direct")
    changed = deepcopy(templates)
    changed["GET /v1/models"] = replace(changed["GET /v1/models"], **{field: value})
    after = manifest(ctx, rs, changed, skipped, roles=["none", "allowed"], mode="direct")
    assert sha256_hex(canonical_json(before)) != sha256_hex(canonical_json(after))


def test_adapter_source_only_difference_does_not_replace_actual_request_data():
    ctx = context()
    rs = resolve_routes(ctx, discovered(ctx), ["GET /v1/models"])
    templates, skipped = probe_templates(ctx, rs.routes)
    a = manifest(ctx, rs, templates, skipped, roles=["none", "allowed"], mode="direct")
    templates["GET /v1/models"] = replace(
        templates["GET /v1/models"], source="EXAMPLE separate inspected-image provenance")
    b = manifest(ctx, rs, templates, skipped, roles=["none", "allowed"], mode="direct")
    assert a == b


@pytest.mark.parametrize("templates,skipped", [({}, {}), ({}, {"GET /other": "EXAMPLE skip"}),
    ({"GET /v1/models": ProbeRequest("GET", "/v1/models")}, {"GET /v1/models": "EXAMPLE skip"})])
def test_manifest_cannot_hide_unrepresented_or_double_counted_routes(templates, skipped):
    ctx = context()
    rs = resolve_routes(ctx, discovered(ctx), ["GET /v1/models"])
    with pytest.raises(ValueError, match="partition"):
        manifest(ctx, rs, templates, skipped, roles=["none", "allowed"], mode="direct")


@pytest.mark.parametrize("roles", [[], ["allowed", "allowed"], ["EXAMPLE-allowed-secret"], [True]])
def test_manifest_roles_are_actual_labels_never_secret_values(roles):
    ctx = context()
    rs = resolve_routes(ctx, discovered(ctx), ["GET /v1/models"])
    templates, skipped = probe_templates(ctx, rs.routes)
    with pytest.raises(ValueError, match="role labels"):
        manifest(ctx, rs, templates, skipped, roles=roles, mode="direct")


def test_manifest_returns_private_copies_and_keeps_role_protocol_distinct():
    ctx = context("REQ-03")
    rs = resolve_routes(ctx, discovered(ctx, ["GET /metrics"]), ["GET /metrics"])
    templates, skipped = probe_templates(ctx, rs.routes)
    roles = ["none", "wrong", "general", "admin"]
    first = manifest(ctx, rs, templates, skipped, roles=roles, mode="role")
    second = manifest(ctx, rs, templates, skipped, roles=roles, mode="role")
    first["requests"]["GET /metrics"]["headers"]["EXAMPLE"] = "changed"
    first["functional_probes"][0]["body"]["model"] = "changed"
    assert first != second
    assert templates["GET /metrics"].headers == {}
    assert ctx.check.functional_probes[0].body["model"] == "{model}"
    assert second["coverage"]["tcp"] == "not-used"


def prepared_batch(fixture):
    requests = []
    for control in fixture["functional_probes"]:
        requests.append({"id": f"func|{control['id']}", "method": control["method"],
                         "url": "http://example.invalid:8080" + control["path"],
                         "body": deepcopy(control["body"]), "headers": {"Authorization": "Bearer EXAMPLE-only"}})
    for name, template in fixture["requests"].items():
        for role in fixture["roles"]:
            requests.append({"id": f"gw|{name}|{role}", "method": template["method"],
                             "url": "http://example.invalid:8080" + template["path"],
                             "body": deepcopy(template["body"]),
                             "headers": {**template["headers"], **({"Authorization": "Bearer EXAMPLE-only"} if role != "none" else {})}})
    return requests


def test_prepared_batch_is_checked_without_requiring_tcp_inferred_http_requests():
    ctx = context()
    fixture = resolved_manifest(ctx, discovered(ctx), ["GET /v1/models", "POST /v1/completions"])
    requests = prepared_batch(fixture)
    before = deepcopy((fixture, requests))
    assert verify_requests(fixture, requests) is None
    assert (fixture, requests) == before
    optional = deepcopy(next(r for r in requests if r["id"] == "gw|GET /v1/models|allowed"))
    optional["id"] = "direct|GET /v1/models|allowed"
    assert verify_requests(fixture, [*requests, optional]) is None


@pytest.mark.parametrize("damage", ["missing-functional", "missing-gateway", "duplicate-functional",
                                   "duplicate-gateway", "unknown-role", "unknown-route", "method", "path",
                                   "body", "headers", "query", "unknown-prefix", "fragment"])
def test_prepared_batch_cannot_omit_or_change_manifest_requests(damage):
    ctx = context()
    fixture = resolved_manifest(ctx, discovered(ctx), ["GET /v1/models", "POST /v1/completions"])
    requests = prepared_batch(fixture)
    functional = requests[0]
    gateway = next(r for r in requests if r["id"] == "gw|GET /v1/models|allowed")
    if damage == "missing-functional": requests.remove(functional)
    elif damage == "missing-gateway": requests.remove(gateway)
    elif damage == "duplicate-functional": requests.append(deepcopy(functional))
    elif damage == "duplicate-gateway": requests.append(deepcopy(gateway))
    elif damage == "unknown-role": gateway["id"] = "gw|GET /v1/models|EXAMPLE-other-role"
    elif damage == "unknown-route": gateway["id"] = "gw|GET /v1/other|allowed"
    elif damage == "method": gateway["method"] = "POST"
    elif damage == "path": gateway["url"] += "/other"
    elif damage == "body": gateway["body"] = {"EXAMPLE": "changed"}
    elif damage == "headers": gateway["headers"]["EXAMPLE-header"] = "changed"
    elif damage == "query": gateway["url"] += "?EXAMPLE=changed"
    elif damage == "unknown-prefix": gateway["id"] = "EXAMPLE|GET /v1/models|allowed"
    else: gateway["url"] += "#EXAMPLE-fragment"
    with pytest.raises(ValueError):
        verify_requests(fixture, requests)


def test_batch_auth_values_are_not_hashed_but_static_headers_are_verified():
    ctx = context()
    fixture = resolved_manifest(ctx, discovered(ctx), ["GET /v1/models", "POST /scale_elastic_ep"])
    requests = prepared_batch(fixture)
    for request in requests:
        if "Authorization" in request["headers"]:
            request["headers"]["Authorization"] = "Bearer EXAMPLE-another-owned-key"
    assert verify_requests(fixture, requests) is None
    scale = next(r for r in requests if r["id"] == "gw|POST /scale_elastic_ep|allowed")
    scale["headers"]["Content-Type"] = "application/EXAMPLE-other"
    with pytest.raises(ValueError):
        verify_requests(fixture, requests)


def test_role_fixture_rejects_direct_requests():
    ctx = context("REQ-03")
    fixture = resolved_manifest(ctx, discovered(ctx, ["GET /metrics"]), ["GET /metrics"],
                                roles=["none", "wrong", "general", "admin"], mode="role")
    requests = prepared_batch(fixture)
    added = deepcopy(next(r for r in requests if r["id"] == "gw|GET /metrics|admin"))
    added["id"] = "direct|GET /metrics|admin"
    with pytest.raises(ValueError):
        verify_requests(fixture, [*requests, added])
