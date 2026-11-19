"""supply_boot 전체 프로파일의 증거 저장. 수집·판정·실행기는 추가하지 않는다."""
from pathlib import Path
import json
import os
from tempfile import TemporaryDirectory

from guard.adapters.vllm_file_read_sensor import MAX_BYTES as FILE_READ_MAX_BYTES, MAX_ROWS as FILE_READ_MAX_ROWS
from guard.models import sha256_hex
from .base import DraftArtifact
from .owned_capture import MAX_BYTES


def persist(workdir, summary, collected, collector):
    artifacts, errors = [], []
    workdir = Path(workdir)
    try:
        workdir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return [], ['SUPPLY_BOOT_EVIDENCE_DIRECTORY_UNAVAILABLE']
    for role in ('normal', 'challenge'):
        raw = collected[role].get('record_bytes')
        if raw is None:
            continue
        name = 'supply_boot_' + role + '_record.json'
        try:
            if type(raw) is not bytes or len(raw) > MAX_BYTES:
                raise ValueError('SUPPLY_BOOT_RECORD_ARTIFACT_BOUND')
            path = workdir / name
            with path.open('xb') as stream:
                stream.write(raw)
            artifacts.append(DraftArtifact(name=name, path=path, collector=collector))
        except (OSError, ValueError):
            errors.append('SUPPLY_BOOT_RECORD_ARTIFACT_UNAVAILABLE:' + role)
    name = 'supply_boot_summary.json'
    try:
        path = workdir / name
        with path.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump({**summary, 'artifact_errors': list(errors)}, stream, ensure_ascii=False, sort_keys=True)
        artifacts.append(DraftArtifact(name=name, path=path, collector=collector))
    except OSError:
        errors.append('SUPPLY_BOOT_SUMMARY_ARTIFACT_UNAVAILABLE')
    return artifacts, errors


def persist_normal_file_read_sources(workdir, captured, selected, collector):
    """Retain only exact, admitted normal-session recording bytes."""
    from .lifecycle_capture import canonical_row

    artifacts, errors, names = [], [], {}
    workdir = Path(workdir)
    for number in sorted(set(selected)):
        recording = captured["recordings"][number]
        capture = recording["capture"]
        provenance, summary, rows = capture["provenance"], capture["summary"], capture["records"]
        if (capture.get("process_context_verified") is not True or recording.get("session_owner_match") is not True
                or recording.get("listed_file_identity_match") is not True or provenance.get("owner_binding_verified") is not True
                or summary.get("recording_closed") is not True or summary.get("discarded_tail_bytes") != 0
                or not 0 < len(rows) <= FILE_READ_MAX_ROWS or rows[-1]["seq"] + 1 != len(rows)):
            errors.append("SUPPLY_BOOT_NORMAL_SOURCE_BYTES_UNVERIFIED")
            continue
        raw = b"".join(canonical_row(row) for row in rows)
        if (not 0 < len(raw) <= FILE_READ_MAX_BYTES or provenance.get("bytes") != len(raw)
                or provenance.get("capture_sha256") != sha256_hex(raw)):
            errors.append("SUPPLY_BOOT_NORMAL_SOURCE_BYTES_UNVERIFIED")
            continue
        name = f"supply_boot_normal_source_{number:03d}.jsonl"
        path = workdir / name
        try:
            workdir.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(dir=workdir, prefix=".sup05-source-") as staging:
                temporary = Path(staging) / name
                with temporary.open("xb") as stream:
                    if stream.write(raw) != len(raw):
                        raise OSError("SUPPLY_BOOT_NORMAL_SOURCE_ARTIFACT_SHORT_WRITE")
                    stream.flush()
                    os.fsync(stream.fileno())
                # Publish the completed file atomically without replacing another file.
                os.link(temporary, path)
        except OSError:
            errors.append("SUPPLY_BOOT_NORMAL_SOURCE_ARTIFACT_UNAVAILABLE")
            continue
        artifacts.append(DraftArtifact(name=name, path=path, collector=collector))
        names[number] = name
    return artifacts, sorted(set(errors)), names
