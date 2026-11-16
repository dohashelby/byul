"""Every MVPConfig field is actually read outside its validation module."""

import ast
from dataclasses import fields
from pathlib import Path
import unittest

from serving_runtime.config.config import MVPConfig

ROOT = Path(__file__).resolve().parents[1] / "src/serving_runtime"
KNOWN_NON_CONSUMED = {}


class ConfigFieldConsumptionTests(unittest.TestCase):
    def test_all_fields_are_read_outside_config_or_explicitly_explained(self):
        expected = {field.name for field in fields(MVPConfig)}
        # All 57 fields are consumed here; request-flow/relay bodies need not be opened.
        tree = ast.parse((ROOT / "gateway/app.py").read_text())
        read = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Load)
            and (
                (isinstance(node.value, ast.Name) and node.value.id == "config")
                or (isinstance(node.value, ast.Attribute) and node.value.attr == "config")
            )
        }
        self.assertTrue(all(reason.strip() for reason in KNOWN_NON_CONSUMED.values()))
        self.assertEqual(expected - read, set(KNOWN_NON_CONSUMED), "unconsumed field or stale exception")
