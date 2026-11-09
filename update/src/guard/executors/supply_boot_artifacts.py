"""supply_boot 전체 프로파일의 증거 저장. 수집·판정·실행기는 추가하지 않는다."""
from pathlib import Path
import json

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
