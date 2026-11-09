"""Producer releases and qualification receipts have independent, closed scopes."""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


@dataclass(frozen=True)
class Qualification:
    receipt: str
    wire_schema: str
    producer_sha256: str
    engine: str
    engine_version: str
    image_digest: str
    backend: str
    tp_size: int
    origin: str
    stage_ranks: tuple[tuple[str, tuple[int, ...]], ...] = ()

    def matches(self, producer, schema, scope, stage_ranks=None):
        return (type(scope) is dict and set(scope) == {'engine', 'engine_version', 'image_digest', 'backend', 'tp_size', 'origin'}
            and type(scope['tp_size']) is int and scope['tp_size'] > 0
            and producer['source_sha256'] == self.producer_sha256 and schema == self.wire_schema
            and all(scope[key] == getattr(self, key) for key in scope)
            and type(stage_ranks) is dict and dict(self.stage_ranks) == {key: tuple(ranks) for key, ranks in stage_ranks.items()})


def release_digest(schema, paths):
    """Explicit feature dependencies, including the wire schema, form a release."""
    digest = sha256(schema.encode() + b'\0')
    for path in sorted(map(Path, paths), key=lambda p: p.name):
        if path.is_symlink():
            raise ValueError('PRODUCER_RELEASE_SYMLINK')
        digest.update(path.name.encode() + b'\0' + path.read_bytes() + b'\0')
    return digest.hexdigest()
