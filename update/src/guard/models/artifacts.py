"""Versioned model-closure identity, preserving the historical snapshot hash."""
import re

from .hashing import canonical_json, sha256_hex


def model_closure_sha256(model):
    if model.inventory_sha256 is None: return None
    if model.tokenizer_revision is None and model.components is None:
        return model.inventory_sha256
    return sha256_hex(canonical_json({'schema': 'guard-model-closure/1', 'id': model.id,
        'revision': model.revision, 'inventory_sha256': model.inventory_sha256,
        'tokenizer_revision': model.tokenizer_revision,
        'components': None if model.components is None else [c.model_dump(mode='json') for c in model.components]}))

_PINNED_IMAGE = re.compile(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}")


def is_pinned_image(value) -> bool:
    return type(value) is str and len(value) <= 512 and _PINNED_IMAGE.fullmatch(value) is not None
