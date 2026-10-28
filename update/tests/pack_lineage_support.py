"""Read newly preserved historical packs from exact Git bytes, never live packs."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

from guard.models import load_pack

ROOT = Path(__file__).resolve().parent / "fixtures/baseline-lineage"

def historical_pack(label, target):
    folder = ROOT / label
    record = json.loads((folder / "SNAPSHOT.json").read_text(encoding="utf-8"))
    raw = (folder / "source.zip").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == record["source_zip_sha256"]
    target = Path(target)
    target.mkdir()
    with zipfile.ZipFile(folder / "source.zip") as archive:
        assert set(archive.namelist()) == set(record["files"])
        for name in archive.namelist():
            relative = PurePosixPath(name)
            assert not relative.is_absolute() and ".." not in relative.parts
            data = archive.read(name)
            assert hashlib.sha256(data).hexdigest() == record["files"][name]
            path = target.joinpath(*relative.parts).resolve()
            assert path.is_relative_to(target.resolve())
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    pack = load_pack(target)
    assert pack.manifest.revision == record["pack_revision"]
    return pack
