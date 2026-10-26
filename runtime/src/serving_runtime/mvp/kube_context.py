"""Shared validation for explicitly selected Kubernetes contexts."""

from __future__ import annotations

import re


DEFAULT_KUBE_CONTEXT = "kind-runtime-lab"
_KUBE_CONTEXT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+-]{0,252}")


def validate_kube_context(value: str) -> str:
    """Return a safe argv value for ``--context`` or raise ``ValueError``."""

    if not isinstance(value, str) or _KUBE_CONTEXT.fullmatch(value) is None:
        raise ValueError(
            "kube context must be 1-253 characters using letters, digits, ._:@/+- "
            "and must not start with '-'"
        )
    return value
