"""Offline candidate generation and explicit human publication; never auto-approve."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

from serving_runtime.mvp.assets import snapshot, _walk, load_manifest
from serving_runtime.mvp.collectors import load_declaration
from serving_runtime.mvp.policy import _object_without_duplicates, _reject_json_constant
from serving_runtime.mvp.policy_state import load_revocations

SCHEMA = "runtime-verification-candidate/1.0"
FILENAMES = {"assets": "asset-manifest.json", "baseline": "baseline.json",
             "manifest": "manifest.json", "revocations": "revocations.json"}


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(document):
    return (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      object_pairs_hook=_object_without_duplicates,
                      parse_constant=_reject_json_constant)


def plain_path(path):
    path = Path(os.path.abspath(path))
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("SYMLINK_PATH")
    return path


def atomic_write(path, raw, *, replace=False):
    """Publish complete 0640 bytes in a prepared setgid directory, without following links."""
    path = plain_path(path)
    info = path.parent.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
        raise ValueError("UNSAFE_OUTPUT_DIRECTORY")
    fd, temporary = tempfile.mkstemp(prefix=".verification-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o640)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)  # atomic, refuses an existing candidate/approval
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def candidate(kind, document, *, unconfirmed=None):
    return {"schema_version": SCHEMA, "state": "candidate", "kind": kind,
            "collected_at": now(), "document": document,
            "unconfirmed": unconfirmed or {}}


def assets_candidate(root, gateway_root="/models"):
    # snapshot's safe descriptor-based hashes are reused; reject skipped links too.
    document = snapshot(root)
    _, links, _, errors = _walk(Path(root))
    if links or errors:
        raise ValueError("ASSET_TREE_INCOMPLETE_OR_SYMLINK")
    if not Path(gateway_root).is_absolute():
        raise ValueError("GATEWAY_ROOT_NOT_ABSOLUTE")
    document["root"] = gateway_root
    return candidate("assets", document)


def declaration_candidate(environment_candidate, kind):
    if kind not in {"baseline", "manifest"}:
        raise ValueError("INVALID_DECLARATION_KIND")
    if not isinstance(environment_candidate, dict) or environment_candidate.get("schema_version") != "runtime-environment-candidate/1.0":
        raise ValueError("INVALID_ENVIRONMENT_CANDIDATE")
    fields = environment_candidate.get("declaration_fields", {})
    if not fields:
        raise ValueError("NO_OBSERVED_DECLARATION_FIELDS")
    return candidate(kind, {"schema_version": "runtime-mvp-baseline/1.0", "fields": fields},
                     unconfirmed=environment_candidate.get("unconfirmed", {}))


def validate_document(kind, document):
    # Reuse the Gateway's exact validators; candidate envelopes are never accepted there.
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "document.json"
        path.write_bytes(encode(document))
        if kind == "assets":
            load_manifest(path)
        elif kind in {"baseline", "manifest"}:
            result = load_declaration(path, kind=kind)
            if result.state != "ok" or not result.fields:
                raise ValueError("INVALID_DECLARATION")
        elif kind == "revocations":
            if load_revocations(path).state != "ok":
                raise ValueError("INVALID_REVOCATIONS")
        else:
            raise ValueError("INVALID_KIND")


def approve(path, approved_dir, expected_sha256, reviewer, reason, *, replace=False):
    path = plain_path(path)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256 or not reviewer.strip() or not reason.strip():
        raise ValueError("REVIEW_HASH_OR_ATTESTATION_MISSING")
    value = json.loads(raw, object_pairs_hook=_object_without_duplicates,
                       parse_constant=_reject_json_constant)
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA or value.get("state") != "candidate":
        raise ValueError("NOT_A_CANDIDATE")
    kind, document = value["kind"], value["document"]
    validate_document(kind, document)
    approved_dir = plain_path(approved_dir)
    info = approved_dir.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise ValueError("APPROVAL_DIRECTORY_OWNER_OR_MODE")
    target = approved_dir / FILENAMES[kind]
    if target.exists() and not replace:
        raise ValueError("APPROVAL_EXISTS_REVIEW_REPLACEMENT")
    payload = encode(document)
    receipt = {"schema_version": "runtime-verification-approval/1.0", "kind": kind,
               "approved_at": now(), "reviewer": reviewer, "reason": reason,
               "candidate_sha256": digest, "document_sha256": hashlib.sha256(payload).hexdigest()}
    # A hash-addressed receipt may be left after a crash; the document is published last.
    # An orphan receipt is not an active approval. Compare its document hash to the active file.
    receipt_path = approved_dir / (kind + "-" + digest + ".approval.json")
    if not receipt_path.exists():
        atomic_write(receipt_path, encode(receipt))
    atomic_write(target, payload, replace=replace)
    return digest


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    assets = commands.add_parser("assets")
    assets.add_argument("--root", required=True)
    assets.add_argument("--gateway-root", default="/models")
    assets.add_argument("--out", required=True)
    declaration = commands.add_parser("declaration")
    declaration.add_argument("--kind", choices=("baseline", "manifest"), required=True)
    declaration.add_argument("--environment-candidate", required=True)
    declaration.add_argument("--out", required=True)
    revocations = commands.add_parser("revocations")
    revocations.add_argument("--source", help="existing reviewed format; omitted means an EMPTY CANDIDATE")
    revocations.add_argument("--out", required=True)
    approval = commands.add_parser("approve")
    approval.add_argument("--candidate", required=True)
    approval.add_argument("--approved-dir", default="/srv/serving/verification/approved")
    approval.add_argument("--expect-sha256", required=True)
    approval.add_argument("--reviewer", required=True)
    approval.add_argument("--reason", required=True)
    approval.add_argument("--replace", action="store_true", help="explicitly replace a previously approved input")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "approve":
            digest = approve(args.candidate, args.approved_dir, args.expect_sha256,
                             args.reviewer, args.reason, replace=args.replace)
            print("APPROVED candidate_sha256=" + digest + "; Gateway 재시작 필요(기준/선언/폐기 목록)")
            return 0
        if args.command == "assets":
            value = assets_candidate(args.root, args.gateway_root)
        elif args.command == "declaration":
            value = declaration_candidate(read_json(args.environment_candidate), args.kind)
        else:
            document = read_json(args.source) if args.source else {
                "schema_version": "runtime-mvp-revocations/1.0", "revoked": []}
            value = candidate("revocations", document)
        validate_document(value["kind"], value["document"])
        raw = encode(value)
        atomic_write(args.out, raw)
        print("CANDIDATE_ONLY sha256=" + hashlib.sha256(raw).hexdigest())
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        print("VERIFICATION_MATERIAL_FAILED: 입력·권한·형식을 확인하세요(상세 값 비노출).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
