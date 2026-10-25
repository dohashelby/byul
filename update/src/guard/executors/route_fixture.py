"""Opt-in fixed route inputs; observed registrations and responses stay separate.

This module never creates a request or decides that a mutating route is safe.
The adapter supplies the actual templates, and the existing executors retain
their TCP inference, skipped-route and complete-coverage contracts.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import re
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from .http_common import Ctx, RouteSet


MAX_ROUTES = 512
MAX_ROUTE_LENGTH = 2048
_ROUTE = re.compile(
    r"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) "
    r"(/(?:[A-Za-z0-9._~!$&'()+,;=:@/-]|\{[A-Za-z_][A-Za-z0-9_]*\})*)"
)


def _configured_routes(configured) -> list[str]:
    if type(configured) is not list or not 1 <= len(configured) <= MAX_ROUTES:
        raise ValueError("fixed routes require a nonempty bounded list[str]")
    for item in configured:
        if type(item) is not str or len(item) > MAX_ROUTE_LENGTH:
            raise ValueError("fixed route entries must be bounded strings")
        matched = _ROUTE.fullmatch(item)
        if matched is None:
            raise ValueError("fixed routes require canonical METHOD /path entries")
        path = matched.group(2)
        if "//" in path or any(part in {".", ".."} for part in path.split("/")):
            raise ValueError("fixed route paths must not contain ambiguous segments")
    if len(set(configured)) != len(configured):
        raise ValueError("fixed routes must be unique")
    if configured != sorted(configured):
        raise ValueError("fixed routes must be sorted")
    return list(configured)


def resolve_routes(ctx: Ctx, rs: RouteSet, configured) -> RouteSet:
    """Use the configured union without inventing registrations on either side.

    A missing OpenAPI collection or an unexpected newly registered route cannot
    be hidden by the fixed list. Adapter classification also prevents adding
    routes outside this check's policy-adjusted scope. Unverified mutating
    routes remain present for the existing skip/TCP-only handling.
    """
    if configured is None:
        return rs
    fixed = _configured_routes(configured)
    if ctx.allow_mutating:
        raise ValueError("fixed route fixtures prohibit allow_mutating_routes")
    if not rs.collected:
        raise ValueError("fixed route fixtures require collected route registrations")
    if ctx.check.route_scope is None:
        raise ValueError("fixed route fixtures require a route scope")
    if not set(rs.registered).issubset(fixed):
        raise ValueError("fixed routes omit actually registered scoped routes")

    paths = {}
    for item in fixed:
        method, path = item.split(" ", 1)
        paths.setdefault(path, {})[method.lower()] = {}
    classified = ctx.adapter.routes_from_openapi({"paths": paths})
    routes = {f"{route.method.upper()} {route.path}": route for route in classified}
    if len(classified) != len(fixed) or set(routes) != set(fixed):
        raise ValueError("adapter cannot classify the complete fixed route list")
    included = set(ctx.check.route_scope.include_kinds)
    excluded_key = ctx.check.route_scope.exclude_kinds_policy
    if excluded_key:
        included -= set(ctx.policy_values.get(excluded_key) or [])
    if any(route.kind not in included for route in routes.values()):
        raise ValueError("fixed routes contain paths outside the policy-adjusted scope")
    return replace(rs, routes={name: routes[name] for name in fixed}, registered=list(rs.registered))


def manifest(ctx: Ctx, rs: RouteSet, templates, skipped, *, roles, mode) -> dict:
    """Describe actual resolved inputs, never response/side/registration values.

    Hash this returned structure as a whole. Route omissions, headers, safety
    basis, handler expectations and normal controls are meaningful inputs.
    Adapter source annotations can differ by image and are retained by the
    executor's separate provenance log, not by this request-input manifest.
    """
    from .http_common import TCP_BLOCKED, fill_model

    if mode not in {"direct", "role"}:
        raise ValueError("fixed route fixture mode must be direct or role")
    fixed = _configured_routes(sorted(rs.routes))
    if (set(templates) & set(skipped) or set(templates) | set(skipped) != set(fixed)
            or any(type(reason) is not str or not reason for reason in skipped.values())):
        raise ValueError("fixed fixture templates/skips must partition all fixed routes")
    if (type(roles) not in (list, tuple) or not roles
            or any(type(role) is not str or not role or role not in ctx.keys for role in roles)
            or len(set(roles)) != len(roles)):
        raise ValueError("fixed fixture roles must be unique actual role labels")

    requests = {}
    for name in sorted(templates):
        request = templates[name]
        if request.method != rs.routes[name].method:
            raise ValueError("fixed fixture template method differs from its route")
        requests[name] = {
            "method": request.method,
            "path": request.path,
            "body": deepcopy(request.body),
            "headers": deepcopy(request.headers),
            "basis": request.basis,
            "expected_handler_reached": deepcopy(request.expected_handler_reached),
        }
    functional = [{"id": probe.id, "method": probe.method.upper(), "path": probe.path,
                   "body": fill_model(deepcopy(probe.body), ctx.env.model.id),
                   "headers": {}, "expect": list(probe.expect),
                   "as_role": probe.as_role, "via": probe.via}
                  for probe in ctx.check.functional_probes]
    coverage = {"mode": mode, "registered_routes": "all-actually-registered-scoped-routes",
                "unverified_mutating": "never-send-without-adapter-safe-probe"}
    if mode == "direct":
        coverage.update(tcp="connect-first-for-each-direct-target-and-role",
                        blocked_outcomes=list(TCP_BLOCKED),
                        blocked_inference="all-fixed-routes-with-trusted-positive-control",
                        connected="resolved-safe-http-templates-only")
    else:
        coverage.update(tcp="not-used", requests="gateway-resolved-safe-http-templates-by-role")
    return {"protocol": "fixed-route-fixture/1", "fixed_routes": fixed,
            "requests": requests, "skipped": {name: skipped[name] for name in sorted(skipped)},
            "roles": sorted(roles), "functional_probes": functional, "coverage": coverage}


def verify_requests(fixture: dict, requests: list[dict]) -> None:
    """Verify a prepared batch before sending, with credentials kept out.

    Functional and Gateway role matrices must be complete and unique. Direct
    HTTP requests are optional because a blocked TCP connection is represented
    by separate inferred local results; any submitted direct request must still
    match an actual resolved template and a known role.
    """
    from guard.models import canonical_json

    if fixture.get("protocol") != "fixed-route-fixture/1" or type(requests) is not list:
        raise ValueError("invalid fixed fixture request batch")
    mode = fixture["coverage"]["mode"]
    if mode not in {"direct", "role"}:
        raise ValueError("invalid fixed fixture request mode")
    functional = {f"func|{probe['id']}": probe for probe in fixture["functional_probes"]}
    if len(functional) != len(fixture["functional_probes"]):
        raise ValueError("duplicate fixed fixture functional labels")
    gateway = {f"gw|{name}|{role}": probe
               for name, probe in fixture["requests"].items() for role in fixture["roles"]}
    direct = {f"direct|{name}|{role}": probe
              for name, probe in fixture["requests"].items() for role in fixture["roles"]} if mode == "direct" else {}
    allowed = {**functional, **gateway, **direct}
    seen = set()

    def public_headers(headers):
        if type(headers) is not dict or any(type(k) is not str or type(v) is not str for k, v in headers.items()):
            raise ValueError("invalid fixed fixture request headers")
        names = [key.lower() for key in headers]
        if len(set(names)) != len(names):
            raise ValueError("ambiguous fixed fixture request headers")
        return {key.lower(): value for key, value in headers.items() if key.lower() != "authorization"}

    for request in requests:
        if type(request) is not dict or type(request.get("id")) is not str:
            raise ValueError("invalid fixed fixture request label")
        label = request["id"]
        if label not in allowed or label in seen:
            raise ValueError("unexpected or duplicate fixed fixture request label")
        seen.add(label)
        expected = allowed[label]
        try:
            url = urlsplit(request["url"])
            path = (url.path or "/") + ("?" + url.query if url.query else "")
            valid = (url.scheme in {"http", "https"} and bool(url.hostname) and not url.fragment
                     and url.username is None and url.password is None
                     and request.get("method") == expected["method"] and path == expected["path"]
                     and canonical_json(request.get("body")) == canonical_json(expected["body"])
                     and public_headers(request.get("headers", {})) == public_headers(expected["headers"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid fixed fixture request inputs") from exc
        if not valid:
            raise ValueError("submitted request differs from the fixed fixture")
    if not set(functional).issubset(seen) or not set(gateway).issubset(seen):
        raise ValueError("fixed fixture functional or gateway requests are missing")
