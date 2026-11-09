#!/usr/bin/env python3
"""Validate a policy for the selected default text deployment; no deployment occurs."""
from __future__ import annotations

import argparse
import json
import os

from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.selected_profile import validate_selected_profile


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--report-out", help="new JSON report file; existing files are preserved")
    args = parser.parse_args(argv)
    report = validate_selected_profile(load_policy(args.policy))
    raw = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if args.report_out:
        descriptor = os.open(args.report_out, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
    print(raw.decode("utf-8"), end="")
    return 0 if report["supported"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
