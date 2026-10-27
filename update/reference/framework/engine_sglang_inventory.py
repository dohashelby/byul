#!/usr/bin/env python3
"""Read pinned SGLang Python as AST; never import or start the engine.

Supported: FastAPI/APIRouter decorator registrations, if/else guards, local
from-import router aliases and include_router chains. Other control flow and
unresolved includes/registration calls are surfaced for manual review.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path

COMMIT = "baa9befa381511cd4d56f83980951e5a45fffc6d"
MAIN = "python/sglang/srt/entrypoints/http_server.py"
PIN = {
    MAIN: "a89d20bb2fa8a86c511ab4d9d054f74d707423ab220f733ff25e62fcc0e49979",
    "python/sglang/srt/entrypoints/v1_loads.py": "c0dbf2fc90fe24dd568bb7f88e8d62d923e77af33b7f85603f7d39c409b8930e",
    "python/sglang/srt/entrypoints/elastic_ep.py": "64b73e41deff7f54772baa6f50248723ff56ee3ef21af5a3505d12a93a8c87a9",
}
METHODS = {"get", "post", "put", "delete", "head", "patch", "options", "trace", "api_route"}
HERE = Path(__file__).resolve().parent
LEGACY = HERE / "엔진별_적용검토/SGLang_v0.5.22_HTTP_route목록.json"
DEFAULT_OUTPUT = HERE / "엔진별_적용검토/SGLang_v0.5.22_HTTP_정적후보_v2.json"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def url(path, line=None):
    return f"https://github.com/sgl-project/sglang/blob/{COMMIT}/{path}" + (f"#L{line}" if line else "")


def literal(node):
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return None


def expression(node):
    return ast.unparse(node)


def module_inventory(text, path):
    """Return source declarations, preserving lexical guards without evaluating them."""
    tree = ast.parse(text, filename=path)
    receivers = set()
    imports = {}
    routes, includes, unresolved = [], [], []
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call):
            if expression(n.value.func).split(".")[-1] in {"FastAPI", "APIRouter"}:
                receivers.update(t.id for t in n.targets if isinstance(t, ast.Name))
    def walk(nodes, conditions=(), scope=()):
        for n in nodes:
            if isinstance(n, ast.ImportFrom):
                for a in n.names:
                    imports[a.asname or a.name] = {
                        "module": n.module, "symbol": a.name, "level": n.level,
                        "line": n.lineno, "conditions": list(conditions),
                    }
            elif isinstance(n, ast.If):
                guard = {"expression": expression(n.test), "branch": "if", "source_path": path,
                         "line": n.lineno, "source_url": url(path, n.lineno)}
                walk(n.body, conditions + (guard,), scope)
                if n.orelse:
                    walk(n.orelse, conditions + ({**guard, "branch": "else"},), scope)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                regs = []
                decorators = [expression(d) for d in n.decorator_list]
                for d in n.decorator_list:
                    if not (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                            and isinstance(d.func.value, ast.Name) and d.func.value.id in receivers
                            and d.func.attr in METHODS):
                        continue
                    kws = {k.arg: k.value for k in d.keywords}
                    p = d.args[0] if d.args else kws.get("path")
                    val = literal(p) if p is not None else None
                    methods = literal(kws["methods"]) if "methods" in kws else (
                        [d.func.attr.upper()] if d.func.attr != "api_route" else None)
                    regs.append({"expression": expression(d), "line": d.lineno,
                                 "source_url": url(path, d.lineno), "receiver": d.func.value.id,
                                 "decorator_method": d.func.attr, "methods_declared": methods,
                                 "path_expression": expression(p) if p is not None else None,
                                 "path_literal": val if isinstance(val, str) else None,
                                 "dynamic_path": not isinstance(val, str),
                                 "path_parameters": isinstance(val, str) and "{" in val})
                if regs:
                    routes.append({"source_function_id": f"{path}:{n.lineno}:{'.'.join(scope + (n.name,))}",
                                   "source_path": path, "function": n.name, "line": n.lineno,
                                   "source_url": url(path, n.lineno), "lexical_scope": list(scope),
                                   "activation_conditions": list(conditions), "decorators": decorators,
                                   "registrations": regs,
                                   "auth_decorator_declarations": [d for d in decorators if d.startswith("auth_level(")],
                                   "effective_auth_status": "unverified", "runtime_registration_status": "unverified"})
                walk(n.body, conditions, scope + (n.name,))
            elif isinstance(n, ast.ClassDef):
                walk(n.body, conditions, scope + (n.name,))
            elif isinstance(n, ast.Expr) and isinstance(n.value, ast.Call):
                c = n.value
                if isinstance(c.func, ast.Attribute) and isinstance(c.func.value, ast.Name) and c.func.value.id in receivers:
                    if c.func.attr == "include_router":
                        alias = expression(c.args[0]) if c.args else None
                        includes.append({"source_path": path, "line": n.lineno, "source_url": url(path, n.lineno),
                                         "expression": expression(c), "router_alias": alias,
                                         "import": imports.get(alias), "activation_conditions": list(conditions),
                                         "lexical_scope": list(scope),
                                         "keyword_expressions": {k.arg: expression(k.value) for k in c.keywords}})
                    elif c.func.attr in {"add_api_route", "add_route", "mount"}:
                        unresolved.append({"source_path": path, "line": n.lineno, "expression": expression(c),
                                           "reason": "non_decorator_registration_requires_manual_review"})
            elif isinstance(n, (ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith, ast.Match)):
                # Do not silently flatten guards which this extractor cannot interpret.
                unresolved.append({"source_path": path, "line": n.lineno,
                                   "reason": "control_flow_requires_manual_review", "node_type": type(n).__name__})
                for _, value in ast.iter_fields(n):
                    if isinstance(value, list) and value and all(isinstance(x, ast.stmt) for x in value):
                        walk(value, conditions + ({"expression": type(n).__name__, "branch": "unresolved",
                                                  "source_path": path, "line": n.lineno,
                                                  "source_url": url(path, n.lineno)},), scope)
    walk(tree.body)
    # Non-route function bodies contain many ordinary loops/try blocks. Only report
    # unsupported flow if it can contain a route registration or include statement.
    unsupported_nodes = {n.lineno: n for n in ast.walk(tree) if isinstance(n, (ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith, ast.Match))}
    def contains_registration(node):
        return any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                   and isinstance(c.func.value, ast.Name) and c.func.value.id in receivers
                   and c.func.attr in METHODS | {"include_router", "add_api_route", "add_route", "mount"}
                   for c in ast.walk(node))
    unresolved = [u for u in unresolved if u["reason"] != "control_flow_requires_manual_review" or
                  contains_registration(unsupported_nodes[u["line"]])]
    return {"routes": routes, "includes": includes, "unresolved": unresolved}


def build_inventory(source_root, pinned=True):
    root = Path(source_root)
    sources, modules, candidates, include_sites, unresolved = {}, {}, [], [], []
    def visit(path, chain=(), stack=()):
        if path in stack:
            raise ValueError(f"include_router cycle: {path}")
        data = (root / path).read_bytes()
        digest = sha(data)
        if pinned and PIN.get(path) != digest:
            raise ValueError(f"source pin mismatch or unpinned module: {path}")
        sources[path] = {"sha256": digest, "url": url(path)}
        module = modules.setdefault(path, module_inventory(data.decode("utf-8"), path))
        unresolved.extend(module["unresolved"])
        for r in module["routes"]:
            suffix = "".join(f"@{c['source_path']}:{c['line']}" for c in chain)
            candidates.append({"candidate_id": r["source_function_id"] + suffix, **r, "include_chain": list(chain)})
        for inc in module["includes"]:
            include_sites.append({**inc, "parent_include_chain": list(chain)})
            imp = inc["import"]
            if not imp or imp["level"] or not imp["module"]:
                unresolved.append({**inc, "reason": "include_router_import_unresolved"})
                continue
            child = "python/" + imp["module"].replace(".", "/") + ".py"
            if not (root / child).is_file():
                unresolved.append({**inc, "reason": "include_router_source_unavailable", "module_path": child})
                continue
            visit(child, chain + (inc,), stack + (path,))
    visit(MAIN)
    legacy = json.loads(LEGACY.read_text())
    old_keys = [(r["function"], r["line"]) for r in legacy["routes"]]
    baseline = []
    for old in legacy["routes"]:
        found = [r for r in candidates if not r["include_chain"] and r["function"] == old["function"] and r["line"] == old["line"]]
        if len(found) != 1:
            raise ValueError(f"legacy source mapping differs: {old['function']}")
        r = found[0]
        if r["decorators"] != old["decorators"] or [d["expression"] for d in r["registrations"]] != old["registration"]:
            raise ValueError(f"legacy declaration changed: {old['function']}")
        baseline.append({"legacy_index": len(baseline), "candidate_id": r["candidate_id"]})
    counts = {
        "legacy_function_rows": len(old_keys),
        "main_module_function_candidates": sum(not r["include_chain"] for r in candidates),
        "included_module_function_candidates": sum(bool(r["include_chain"]) for r in candidates),
        "unique_source_functions": len({r["source_function_id"] for r in candidates}),
        "candidate_instances": len(candidates),
        "decorator_registration_statements": sum(len(r["registrations"]) for r in candidates),
        "include_router_sites": len(include_sites),
        "actual_api_count": None, "actual_active_route_count": None,
    }
    return {"schema_version": "2.0", "kind": "static_http_route_candidates", "engine": "SGLang", "version": "v0.5.22",
            "source_commit": COMMIT, "reviewed_at": "2026-10-27", "source_files": sources,
            "extraction_scope": "Python AST decorator candidates and local include_router declarations; guards/paths preserved, source not executed",
            "counts": counts, "runtime_registration_status": "unverified", "effective_auth_status": "unverified",
            "legacy_inventory": {"path": LEGACY.name, "sha256": sha(LEGACY.read_bytes()), "unchanged_rows": baseline},
            "include_router_sites": include_sites, "unresolved_static_sites": unresolved, "candidates": candidates}


def validate_inventory(d):
    """Check pinned artifact invariants; independent of an installed engine."""
    def require(ok, message):
        if not ok:
            raise ValueError(message)
    require(d["schema_version"] == "2.0" and d["source_commit"] == COMMIT, "inventory version/pin")
    require({k: v["sha256"] for k, v in d["source_files"].items()} == PIN, "source file hashes")
    cs = d["candidates"]
    require(len({c["candidate_id"] for c in cs}) == len(cs), "duplicate candidate identity")
    require(len(cs) == 86 and len({c["source_function_id"] for c in cs}) == 86, "candidate function count")
    require(sum(len(c["registrations"]) for c in cs) == 91, "registration count")
    require(len(d["include_router_sites"]) == 2 and not d["unresolved_static_sites"], "unresolved/include sites")
    require(d["runtime_registration_status"] == "unverified" and d["effective_auth_status"] == "unverified", "static/runtime boundary")
    require(d["counts"] == {"legacy_function_rows": 80, "main_module_function_candidates": 83,
            "included_module_function_candidates": 3, "unique_source_functions": 86, "candidate_instances": 86,
            "decorator_registration_statements": 91, "include_router_sites": 2, "actual_api_count": None,
            "actual_active_route_count": None}, "counts/static runtime boundary")
    require(d["legacy_inventory"]["sha256"] == sha(LEGACY.read_bytes()), "legacy inventory bytes")
    mapping = d["legacy_inventory"]["unchanged_rows"]
    require(len(mapping) == 80 and [x["legacy_index"] for x in mapping] == list(range(80)), "legacy mapping")
    byid = {c["candidate_id"]: c for c in cs}
    for old, m in zip(json.loads(LEGACY.read_text())["routes"], mapping):
        r = byid[m["candidate_id"]]
        require((r["function"], r["line"], r["decorators"], [x["expression"] for x in r["registrations"]]) ==
                (old["function"], old["line"], old["decorators"], old["registration"]), "legacy row changed")
    require(all(c["runtime_registration_status"] == c["effective_auth_status"] == "unverified" for c in cs), "candidate runtime status")
    named = {c["function"]: c for c in cs}
    a, b = named["ollama_root"], named["sglang_root"]
    require(a["activation_conditions"][0]["expression"] == b["activation_conditions"][0]["expression"] == "_ollama_root_route is not None" and
            (a["activation_conditions"][0]["branch"], b["activation_conditions"][0]["branch"]) == ("if", "else"), "mutually exclusive roots")
    require([r["decorator_method"] for r in a["registrations"]] == ["get", "head"], "HEAD declaration")
    dump = named["_dumper_control_handler"]
    require(dump["activation_conditions"][0]["expression"] == "os.environ.get('DUMPER_SERVER_PORT') == 'reuse'" and
            dump["registrations"][0]["path_literal"] == "/dumper/{method}" and dump["registrations"][0]["path_parameters"] and
            dump["auth_decorator_declarations"] == ["auth_level(AuthLevel.ADMIN_OPTIONAL)"], "conditional dumper")
    require(a["registrations"][0]["dynamic_path"] and named["ollama_chat"]["registrations"][0]["dynamic_path"], "dynamic path")
    require(all(len(named[n]["include_chain"]) == 1 for n in ["get_loads", "scale_elastic_ep", "is_scaling_elastic_ep"]), "included router candidates")
    return d["counts"]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-root", type=Path, help="pinned upstream checkout; text parsing only")
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--check", action="store_true", help="compare generated artifact instead of writing")
    p.add_argument("--validate-only", action="store_true", help="validate stored inventory without upstream checkout")
    a = p.parse_args()
    if a.validate_only:
        counts = validate_inventory(json.loads(a.output.read_text()))
        method = "stored_inventory_validation"
    else:
        if not a.source_root:
            p.error("--source-root is required unless --validate-only is used")
        method = "source_ast_regeneration"
        d = build_inventory(a.source_root)
        counts = validate_inventory(d)
        rendered = json.dumps(d, ensure_ascii=False, indent=2) + "\n"
        if a.check:
            if a.output.read_text() != rendered:
                raise SystemExit("stored inventory differs from pinned source extraction")
        else:
            a.output.write_text(rendered)
    print(json.dumps({"passed": True, "method": method, "counts": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
