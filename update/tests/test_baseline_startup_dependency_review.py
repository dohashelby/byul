"""Review the legacy qualification path before accepting the V1 helper revision.

The fixture is the exact helper source before commit 8bac1ab3. Its old digest
was pinned by the startup composition. No engine code or module body is run.
"""
import ast
import hashlib
from pathlib import Path

from guard.adapters import vllm_baseline_startup_composition as composition
from guard.adapters import vllm_rank_ipc_sources as sources


FIXTURE = Path(__file__).parent / 'fixtures/baseline-startup-review/vllm_rank_ipc_sources.before-v1.py.txt'
OLD_SHA256 = 'bfa843defab00d0cf5a44077fd3729edb6d7aed0611d558d23317331da1a2a59'


def functions(raw):
    return {node.name: node for node in ast.parse(raw).body
            if isinstance(node, ast.FunctionDef)}


def body(nodes):
    return ast.dump(ast.Module(body=nodes, type_ignores=[]), include_attributes=False)


def test_startup_dependencies_match_exact_current_source_bytes():
    root = Path(composition.__file__).parent
    assert len(composition.DEPENDENCIES) == 7
    for logical, (_, digest) in composition.DEPENDENCIES.items():
        assert hashlib.sha256((root / (logical + '.py')).read_bytes()).hexdigest() == digest, logical


def test_shared_source_and_callable_guards_are_unchanged_from_reviewed_helper():
    old = FIXTURE.read_bytes()
    assert hashlib.sha256(old).hexdigest() == OLD_SHA256
    previous = functions(old)
    current = functions(Path(sources.__file__).read_bytes())
    for name in ('_identity', '_bytes', '_codes', '_function'):
        assert ast.dump(previous[name], include_attributes=False) == ast.dump(current[name], include_attributes=False), name


def test_legacy_qualification_keeps_exact_gates_body_checks_and_refusal():
    previous = functions(FIXTURE.read_bytes())['qualify']
    current = functions(Path(sources.__file__).read_bytes())
    old_try = previous.body[1]
    new_try = current['qualify'].body[1]
    assert isinstance(old_try, ast.Try) and isinstance(new_try, ast.Try)
    split = next(index for index, node in enumerate(old_try.body)
                 if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == 'runtime'
                         for target in node.targets))
    assert body(old_try.body[:split]) == body(new_try.body[:-1])
    assert body(old_try.body[split:]) == body(current['_qualified'].body[1:])
    expected = ast.parse('return _qualified(module, profile, scope)').body[0]
    assert ast.dump(new_try.body[-1], include_attributes=False) == ast.dump(expected, include_attributes=False)
    assert body(old_try.handlers) == body(new_try.handlers)
