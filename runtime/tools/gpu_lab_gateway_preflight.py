"""Offline host checks required BEFORE applying/restarting the GPU Lab Gateway.

Reads only; never creates a directory, a revocation list or an approval. Paths
must be the hostPath values in the final Gateway manifest on its target node.
This checks local prerequisites, not agreement with the live engine or a Pod's
UID permissions. It neither contacts Kubernetes/AWS nor applies manifests.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import stat

from serving_runtime.mvp.policy_state import load_revocations


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    reason_code: str


def _plain_path(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("INVALID_PATH")
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("SYMLINK_PATH")
    return path


def _directory(path, name, prefix):
    try:
        path = _plain_path(path)
        info = path.stat()
        if not stat.S_ISDIR(info.st_mode):
            return Check(name, False, prefix + "_NOT_DIRECTORY")
        return Check(name, True, prefix + "_OK")
    except FileNotFoundError:
        return Check(name, False, prefix + "_NOT_FOUND")
    except NotADirectoryError:
        return Check(name, False, prefix + "_NOT_DIRECTORY")
    except ValueError:
        return Check(name, False, prefix + "_INVALID_PATH")
    except OSError:
        return Check(name, False, prefix + "_READ_ERROR")


def collect(approved_dir, model_root):
    approved = _directory(approved_dir, "verification_directory", "VERIFICATION_DIRECTORY")
    model = _directory(model_root, "model_directory", "MODEL_DIRECTORY")
    checks = [approved, model]
    if not approved.ok:
        checks.append(Check("revocations", False, "REVOCATIONS_CHECK_BLOCKED"))
        return checks
    try:
        path = _plain_path(Path(approved_dir) / "revocations.json")
        info = path.stat()
        # Reject special files before the existing loader can attempt to read them.
        if not stat.S_ISREG(info.st_mode):
            checks.append(Check("revocations", False, "REVOCATIONS_NOT_REGULAR_FILE"))
            return checks
        state = load_revocations(path)  # Gateway's exact strict JSON/schema validation
        checks.append(Check("revocations", state.state == "ok", state.error_code or "REVOCATIONS_OK"))
    except FileNotFoundError:
        checks.append(Check("revocations", False, "REVOCATIONS_NOT_FOUND"))
    except ValueError:
        checks.append(Check("revocations", False, "REVOCATIONS_INVALID_PATH"))
    except OSError:
        checks.append(Check("revocations", False, "REVOCATIONS_READ_ERROR"))
    return checks


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-dir", default="/srv/serving/verification/approved")
    parser.add_argument("--model-root", required=True,
                        help="exact model hostPath in the final Gateway manifest; confirm live engine separately")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    checks = collect(args.approved_dir, args.model_root)
    for check in checks:
        # Fixed labels/codes only. Never echo input paths, JSON or error text.
        print(f"{check.name}: {'ok' if check.ok else 'fail'} [{check.reason_code}]")
    if not all(check.ok for check in checks):
        print("GATEWAY_PREFLIGHT_FAILED: 적용/재시작을 중단하고 승인본·경로를 확인하세요.")
        return 1
    print("GATEWAY_PREFLIGHT_PASSED: 로컬 경로·폐기 목록 형식 확인. 실엔진 경로 일치·Pod 읽기 권한은 별도 확인.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
