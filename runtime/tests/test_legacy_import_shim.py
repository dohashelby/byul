"""Temporary legacy-path contract; remove together with LEGACY_SHIMS.md."""

from __future__ import annotations

import ast
import importlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

RUNTIME = Path(__file__).resolve().parents[1]
MODULES = json.loads((RUNTIME / "scripts/module_map.json").read_text(encoding="utf-8"))["modules"]
LEGACY_PREFIXES = (
    "serving_runtime.mvp",
    "serving_runtime.bundle_tools",
    "serving_runtime.file_info",
)


def is_legacy(name: str) -> bool:
    return any(name == prefix or name.startswith(prefix + ".") for prefix in LEGACY_PREFIXES)


class LegacyImportShimTests(unittest.TestCase):
    def test_all_legacy_imports_return_the_same_module(self):
        for old, new in MODULES.items():
            with self.subTest(old=old, new=new):
                self.assertIs(importlib.import_module(old), importlib.import_module(new))

    def test_package_export_uses_the_new_config_module(self):
        from serving_runtime.mvp import MVPConfig

        self.assertEqual(MVPConfig.__module__, "serving_runtime.config.config")

    def test_patch_through_the_legacy_path_reaches_the_new_module(self):
        new = importlib.import_module("serving_runtime.bridge_update.deployment_guard")
        original = new.verify_deployment_guard
        replacement = object()
        with patch("serving_runtime.mvp.deployment_guard.verify_deployment_guard", replacement):
            self.assertIs(new.verify_deployment_guard, replacement)
        self.assertIs(new.verify_deployment_guard, original)

    def test_private_name_remains_available_at_the_legacy_path(self):
        old = importlib.import_module("serving_runtime.mvp.target")
        new = importlib.import_module("serving_runtime.target.target")
        self.assertTrue(callable(old._parse))
        self.assertIs(old._parse, new._parse)

    def test_module_shims_contain_only_alias_boilerplate(self):
        for old, new in MODULES.items():
            path = RUNTIME / "src" / Path(*old.split(".")).with_suffix(".py")
            body = ast.parse(path.read_text(encoding="utf-8")).body
            with self.subTest(module=old):
                self.assertEqual(len(body), 4)
                self.assertIsInstance(body[0], ast.Expr)
                self.assertIsInstance(body[0].value, ast.Constant)
                self.assertIsInstance(body[0].value.value, str)
                expected = ast.parse(
                    f'import sys\nfrom importlib import import_module\nsys.modules[__name__] = import_module("{new}")\n'
                )
                actual = ast.Module(body=body[1:], type_ignores=[])
                self.assertEqual(
                    ast.dump(actual, include_attributes=False),
                    ast.dump(expected, include_attributes=False),
                )

    def test_runtime_does_not_use_legacy_imports_or_module_strings(self):
        shims = {RUNTIME / "src" / Path(*old.split(".")).with_suffix(".py") for old in MODULES}
        shims.add(RUNTIME / "src/serving_runtime/mvp/__init__.py")
        findings = []
        for directory in ("src", "tools", "tests"):
            for path in sorted((RUNTIME / directory).rglob("*.py")):
                if path in shims or path == Path(__file__).resolve() or "__pycache__" in path.parts:
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8"))
                package = None
                if directory == "src":
                    parts = list(path.relative_to(RUNTIME / "src").with_suffix("").parts)
                    package = ".".join(parts[:-1])
                for node in ast.walk(tree):
                    names = []
                    if isinstance(node, ast.Import):
                        names = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        module = node.module or ""
                        if node.level and package:
                            module = importlib.util.resolve_name("." * node.level + module, package)
                        names = [module, *(module + "." + alias.name for alias in node.names)]
                    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                        if any(prefix in node.value for prefix in LEGACY_PREFIXES):
                            findings.append(f"{path.relative_to(RUNTIME)}:{node.lineno}: legacy string")
                    for name in names:
                        if is_legacy(name):
                            findings.append(f"{path.relative_to(RUNTIME)}:{node.lineno}: {name}")
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
