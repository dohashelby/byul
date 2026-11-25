"""Local storage operations shared by the manual evidence export tools."""

from pathlib import Path

from guard.models import canonical_json, sha256_hex
from guard.store import Store


def canonical_digest(value) -> str:
    return sha256_hex(canonical_json(value))


def file_inventory(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): sha256_hex(path.read_bytes())
        for path in sorted(Path(root).rglob("*"))
        if path.is_file()
    }


def exact_refs(store: Store) -> list[tuple[str, str, int, str]]:
    return sorted(
        (obj.kind.value, obj.id, obj.revision, obj.content_sha256)
        for kind in ("ENV", "POL", "CHG", "PLAN", "EVD", "RES", "RPT")
        for obj in store.iter_kind(kind)
    )


def write_evidence_json(path: Path, value) -> None:
    with Path(path).open("xb") as stream:
        stream.write(canonical_json(value))
