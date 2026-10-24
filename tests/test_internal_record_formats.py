"""Keep source schema string constants visible in the internal-format document."""

import ast
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = ROOT / "docs" / "내부_기록형식.md"

# Scope: string constants in Python ASTs under src/.  This deliberately ignores
# tools (stand-alone report producers documented separately), examples/tests,
# URLs, and ordinary field-name strings.  runtime-backend/mock is the one
# versionless internal pseudo schema currently present in src.
SCHEMA_LITERAL = re.compile(
    r"(?:runtime|serving-runtime)-[A-Za-z0-9_.-]+/(?:[0-9]+\.[0-9]+|mock)"
)


class InternalRecordFormatDocumentationTests(unittest.TestCase):
    def test_every_source_schema_literal_is_documented(self) -> None:
        documented = DOCUMENT.read_text(encoding="utf-8")
        discovered: dict[str, list[str]] = {}
        for path in sorted((ROOT / "src").rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            schemas = {
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and SCHEMA_LITERAL.fullmatch(node.value)
            }
            for schema in sorted(schemas):
                discovered.setdefault(schema, []).append(relative)

        self.assertTrue(discovered, "schema scan unexpectedly found no source literals")
        missing = {
            schema: paths
            for schema, paths in discovered.items()
            if schema not in documented
        }
        self.assertEqual({}, missing)


if __name__ == "__main__":
    unittest.main()
