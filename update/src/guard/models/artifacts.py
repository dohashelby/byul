"""Versioned model-closure identity, preserving the historical snapshot hash."""
from .hashing import canonical_json, sha256_hex


def model_closure_sha256(model):
    if model.inventory_sha256 is None: return None
    if model.tokenizer_revision is None and model.components is None:
        return model.inventory_sha256
    return sha256_hex(canonical_json({'schema': 'guard-model-closure/1', 'id': model.id,
        'revision': model.revision, 'inventory_sha256': model.inventory_sha256,
        'tokenizer_revision': model.tokenizer_revision,
        'components': None if model.components is None else [c.model_dump(mode='json') for c in model.components]}))
