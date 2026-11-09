"""Offline, bounded evidence import. Never extract with tarfile.extract/all.

CLI diagnostics contain counts and fixed codes only. Originals are read only;
output is an immutable snapshot (identical reruns succeed without rewriting).
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import zlib

ROOT = Path(__file__).resolve().parents[2]
VERSION = "1.1"
PATTERNS = {
    "BEARER": rb"(?i)\bbearer\s+[^\s\"']+",
    "PRIVATE_KEY": rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----",
    "AWS_ACCOUNT": rb"(?<![0-9a-fA-F])[0-9]{12}(?![0-9a-fA-F])",
    "INSTANCE_ID": rb"(?i)\bi-(?:[0-9a-f]{17}|[0-9a-f]{8})\b",
    "ACCESS_KEY": rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    "TOKEN": rb"(?i)(?:[\"']?\b(?:[a-z_]*token|api[_-]?key|password|aws_secret_access_key|client_secret)[\"']?\s*[:=]\s*[\"']?)([^\s\"',;}]+)",
    "TOKEN_ARGUMENT": rb"(?i)--(?:[a-z-]*token|api-key|password)\s+[\"']?([^\s\"',;}]+)",
    "JWT": rb"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
    "SERVICE_TOKEN": rb"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]+)",
}
COMPILED = {k: re.compile(v) for k, v in PATTERNS.items()}
PLACEHOLDERS = {b"null", b"none", b"false", b"true", b"redacted", b"<redacted>", b"[redacted]", b"***"}
SYNTHETIC = re.compile(rb'''(?i)(?:["']?\b(?:data_origin|origin_detail|origin|evidence_kind)["']?\s*[:=]\s*["']?(?:EXAMPLE|synthetic|mock|fake_backend_test|actual_test)\b|\bsynthetic\b)''')


class ImportFailure(ValueError):
    """The message is always a constant code, never exception/input text."""


def secret_codes(data: bytes) -> list[str]:
    codes = []
    for code, pattern in COMPILED.items():
        if code.startswith("TOKEN"):
            found = any(m.group(1).lower() not in PLACEHOLDERS for m in pattern.finditer(data))
        else:
            found = pattern.search(data) is not None
        if found:
            codes.append(code)
    return sorted(codes)


def canonical(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def timestamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def safe_name(name: str) -> str:
    # Permit tar's conventional ./ prefix but never normalize traversal away.
    while name.startswith("./"):
        name = name[2:]
    name = name.rstrip("/")
    if not name or not re.fullmatch(r"[A-Za-z0-9._/-]+", name) or any(
        p in ("", ".", "..") for p in name.split("/")
    ) or name.startswith("/") or len(name) > 1024 or len(name.split("/")) > 40:
        raise ImportFailure("UNSAFE_PATH")
    return name


def no_links(path: Path) -> None:
    for p in (path, *path.parents):
        if p.is_symlink():
            raise ImportFailure("SYMLINK")


def open_regular(path=None, *, name=None, dir_fd=None):
    fd = os.open(path if path is not None else name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ImportFailure("SPECIAL_FILE")
    return os.fdopen(fd, "rb")


@dataclass(frozen=True)
class Input:
    run: str
    path: Path
    sha256: str | None = None
    result_doc: Path | None = None
    origin: str | None = None
    window: dict | None = None


@dataclass(frozen=True)
class Limits:
    file_bytes: int = 32 * 1024 * 1024
    total_bytes: int = 256 * 1024 * 1024
    members: int = 10000
    archive_bytes: int = 256 * 1024 * 1024


class Budget:
    def __init__(self, limits):
        self.limits = limits
        self.members = self.bytes = 0

    def add(self, size):
        self.members += 1
        self.bytes += size
        if size < 0 or size > self.limits.file_bytes or self.bytes > self.limits.total_bytes or self.members > self.limits.members:
            raise ImportFailure("SIZE_LIMIT")


class BoundedReader:
    """Also cap decompressed tar headers/padding/PAX before tarfile reads them."""
    def __init__(self, stream, limit):
        self.stream, self.remaining = stream, limit

    def read(self, size=-1):
        data = self.stream.read(min(size if size >= 0 else self.remaining + 1, self.remaining + 1))
        self.remaining -= len(data)
        if self.remaining < 0:
            raise ImportFailure("SIZE_LIMIT")
        return data


def folder_files(path: Path, budget: Budget):
    def walk(fd, prefix=""):
        with os.scandir(fd) as entries:
            names = []
            for entry in entries:
                if len(names) >= budget.limits.members:
                    raise ImportFailure("SIZE_LIMIT")
                names.append(entry.name)
        for name in sorted(names):
            relative = safe_name(prefix + name)
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            budget.add(info.st_size if stat.S_ISREG(info.st_mode) else 0)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    yield from walk(child, relative + "/")
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                with open_regular(name=name, dir_fd=fd) as stream:
                    current = os.fstat(stream.fileno())
                    if current.st_size != info.st_size or current.st_mtime_ns != info.st_mtime_ns:
                        raise ImportFailure("INPUT_CHANGED")
                    data = stream.read(budget.limits.file_bytes + 1)
                    after = os.fstat(stream.fileno())
                    if len(data) != info.st_size or (after.st_ino, after.st_size, after.st_mtime_ns) != (current.st_ino, current.st_size, current.st_mtime_ns):
                        raise ImportFailure("INPUT_CHANGED")
                    yield relative, info.st_mtime, data
            else:
                raise ImportFailure("SYMLINK" if stat.S_ISLNK(info.st_mode) else "SPECIAL_FILE")
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield from walk(fd)
    finally:
        os.close(fd)


def archive_files(stream, budget: Budget):
    with gzip.GzipFile(fileobj=stream) as decoded:
        bounded = BoundedReader(decoded, budget.limits.total_bytes + budget.limits.members * 4096 + 10240)
        with tarfile.open(fileobj=bounded, mode="r|") as archive:
            seen = set()
            for member in archive:
                # ./ as the root folder is harmless, but never a file.
                if member.isdir() and member.name in (".", "./"):
                    budget.add(0)
                    continue
                name = safe_name(member.name)
                if name in seen:
                    raise ImportFailure("DUPLICATE_PATH")
                seen.add(name)
                budget.add(member.size)
                if member.isdir():
                    continue
                if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or member.issparse():
                    raise ImportFailure("SPECIAL_FILE")
                with archive.extractfile(member) as content:
                    data = content.read(budget.limits.file_bytes + 1)
                if len(data) != member.size:
                    raise ImportFailure("INVALID_ARCHIVE")
                yield name, member.mtime, data
            # Validate gzip CRC/truncation and cap trailing decompressed bytes too.
            while bounded.read(65536):
                pass


def document_info(item: Input):
    doc_path = item.result_doc
    if doc_path is None and not item.path.is_dir() and re.fullmatch(r"run[0-9]+", item.run):
        matches = list((ROOT / "runtime/docs").glob(f"AWS_GPU_서버{item.run[3:]}회차_결과_*.md"))
        if len(matches) == 1:
            doc_path = matches[0]
    if doc_path is None:
        return None, None, None
    no_links(doc_path)
    with open_regular(doc_path) as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ImportFailure("DOCUMENT_SIZE_LIMIT")
    text = raw.decode("utf-8")
    lines = text.splitlines()
    run = re.fullmatch(r"run([0-9]+)", item.run)
    heading_run = re.search(r"^#.*서버\s*([0-9]+)회차.*결과", text)
    if run and heading_run and int(run[1]) != int(heading_run[1]):
        raise ImportFailure("DOCUMENT_RUN_MISMATCH")
    run_heading = bool(run and heading_run)
    # Establish the document/input relationship before accepting a digest from
    # anywhere in it. Explicit paths alone do not establish that relationship.
    if item.path.name not in text and not run_heading:
        raise ImportFailure("DOCUMENT_RUN_MISMATCH")
    hashes = {h.lower() for h in re.findall(r"(?i)(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", text)}
    prefix_pattern = re.compile(r"(?i)SHA[- ]?256\s*(?:(?:앞부분|접두)\s*)?[:：`* (]*([0-9a-f]{8,63})(?![0-9a-f])")
    # Old reports also contain commit and bundle prefixes. Prefer SHA-labelled
    # values on the archive line or its continuation, rather than those values.
    archive_lines = [i for i, line in enumerate(lines) if item.path.name in line]
    prefix_context = "\n".join("\n".join(lines[i:i + 2]) for i in archive_lines)
    prefixes = {m[1].lower() for m in prefix_pattern.finditer(prefix_context)}
    if not prefixes:
        prefixes = {m[1].lower() for m in prefix_pattern.finditer(text)}
    window = None
    # Only recognize the documented AWS report convention; otherwise leave null.
    date = re.search(r"\((\d{4}-\d{2}-\d{2})\)", lines[0]) if lines else None
    timing = next((line for line in text.splitlines() if "환경·시간:" in line), "")
    start = re.search(r"(\d{2}:\d{2}:\d{2}) KST 기동 요청", timing)
    end = re.search(r"(\d{2}:\d{2}:\d{2}) stopped 확인", timing)
    if date and start and end:
        beginning = datetime.fromisoformat(f"{date[1]}T{start[1]}+09:00")
        ending = datetime.fromisoformat(f"{date[1]}T{end[1]}+09:00")
        if ending < beginning:
            ending += timedelta(days=1)
        window = {"from": beginning.isoformat(), "until": ending.isoformat()}
    real_report = bool(re.search(r"^# AWS GPU .*서버 .*회차 결과", text))
    return hashes, window, {"sha256": hashlib.sha256(raw).hexdigest(), "real_report": real_report, "hash_prefixes": prefixes}


def verify_hash(item: Input, digest: str, doc_hashes, doc):
    """Verify every supplied type of evidence; explicit input cannot mask a conflict."""
    if item.sha256 and item.sha256 != digest:
        raise ImportFailure("SHA256_MISMATCH")
    basis = []
    if doc_hashes:
        if digest not in doc_hashes:
            raise ImportFailure("SHA256_MISMATCH")
        basis.append("document_full")
    elif doc and doc["hash_prefixes"]:
        if not any(digest.startswith(prefix) for prefix in doc["hash_prefixes"]):
            raise ImportFailure("SHA256_MISMATCH")
        basis.append("document_prefix")
    elif doc and not item.sha256:
        raise ImportFailure("DOCUMENT_HASH_MISSING")
    if item.sha256:
        basis.append("explicit")
    verified = True if doc_hashes or item.sha256 else "prefix_only" if basis else False
    if item.origin == "MEASURED" and verified is not True:
        raise ImportFailure("ORIGIN_REQUIRES_FULL_HASH")
    origin = item.origin or ("MEASURED" if "document_full" in basis and doc["real_report"] else "DECLARED")
    return verified, basis or ["none"], origin


def validate(document, schemas_dir: Path):
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError:
        raise ImportFailure("SCHEMA_DEPENDENCY_MISSING") from None
    schemas = [json.loads((schemas_dir / name).read_text()) for name in ("common.schema.json", "evidence-index.schema.json")]
    # Bundle the only external reference locally. No resolver/network access,
    # including on hosts with jsonschema predating the referencing package.
    def local_refs(value):
        if isinstance(value, dict):
            return {k: (v.replace("common.schema.json#", "#") if k == "$ref" and isinstance(v, str) else local_refs(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [local_refs(v) for v in value]
        return value
    bundled = local_refs(schemas[1])
    bundled["$defs"] = schemas[0]["$defs"]
    checker = FormatChecker()
    if "date-time" not in checker.checkers:
        @checker.checks("date-time", raises=(ValueError, OverflowError))
        def date_time(value):
            if not isinstance(value, str):
                return True  # type is checked by the schema
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value):
                return False
            return datetime.fromisoformat(value).tzinfo is not None
    validator = Draft202012Validator(bundled, format_checker=checker)
    if not validator.is_valid(document):
        raise ImportFailure("SCHEMA_INVALID")


def tree_manifest(root: Path, limits: Limits):
    return [(name, hashlib.sha256(data).hexdigest()) for name, _, data in folder_files(root, Budget(limits))]


def generate(inputs: list[Input], output: Path, *, limits=Limits(), schemas_dir=ROOT / "contracts/schemas"):
    output = output.absolute()
    no_links(output)
    if not inputs or min(limits.file_bytes, limits.total_bytes, limits.members, limits.archive_bytes) <= 0:
        raise ImportFailure("INVALID_INPUT")
    runs = [i.run for i in inputs]
    if len(set(runs)) != len(runs) or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", run) or secret_codes(run.encode()) for run in runs):
        raise ImportFailure("INVALID_RUN")
    for item in inputs:
        no_links(item.path.absolute())
        if output.resolve() == item.path.resolve() or (item.path.is_dir() and output.resolve().is_relative_to(item.path.resolve())) or item.path.resolve().is_relative_to(output.resolve()):
            raise ImportFailure("OUTPUT_OVERLAPS_INPUT")
        if item.origin not in (None, "MEASURED", "DECLARED", "EXAMPLE"):
            raise ImportFailure("INVALID_ORIGIN")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".evidence-", dir=output.parent))
    try:
        entries, summaries = [], []
        budget = Budget(limits)  # all runs share the resource budget
        times = []
        for item in sorted(inputs, key=lambda i: i.run):
            doc_hashes, doc_window, doc = document_info(item)
            if item.sha256 and not re.fullmatch(r"[0-9a-f]{64}", item.sha256):
                raise ImportFailure("INVALID_SHA256")
            window = item.window or doc_window
            excluded, accepted, manifest = Counter(), [], []
            stream = None
            if item.path.is_dir():
                files = folder_files(item.path, budget)
            else:
                stream = open_regular(item.path)
                archive_stat = os.fstat(stream.fileno())
                if archive_stat.st_size > limits.archive_bytes:
                    stream.close()
                    raise ImportFailure("SIZE_LIMIT")
                archive_hash, read_bytes = hashlib.sha256(), 0
                while chunk := stream.read(65536):
                    read_bytes += len(chunk)
                    if read_bytes > limits.archive_bytes:
                        stream.close()
                        raise ImportFailure("SIZE_LIMIT")
                    archive_hash.update(chunk)
                digest = archive_hash.hexdigest()
                try:
                    verified, basis, run_origin = verify_hash(item, digest, doc_hashes, doc)
                except ImportFailure:
                    stream.close()
                    raise
                stream.seek(0)
                files = archive_files(stream, budget)
            try:
                for name, mtime, data in files:
                    digest_file = hashlib.sha256(data).hexdigest()
                    collected = timestamp(mtime)
                    times.append(collected)
                    manifest.append([name, collected, len(data), digest_file])
                    codes = sorted(set(secret_codes(data) + secret_codes(name.encode())))
                    if codes:
                        excluded.update(codes)
                        excluded["FILES"] += 1
                        continue
                    target = stage / "files" / item.run / name
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    with target.open("xb") as dest:
                        os.chmod(target, 0o600)
                        dest.write(data)
                    # Folder manifest verification follows collection; apply
                    # its final origin below after the full manifest is known.
                    origin = run_origin if stream else (item.origin or "DECLARED")
                    if SYNTHETIC.search(data) or re.search(r"(?i)(synthetic|example|mock)", name):
                        origin = "EXAMPLE"
                    media = mimetypes.guess_type(name)[0] or "application/octet-stream"
                    if not re.fullmatch(r"[a-z]+/[a-z0-9.+-]+", media):
                        media = "application/octet-stream"
                    accepted.append({"evidence_id": "ev-" + hashlib.sha256(f"{item.run}/{name}".encode()).hexdigest(),
                                     "path": f"{item.run}/{name}", "bytes": len(data), "sha256": digest_file,
                                     "media_type": media, "collector": {"name": "runtime-evidence-index", "version": VERSION},
                                     "collected_at": collected, "window": window, "source": f"aws-lab {item.run}" if doc and doc["real_report"] else item.run,
                                     "data_origin": origin, "refs": []})
            finally:
                if stream:
                    after = os.fstat(stream.fileno())
                    stream.close()
                    if (after.st_size, after.st_mtime_ns) != (archive_stat.st_size, archive_stat.st_mtime_ns):
                        raise ImportFailure("INPUT_CHANGED")
            if stream is None:
                digest = hashlib.sha256(canonical(sorted(manifest))).hexdigest()
                verified, basis, run_origin = verify_hash(item, digest, doc_hashes, doc)
            for entry in accepted:
                if stream is None and entry["data_origin"] != "EXAMPLE":
                    entry["data_origin"] = run_origin
                entry["refs"] = [f"{'archive' if stream else 'folder-manifest'}-sha256:{digest}"]
                if doc:
                    entry["refs"].append(f"result-document-sha256:{doc['sha256']}")
            entries.extend(accepted)
            summaries.append({"run": item.run, "sha256": digest, "hash_verified": verified,
                              "basis": basis, "origin_assertion": item.origin,
                              "files": len(accepted), "excluded": excluded.pop("FILES", 0), "reason_codes": dict(sorted(excluded.items())),
                              "origins": dict(sorted(Counter(e["data_origin"] for e in accepted).items()))})
        entries.sort(key=lambda e: e["path"])
        if not times:
            raise ImportFailure("NO_FILES")
        document = {"schema_version": "byul-evidence-index/0.1", "producer": "runtime", "created_at": max(times), "items": entries}
        validate(document, schemas_dir)
        (stage / "evidence-index.json").write_bytes(canonical(document))
        (stage / "import-report.json").write_bytes(canonical({"runs": summaries, "schema_valid": True}))
        for file in stage.rglob("*"):
            os.chmod(file, 0o700 if file.is_dir() else 0o600)
        if output.exists():
            metadata_bytes = sum((stage / name).stat().st_size for name in ("evidence-index.json", "import-report.json"))
            compare_limits = Limits(max(limits.file_bytes, metadata_bytes), limits.total_bytes + metadata_bytes, limits.members * 41 + 10, limits.archive_bytes)
            if not output.is_dir() or tree_manifest(output, compare_limits) != tree_manifest(stage, compare_limits):
                raise ImportFailure("OUTPUT_EXISTS_DIFFERENT")
        else:
            stage.rename(output)
        return {"runs": summaries, "schema_valid": True}
    finally:
        if stage.exists():
            shutil.rmtree(stage)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ImportFailure("INVALID_ARGUMENTS")


def mapping(values):
    result = {}
    for value in values:
        key, separator, text = value.partition("=")
        if not separator or not text or key in result:
            raise ImportFailure("INVALID_ARGUMENTS")
        result[key] = text
    return result


def main(argv=None):
    parser = Parser(description="Offline evidence snapshot; diagnostics never include rejected values or paths.")
    parser.add_argument("--input", action="append", required=True, metavar="RUN=PATH")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sha256", action="append", default=[], metavar="RUN=HEX")
    parser.add_argument("--result-doc", action="append", default=[], metavar="RUN=PATH")
    parser.add_argument("--origin", action="append", default=[], metavar="RUN=MEASURED|DECLARED|EXAMPLE")
    parser.add_argument("--window", action="append", default=[], metavar="RUN=FROM,UNTIL")
    parser.add_argument("--max-file-bytes", type=int, default=Limits.file_bytes)
    parser.add_argument("--max-total-bytes", type=int, default=Limits.total_bytes)
    parser.add_argument("--max-members", type=int, default=Limits.members)
    parser.add_argument("--max-archive-bytes", type=int, default=Limits.archive_bytes)
    try:
        args = parser.parse_args(argv)
        paths, hashes, docs, origins, windows = [mapping(v) for v in (args.input, args.sha256, args.result_doc, args.origin, args.window)]
        if any(set(group) - paths.keys() for group in (hashes, docs, origins, windows)):
            raise ImportFailure("UNKNOWN_RUN")
        parsed_windows = {}
        for run, value in windows.items():
            start, end = value.split(",")
            if datetime.fromisoformat(start).tzinfo is None or datetime.fromisoformat(end).tzinfo is None or datetime.fromisoformat(end) < datetime.fromisoformat(start):
                raise ImportFailure("INVALID_WINDOW")
            parsed_windows[run] = {"from": start, "until": end}
        inputs = [Input(run, Path(path).expanduser(), hashes.get(run), Path(docs[run]).expanduser() if run in docs else None, origins.get(run), parsed_windows.get(run)) for run, path in paths.items()]
        report = generate(inputs, args.output, limits=Limits(args.max_file_bytes, args.max_total_bytes, args.max_members, args.max_archive_bytes))
        print(json.dumps(report, sort_keys=True))
        return 0
    except ImportFailure as error:
        print(str(error), file=sys.stderr)
    except (OSError, ValueError, TypeError, tarfile.TarError, EOFError, RecursionError, OverflowError, zlib.error):
        print("IMPORT_FAILED", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
