"""Current imports resolve directly; removed legacy aliases cannot return."""

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


class ImportPathTests(unittest.TestCase):
    def test_all_new_modules_resolve_without_legacy_imports(self):
        for old, new in MODULES.items():
            with self.subTest(old=old, new=new):
                module = importlib.import_module(new)
                self.assertEqual(module.__name__, new)
                self.assertTrue(Path(module.__file__).resolve().is_relative_to(RUNTIME / "src"))
                with self.assertRaises(ModuleNotFoundError):
                    importlib.import_module(old)

    def test_package_export_uses_the_new_config_module(self):
        from serving_runtime.config.config import MVPConfig

        self.assertEqual(MVPConfig.__module__, "serving_runtime.config.config")

    def test_patch_through_the_current_path_reaches_the_actual_module(self):
        new = importlib.import_module("serving_runtime.bridge_update.deployment_guard")
        original = new.verify_deployment_guard
        replacement = object()
        with patch("serving_runtime.bridge_update.deployment_guard.verify_deployment_guard", replacement):
            self.assertIs(new.verify_deployment_guard, replacement)
        self.assertIs(new.verify_deployment_guard, original)

    def test_private_name_remains_available_at_the_current_path(self):
        from serving_runtime.target.target import _parse

        new = importlib.import_module("serving_runtime.target.target")
        self.assertTrue(callable(_parse))
        self.assertIs(_parse, new._parse)

    def test_legacy_module_files_and_package_are_absent(self):
        for old in MODULES:
            path = RUNTIME / "src" / Path(*old.split(".")).with_suffix(".py")
            with self.subTest(module=old):
                self.assertFalse(path.exists())
        self.assertFalse((RUNTIME / "src/serving_runtime/mvp").exists())

    def test_runtime_platform_and_update_do_not_use_legacy_imports_or_strings(self):
        findings = []
        roots = [RUNTIME / directory for directory in ("src", "tools", "labtools", "tests")]
        roots.extend((RUNTIME.parent / "platform/api/tests", RUNTIME.parent / "update/tests"))
        for root in roots:
            for path in sorted(root.rglob("*.py")):
                if path == Path(__file__).resolve() or "__pycache__" in path.parts:
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8"))
                package = None
                if root == RUNTIME / "src":
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
                            findings.append(f"{path.relative_to(RUNTIME.parent)}:{node.lineno}: legacy string")
                    for name in names:
                        if is_legacy(name):
                            findings.append(f"{path.relative_to(RUNTIME.parent)}:{node.lineno}: {name}")
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
