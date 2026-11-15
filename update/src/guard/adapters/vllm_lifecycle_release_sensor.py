"""Release rows for the immutable native /8 base (user decision M-A6-V6, 2026-11-15).

The /8 base accepts one exact source row per module. This profile configures a
private copy of that exact base with the reviewed exact rows of one other
engine release; it adds no observation code. Modules without a reviewed row for
that release keep their previous pin, so the base records them as unverified
and leaves them uninstrumented. No engine imports or installation occur on
module import.
"""
from __future__ import annotations
import hashlib
import os
import stat
import sys
import types

PROFILE = "vllm-native-lifecycle-metadata/25"
SCHEMA = "guard-native-lifecycle-events/25"
BASE_SHA256 = "e537d1d1b19813f171aa30b9344c1dbb49139eed3dea60bfa6dc71744d0d4f39"
# vLLM source commit of the release whose installed bytes these rows are
# (tag source equal to the image installation, docs/dev-1/ma3-image-qualification-20261114.json).
COMMIT = "ced6857afa0ea7b2e3f0846a62e1394e90f15607"
# Exact installed bytes (sha256) per module. Review: docs/dev-1/ma6-version-applicability.md 22.
RELEASE_ROWS = {
 "vllm.entrypoints.generate.base.serving": "35ec53f63e86a8c1725728911db0df44eb3101d8cd65e9b2f39d048956977bad",
 "vllm.entrypoints.serve.engine.serving": "70143311acc2230b925e7f3603b8b9314f029ff5e98d5af990b3bdca691331f1",
 "vllm.entrypoints.serve.middleware.authenticate": "0633540cb788738dfee5a69075fbdc30eb98a510e26f69a0b43c57c50d9ef5ab",
 "vllm.entrypoints.openai.models.serving": "2028c0f57d27cd8bb02ab25449855fbd5ae46818b4fb13217369f8feb0abd029",
 "vllm.model_executor.model_loader.weight_utils": "7443e55184777dcc99f621b0d97d9149d52c2ea8b4304c6621eb4902e1c8ba5b",
 "vllm.v1.engine.input_processor": "c8abd8e4e14d99929d45a7ff3b2a74898269c5e2a7fa30b826623ee85e4ce813",
 "vllm.v1.engine.output_processor": "e47b86cd69ce1c1e3f53dac655eaa75c32a4057d10f4a1db16bb83a397f2a329",
 "vllm.v1.engine.async_llm": "52cbf404f1dbc99c6bebf4a50a7eb18ff842a1f53106a45f365523192d816be1",
 "vllm.v1.engine.core": "268bf40534a853812867ae8e80040f190983cb364b41182b55969aace20c93f5",
 "vllm.v1.core.sched.scheduler": "c1db45f3bbad3a875dd8638331b8870c4380ac1ab4734265ba5c863833a82884",
 "vllm.v1.core.sched.output": "3a66c8efaefe10c572f6f33cf1f8aa4610758c945130cad62eb9f7331005677c",
 "vllm.v1.core.kv_cache_manager": "58800535eda854580d84e1025bf32fe01b2a43ce0bba501b4f3d125e4f2c8afa",
 "vllm.v1.core.single_type_kv_cache_manager": "0233531d85aeced9e57a38c76d83db970519c92902b7e55484390cb2b64985bf",
 "vllm.v1.core.block_pool": "34c2fd67148cecb7763ff49ad694a57073759b0fa67572a4270dc995b4acf8fc",
 "vllm.v1.worker.gpu_model_runner": "87c29d08c0bbf66993d8b984811e7325e35e6436242a773feb55ec88f16b2c51",
 "vllm.v1.worker.gpu_input_batch": "18cf455c9d0eea61cc581c7932f2e6cd09786914e82dd35eef1c1430da359a0f",
 "vllm.v1.worker.cpu_model_runner": "92111aba92f00571f1a3c4c6e43cf1da6ccb4d2afac2f4d5abc018a4a6e7e8bd",
 "vllm.v1.request": "da610d5203e72d2d524adc5fb39df11502a23520f2f31d9a44cf13d343f9c25d",
}
_BASE = None


def source_rows(base_sources):
    """The base table with this release's reviewed rows; every other module keeps its previous pin."""
    if set(RELEASE_ROWS) - set(base_sources):
        raise ValueError("LIFECYCLE_RELEASE_ROWS_UNAVAILABLE")
    return {**base_sources, **RELEASE_ROWS}


def _dependency():
    directory = os.open('/guard-lifecycle', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open('guard_lifecycle_base.py', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 262144:
            raise ValueError('LIFECYCLE_RELEASE_BASE_UNAVAILABLE')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(262145)
        after = os.fstat(fd)
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if (any(getattr(before, k) != getattr(after, k) for k in fields) or len(raw) != before.st_size
                or hashlib.sha256(raw).hexdigest() != BASE_SHA256):
            raise ValueError('LIFECYCLE_RELEASE_BASE_UNAVAILABLE')
        return raw
    finally:
        os.close(fd)


def configure(base):
    """Configure only a private exact-byte /8 module; the installed /8 stays unchanged."""
    if sys.implementation.name != 'cpython' or sys.version_info[:2] != (3, 12) or sys.flags.optimize != 0:
        raise ValueError('LIFECYCLE_RELEASE_RUNTIME_UNAVAILABLE')
    base.SOURCE_SHA = source_rows(base.SOURCE_SHA)
    base.PROFILE, base.SCHEMA, base.COMMIT = PROFILE, SCHEMA, COMMIT
    base.__file__ = __file__
    return base


def _private_base():
    raw = _dependency()
    base = types.ModuleType('_guard_lifecycle_release_private_base')
    base.__file__ = '/guard-lifecycle/guard_lifecycle_base.py'
    exec(compile(raw, base.__file__, 'exec', dont_inherit=True, optimize=0), base.__dict__)
    return base


def install(path=None):
    global _BASE
    if _BASE is not None:
        return False
    try:
        base = configure(_private_base())
        if not base.install(path):
            return False
        _BASE = base
        return True
    except Exception:
        return False


class Middleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if _BASE is None:
            return await self.app(scope, receive, send)
        return await _BASE.Middleware(self.app)(scope, receive, send)


def seal(path='/guard-lifecycle/settings.json'):
    return (_BASE if _BASE is not None else _private_base()).seal(path)


if __name__ == '__main__':
    if sys.argv[1:] != ['--seal']:
        raise SystemExit('NATIVE_SENSOR_SEAL_ARGUMENTS_REQUIRED')
    try:
        seal()
    except Exception:
        raise SystemExit('NATIVE_SENSOR_SEAL_UNAVAILABLE') from None
