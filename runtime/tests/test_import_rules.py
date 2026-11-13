"""Check every static import, including function-local imports and live exceptions."""

import ast
from pathlib import Path
import unittest

PACKAGE = "serving_runtime"
ROOT = Path(__file__).resolve().parents[1] / "src"
RANK = {
    "common": 0,
    "config": 1,
    "policy": 2,
    "target": 2,
    "observation": 2,
    "enforcement": 2,
    "gateway": 3,
    "operations": 4,
    "cli": 4,
    "(top)": 4,
}
BRANCHES = {"devtools", "bridge_update"}
KNOWN_VIOLATIONS = (
    ("cli/demo.py", "serving_runtime.devtools.demo", "WP3에서 CLI 실행 경로 분리"),
    ("cli/demo.py", "serving_runtime.devtools.acceptance", "WP3에서 CLI 실행 경로 분리"),
    (
        "gateway/app.py",
        "serving_runtime.devtools.mock_engine",
        "기존 mock CLI 실행을 유지; 정적 지연 import, 2단계 설계",
    ),
    ("gateway/app.py", "serving_runtime.devtools.lab_report", "기존 GatewayApp 생성 동작 유지; 2단계 설계"),
    ("enforcement/sync_cli.py", "serving_runtime.operations.agent", "python -m 진입점 호환을 위해 위치 유지"),
)
KNOWN_CYCLES = (frozenset({"serving_runtime.enforcement.codeload", "serving_runtime.enforcement.events"}),)


def files():
    result = {}
    for path in sorted((ROOT / PACKAGE).rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        if source.startswith(('"""Legacy import path.', '"""Legacy package.')):
            continue
        parts = list(path.relative_to(ROOT).with_suffix("").parts)
        package = parts[-1] == "__init__"
        if package:
            parts.pop()
        result[".".join(parts)] = (path, ast.parse(source), package)
    return result


def targets(name, tree, package):
    parent = name if package else name.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = parent.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1] + ([base] if base else []))
            for alias in node.names:
                yield base if alias.name == "*" else base + "." + alias.name


def module_target(name, modules):
    while name and name not in modules:
        name = name.rpartition(".")[0]
    return name


def layer(name):
    parts = name.split(".")
    if len(parts) < 2:
        return "(top)"
    return parts[1] if parts[1] in RANK or parts[1] in BRANCHES else "(top)"


def inspect_imports():
    modules = files()
    graph = {name: set() for name in modules}
    violations = set()
    for name, (path, tree, package) in modules.items():
        source_layer = layer(name)
        relative = path.relative_to(ROOT / PACKAGE).as_posix()
        for imported in targets(name, tree, package):
            if not (imported == PACKAGE or imported.startswith(PACKAGE + ".")):
                continue
            target = module_target(imported, modules)
            if not target:
                continue
            graph[name].add(target)
            target_layer = layer(target)
            bad = source_layer == "common"
            if target_layer == "devtools" and source_layer not in {"devtools", "operations"}:
                bad = True
            if target_layer == "bridge_update" and source_layer in {
                "common",
                "config",
                "policy",
                "target",
                "observation",
                "enforcement",
                "gateway",
            }:
                bad = True
            if source_layer in RANK and target_layer in RANK and RANK[source_layer] < RANK[target_layer]:
                bad = True
            if bad:
                violations.add((relative, target))
    return violations, graph


def cycles(graph):
    seen, low, stack, active, result = {}, {}, [], set(), set()

    def visit(node):
        seen[node] = low[node] = len(seen)
        stack.append(node)
        active.add(node)
        for target in graph[node]:
            if target not in seen:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in active:
                low[node] = min(low[node], seen[target])
        if low[node] == seen[node]:
            component = set()
            while True:
                item = stack.pop()
                active.remove(item)
                component.add(item)
                if item == node:
                    break
            if len(component) > 1 or node in graph[node]:
                result.add(frozenset(component))

    for node in sorted(graph):
        if node not in seen:
            visit(node)
    return result


class ImportRulesTests(unittest.TestCase):
    def test_layer_rules_and_live_exceptions(self):
        actual, _ = inspect_imports()
        expected = {(path, module) for path, module, reason in KNOWN_VIOLATIONS if reason.strip()}
        self.assertEqual(len(expected), len(KNOWN_VIOLATIONS))
        self.assertEqual(actual, expected, "unknown violation or stale exception")

    def test_no_new_or_stale_cycles(self):
        _, graph = inspect_imports()
        self.assertEqual(cycles(graph), set(KNOWN_CYCLES))
