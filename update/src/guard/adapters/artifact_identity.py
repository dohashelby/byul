"""Artifact identity validation is provider owned, not a core HF assumption."""
import re


def validate(kind, identity, revision, digest):
    if any(type(v) is not str or not v or len(v) > 2048 or any(ord(c) < 32 or ord(c) == 127 for c in v) for v in (kind, identity, revision)):
        raise ValueError('ARTIFACT_IDENTITY_INVALID')
    if type(digest) is not str or re.fullmatch('[0-9a-f]{64}', digest) is None:
        raise ValueError('ARTIFACT_DIGEST_REQUIRED')
    if kind == 'hf-snapshot' and (re.fullmatch('[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', identity) is None or re.fullmatch('[0-9a-f]{40}', revision) is None):
        raise ValueError('HF_SNAPSHOT_IDENTITY_INVALID')
