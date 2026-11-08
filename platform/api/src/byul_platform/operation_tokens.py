"""Issue an operation token to a private file; print only its hash entry."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys

from .operations import load_tokens


def issue(token_id: str, role: str, server: str | None, token_file: Path) -> dict:
    if role != "agent" and server is not None:
        raise ValueError("server scope is only supported for agents")
    token = secrets.token_urlsafe(32)
    entry = {"id": token_id, "role": role, "sha256": hashlib.sha256(token.encode()).hexdigest()}
    if server is not None:
        entry["server"] = server
    load_tokens(json.dumps([entry]))  # Use the platform's configuration validation.
    fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(token + "\n")
        output.flush()
        os.fsync(output.fileno())
    return entry


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    new = commands.add_parser("new", help="write a new 0600 token file without overwriting")
    new.add_argument("--id", required=True)
    new.add_argument("--role", required=True, choices=("viewer", "runtime_operator", "approver", "agent"))
    new.add_argument("--server")
    new.add_argument("--token-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        entry = issue(args.id, args.role, args.server, args.token_file)
    except (OSError, ValueError, TypeError):
        print("operation token: invalid configuration or token file unavailable; existing files are never overwritten", file=sys.stderr)
        return 1
    print(json.dumps(entry))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
