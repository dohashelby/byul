"""Legacy import path. Points at the moved module; contains no logic. Delete with src/serving_runtime/mvp/ (see LEGACY_SHIMS.md)."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module("serving_runtime.observation.retention")
