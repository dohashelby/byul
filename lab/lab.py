"""Backward-compatible sandbox entry point; product runtime is canonical."""
from guard.runtime.sandbox import (
    up, env_document, vllm_env_document, pull_images, _probe_in, _wait, _vllm_plan, _start_engine,
    LAB, NGINX_IMAGE, PYTHON_IMAGE, ENGINE_PORT, GATEWAY_PORT, KNOWN_GATEWAY_ROUTES,
    PROBE_SCRIPT, T, CACHE_ENV, ENGINE_PROFILES, SENTINEL_TARGET, GATEWAY_PROFILES,
)
