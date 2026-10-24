"""Share verified immutable test preparation, never the operations under test."""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from guard.models import canonical_json, sha256_hex
from guard.store import Store
from guard.store.packs import put_pack
from tests.builders import PACK_DIR, ROOT


def dump_fixture_yaml(data, **kwargs):
    # Keep default escaped/wrapped YAML bytes. The form writers explicitly use
    # Unicode; their equivalent C safe emitter avoids repeated Python emission.
    dumper = getattr(yaml, "CSafeDumper", yaml.SafeDumper) if kwargs.get("allow_unicode") is True else yaml.SafeDumper
    return yaml.dump(data, Dumper=dumper, **kwargs)


def _files(source):
    paths = sorted(Path(source).rglob("*"))
    assert not any(path.is_symlink() for path in paths)
    return tuple((path.relative_to(source), path.read_bytes()) for path in paths if path.is_file())


def _write(destination, files):
    destination = Path(destination)
    for relative in sorted({p.parent for p, _ in files}, key=lambda p: (len(p.parts), p.as_posix())):
        (destination / relative).mkdir(parents=True, exist_ok=True)
    for relative, data in files:
        # Existing damaged files are never repaired by a preparation cache.
        with (destination / relative).open("xb") as stream:
            stream.write(data)


@lru_cache(maxsize=1)
def _baseline_snapshot():
    with TemporaryDirectory(prefix="guard-test-pack-") as directory:
        store = Store(Path(directory) / "store")
        ref = put_pack(store, PACK_DIR)
        assert store.verify() == []
        artifacts = tuple((p.name, p.read_bytes()) for p in sorted((store.root / "artifacts").iterdir()))
        assert all(sha256_hex(data) == name for name, data in artifacts)
    return ref, artifacts


def put_baseline_snapshot(store):
    """Only for the unmodified repository pack; changed-pack tests use put_pack."""
    ref, artifacts = _baseline_snapshot()
    for name, data in artifacts:
        try:
            with (store.root / "artifacts" / name).open("xb") as stream:
                stream.write(data)
        except FileExistsError:
            # Preserve the real writer's integrity checks, including corruption.
            assert store.put_artifact(data).sha256 == name
    return deepcopy(ref)


@lru_cache(maxsize=1)
def _workdir_files():
    return ((Path("guard.toml"), (ROOT / "guard.toml").read_bytes()),
            *((Path(name) / relative, data) for name in ("examples", "packs")
              for relative, data in _files(ROOT / name)))


def copy_workdir(destination):
    """Copy pristine repository fixtures, with private files for each case."""
    _write(destination, _workdir_files())


@lru_cache(maxsize=None)
def _frozen_tree(source):
    return _files(source)


def copy_frozen_tree(source, destination):
    """The caller promises source is immutable; mutate only the fresh copy."""
    destination = Path(destination)
    destination.mkdir()
    _write(destination, _frozen_tree(Path(source)))


_MANUAL_SEEDS = {}


def manual_setup_seed(family, parameters, folder, create, *, store_directory="store"):
    """Clone verified preconditions keyed by all pack/policy/environment inputs.

    create returns (Store, Plan, metadata). Callers still run each tested import,
    executor, judge, export, restore or report on the private clone. Only trusted
    in-process preparation bytes are cached; no on-disk evidence is repaired.
    """
    key = (family, store_directory, canonical_json(parameters))
    if key not in _MANUAL_SEEDS:
        with TemporaryDirectory(prefix="guard-test-prepared-") as directory:
            store, plan, metadata = create(Path(directory))
            assert store.verify() == []
            files = _files(store.root)
            assert not any(p.name.endswith(("-wal", "-shm", "-journal")) for p, _ in files)
            _MANUAL_SEEDS[key] = (files, plan.ref(), deepcopy(metadata))
    files, ref, metadata = _MANUAL_SEEDS[key]
    root = Path(folder) / store_directory
    _write(root, files)
    store = Store(root)
    return store, store.resolve(ref), deepcopy(metadata)
