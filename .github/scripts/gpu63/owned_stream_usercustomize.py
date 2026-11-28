"""Start supplemental observers only in the named owned audit Pods."""

import os
import sys


if os.environ.get("BYUL_OWNED_STREAM_POD_UID"):
    try:
        role = os.environ.get("BYUL_OWNED_STREAM_ROLE")
        if role == "engine":
            from owned_stream_observer import install
        elif role == "probe":
            from owned_stream_probe_observer import install
        else:
            raise ValueError("unbound observer role")

        install()
    except (AttributeError, KeyError, OSError, RuntimeError, SyntaxError, TypeError, ValueError) as error:
        sys.stderr.write(f"owned stream observer unavailable ({type(error).__name__})\n")
