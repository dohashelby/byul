"""Account management for administrators (no sign-up screen).

Temporary passwords are written to a new 0600 file and never printed.
Examples:
  python -m byul_platform.accounts_cli list
  python -m byul_platform.accounts_cli create --id op1 --role runtime_operator --password-file /root/op1.pw
  python -m byul_platform.accounts_cli disable --id op1
  python -m byul_platform.accounts_cli set-role --id op1 --role approver
  python -m byul_platform.accounts_cli reset-password --id op1 --password-file /root/op1.pw
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .accounts import ROLES, AccountError, AccountStore, write_secret_file
from .auth import AuthService


def default_path() -> Path | None:
    if os.environ.get("BYUL_ACCOUNTS_FILE", "").strip():
        return Path(os.environ["BYUL_ACCOUNTS_FILE"].strip()).expanduser()
    if os.environ.get("PLATFORM_DATA_DIR", "").strip():
        return Path(os.environ["PLATFORM_DATA_DIR"].strip()).expanduser() / "accounts" / "accounts.json"
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--accounts-file", type=Path, default=None,
                        help="기본: BYUL_ACCOUNTS_FILE 또는 PLATFORM_DATA_DIR/accounts/accounts.json")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    for name in ("create", "reset-password"):
        command = commands.add_parser(name)
        command.add_argument("--id", required=True)
        command.add_argument("--password-file", type=Path, required=True, help="임시 비밀번호를 쓸 새 0600 파일")
        if name == "create":
            command.add_argument("--role", required=True, choices=ROLES)
    for name in ("disable", "enable"):
        commands.add_parser(name).add_argument("--id", required=True)
    role = commands.add_parser("set-role")
    role.add_argument("--id", required=True)
    role.add_argument("--role", required=True, choices=ROLES)
    args = parser.parse_args(argv)

    path = args.accounts_file or default_path()
    if path is None:
        parser.error("--accounts-file, BYUL_ACCOUNTS_FILE or PLATFORM_DATA_DIR is required")
    store = AccountStore(path)
    audit = AuthService(accounts=store, audit_path=path.with_name("auth-audit.jsonl")).audit
    store.ensure_bootstrap()
    try:
        if args.command == "list":
            print(json.dumps(store.list(), ensure_ascii=False, indent=1))
            return 0
        if args.command in {"create", "reset-password"}:
            if args.password_file.exists():
                raise AccountError("PASSWORD_FILE_EXISTS")
            secret = store.create(args.id, args.role) if args.command == "create" else store.reset_password(args.id)
            write_secret_file(args.password_file, secret)
        elif args.command in {"disable", "enable"}:
            store.set_enabled(args.id, args.command == "enable")
        else:
            store.set_role(args.id, args.role)
    except AccountError as error:
        print(f"account: {error.code}", file=sys.stderr)
        return 1
    except OSError:
        print("account: file unavailable (existing password files are never overwritten)", file=sys.stderr)
        return 1
    audit("account_" + args.command.replace("-", "_"), username=args.id)
    print(json.dumps({"ok": True, "command": args.command, "id": args.id}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
