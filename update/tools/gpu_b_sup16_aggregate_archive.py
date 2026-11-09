"""Source-free SUP16 aggregate callback; S2 supplies actual fixed inputs.

Only the original archive method's historical health path is substituted.
This module does not start phases, make proofs, or operate resources.
"""
import ast
import datetime
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import types
import zipfile

CONTROLLER_SHA = '02899e3e8d9f46516eb09855f516ae074780c312988571f2f8fba20f15569b52'
PROOF_KEYS = frozenset(('configuration_sha256', 'source', 'boot', 'node_uid', 'timer_raw_sha256'))
FRAME_KEYS = frozenset(('ROOT', 'RUNTIME', 'CODE', 'HEALTH', 'ARCHIVE', 'RUN'))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def make_aggregate_archive(*, controller_raw, frame, configuration_pointer,
                           preflight_pointer, expected_preflight,
                           selected_system_python):
    """Return the zero-argument callback expected by OuterLifecycle.

    Inputs are fixed paths/pins, not asserted current resource identities.
    The caller's original phase supervisor must bound the invocation and
    prove its terminal state. No deadline or positive proof is created here.
    """
    require(type(controller_raw) is bytes and sha(controller_raw) == CONTROLLER_SHA,
            'SUP16_AGGREGATE_EXACT_CONTROLLER_REQUIRED')
    require(set(frame) == FRAME_KEYS, 'SUP16_AGGREGATE_EXACT_FRAME_REQUIRED')
    require(set(expected_preflight) == PROOF_KEYS
            and all(type(v) is str and v for v in expected_preflight.values()),
            'SUP16_AGGREGATE_EXACT_PREFLIGHT_BINDING_REQUIRED')
    fixed = {k: Path(v) if k != 'RUN' else v for k, v in frame.items()}
    require(type(fixed['RUN']) is str and fixed['RUN'], 'SUP16_AGGREGATE_FIXED_RUN_REQUIRED')
    paths = [fixed[k] for k in FRAME_KEYS if k != 'RUN']
    require(all(p.is_absolute() for p in paths) and len(set(paths)) == len(paths),
            'SUP16_AGGREGATE_DISTINCT_ABSOLUTE_PATHS_REQUIRED')
    roots = [fixed[k] for k in ('ROOT', 'RUNTIME', 'CODE', 'HEALTH')]
    require(not any(fixed['ARCHIVE'].is_relative_to(p) for p in roots),
            'SUP16_AGGREGATE_ARCHIVE_OUTSIDE_SNAPSHOT_REQUIRED')
    require(not any(a.is_relative_to(b) or b.is_relative_to(a)
                    for i, a in enumerate(roots) for b in roots[i + 1:]),
            'SUP16_AGGREGATE_DISTINCT_SNAPSHOT_ROOTS_REQUIRED')
    tree = ast.parse(controller_raw)
    wanted = ('require', 'now', 'sha', 'read_regular', 'archive_receipt',
              'system_python_identity', 'validate_cleanup_python_flags')
    defs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted}
    require(set(defs) == set(wanted), 'SUP16_AGGREGATE_ORIGINAL_FUNCTIONS_REQUIRED')
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Controller')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'archive')
    class HealthOnly(ast.NodeTransformer):
        count = 0
        def visit_BinOp(self, node):
            if (isinstance(node.op, ast.Div) and isinstance(node.left, ast.Name)
                    and node.left.id == 'BASE' and isinstance(node.right, ast.Constant)
                    and node.right.value == 'REQ-READY-20261109-V9-HEALTH'):
                self.count += 1
                return ast.copy_location(ast.Name(id='HEALTH', ctx=ast.Load()), node)
            return self.generic_visit(node)
    substitution = HealthOnly()
    method = substitution.visit(method)
    require(substitution.count == 1, 'SUP16_AGGREGATE_ONE_ORIGINAL_HEALTH_SITE_REQUIRED')
    module = ast.fix_missing_locations(ast.Module(body=[defs[k] for k in wanted] + [method], type_ignores=[]))
    namespace = dict(os=os, stat=stat, json=json, hashlib=hashlib, Path=Path,
                     datetime=datetime, zipfile=zipfile,
                     SYSTEM_PY=Path(selected_system_python), **fixed)
    exec(compile(module, 'fixed-028-aggregate-archive-health-substitution', 'exec'), namespace)
    read = namespace['read_regular']
    def pinned(pointer):
        require(type(pointer) is dict and set(pointer) == {'path', 'owner', 'sha256'}
                and type(pointer['owner']) is int and pointer['owner'] == 0,
                'SUP16_AGGREGATE_ROOT_PIN_REQUIRED')
        path = Path(pointer['path'])
        require(path.is_absolute(), 'SUP16_AGGREGATE_ABSOLUTE_PROOF_REQUIRED')
        raw = read(path, 0, 3 * 1024**2)
        require(sha(raw) == pointer['sha256'] and stat.S_IMODE(path.lstat().st_mode) == 0o600,
                'SUP16_AGGREGATE_ROOT600_PIN_REQUIRED')
        return raw, json.loads(raw)
    # Freeze declarations by value; caller mutations do not redirect later I/O.
    config_pin, proof_pin = dict(configuration_pointer), dict(preflight_pointer)
    binding = dict(expected_preflight)
    def invoke():
        require(os.geteuid() == 0, 'SUP16_AGGREGATE_ROOT_PROCESS_REQUIRED')
        _, config = pinned(config_pin)
        _, proof = pinned(proof_pin)
        require(all(proof.get(k) == v for k, v in binding.items())
                and config.get('source') == binding['source'] and proof.get('no_timer_reset') is True,
                'SUP16_AGGREGATE_CURRENT_PREFLIGHT_MISMATCH')
        system = proof.get('system_python')
        require(type(system) is dict and set(system) == {'binary', 'observed'},
                'SUP16_AGGREGATE_SYSTEM_PROOF_REQUIRED')
        observed = system['observed']
        require(type(observed) is dict and {'version', 'flags'}.issubset(observed),
                'SUP16_AGGREGATE_ORIGINAL_OBSERVED_SCHEMA_REQUIRED')
        require(sys.platform == 'linux' and sys.version_info[:2] == (3, 12)
                and not sys.flags.optimize and 'PYTHONOPTIMIZE' not in os.environ,
                'UNOPTIMIZED_LINUX_PY312_REQUIRED')
        actual_flags = {'isolated': sys.flags.isolated, 'no_site': sys.flags.no_site,
                        'dont_write_bytecode': sys.flags.dont_write_bytecode}
        namespace['validate_cleanup_python_flags'](observed['flags'])
        namespace['validate_cleanup_python_flags'](actual_flags)
        require(actual_flags == observed['flags']
                and list(sys.version_info[:3]) == observed['version']
                and Path(sys.executable) == namespace['SYSTEM_PY'],
                'SUP16_AGGREGATE_ORIGINAL_RUNNING_SYSTEM_REQUIRED')
        require(system['binary'] == namespace['system_python_identity'](),
                'SUP16_AGGREGATE_CURRENT_SYSTEM_BINARY_REQUIRED')
        require(not (fixed['ROOT'] / 'req-product-archive-paths.json').exists(),
                'SUP16_AGGREGATE_NO_ROLE_PRODUCT_REGISTRY_REQUIRED')
        # No last-role context/state is accepted or reused.
        return namespace['archive'](types.SimpleNamespace(config=config))
    return invoke
