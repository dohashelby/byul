"""Produce a local, non-deployable layout candidate from the immutable R3 plan.

Keep the original 41 rows and payloads. Append the exact pinned source-owner
bytes at the sibling path already required by both unchanged consumers.
No installer, operating admission, remote deployment or execution is supplied.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat

BASE_PLAN_SHA = 'f6c46149369726eaf55a0ca9df8ab2d0d801f20fb4cd01b5dde91e5eca88bb1b'
OWNER_SHA = 'fb12dbe8d82d6bc027b613a477d6e1a432fa4693c46c6f1380cb38cecdf79fc9'
CAPTURE_SHA = 'bb9a2839b2e2166af3f4d2309bbe5afe260a2eee2f49d05434913d2084eb4cb7'
RESOLVER_SHA = 'abae956aa5193c2d962d9b2b3d9b276723f5a0dbea5681514564b51056ee8d52'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def pinned_read(path, digest, size=None):
    before = path.lstat()
    assert stat.S_ISREG(before.st_mode) and not path.is_symlink(), 'LOCAL_INPUT_REGULAR_REQUIRED'
    raw = path.read_bytes()
    after = path.lstat()
    assert (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) == (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'LOCAL_INPUT_CHANGED'
    assert sha(raw) == digest and (size is None or len(raw) == size), 'LOCAL_INPUT_PIN_CHANGED'
    return raw


def build(base_root, output):
    """Return actual producer artifacts; caller must supply an absent local output."""
    original_raw = pinned_read(base_root / 'file-install-plan.json', BASE_PLAN_SHA)
    original = json.loads(original_raw)
    rows = original['files']
    assert len(rows) == 41 and len({row['guest'] for row in rows}) == 41
    captures = [row for row in rows if PurePosixPath(row['guest']).name == 'capture_normal.py']
    owners = [row for row in rows if row['sha256'] == OWNER_SHA]
    resolvers = [row for row in rows if PurePosixPath(row['guest']).name == 'resolver.py']
    assert len(captures) == len(owners) == len(resolvers) == 1
    capture, owner, resolver = captures[0], owners[0], resolvers[0]
    assert capture['sha256'] == CAPTURE_SHA and resolver['sha256'] == RESOLVER_SHA
    parent = PurePosixPath(capture['guest']).parent
    assert PurePosixPath(resolver['guest']).parent == parent
    assert PurePosixPath(owner['guest']) == parent / 'static/assets/source_owner.py'
    assert owner['bytes'] == 3540 and owner['owner'] == capture['owner'] == 1000
    assert owner['mode'] == capture['mode'] == '0600'
    target = str(parent / 'source_owner_verify.py')
    assert target not in {row['guest'] for row in rows}

    # Load/pin all original bytes before creating any output; never repair input.
    payloads = {}
    for row in rows:
        local = PurePosixPath(row['local_file'])
        assert not local.is_absolute() and local.parts[0] == 'files' and len(local.parts) == 2
        assert local.name not in ('', '.', '..') and row['mode'] == '0600'
        assert row['local_file'] not in payloads, 'LOCAL_INPUT_NAME_DUPLICATE'
        payloads[row['local_file']] = pinned_read(base_root.joinpath(*local.parts), row['sha256'], row['bytes'])
    added = dict(owner, guest=target, local_file='files/041-source_owner_verify.py')
    assert added['local_file'] not in payloads
    payloads[added['local_file']] = payloads[owner['local_file']]
    plan = dict(original, files=[*rows, added], state='LOCAL_LAYOUT_CANDIDATE_NOT_DEPLOYABLE_NOT_GPU_READY')
    plan.update(current_proofs_issued=False, GPU_AWS_executed=False)
    output.mkdir(exist_ok=False)
    (output / 'files').mkdir()
    for name, raw in payloads.items():
        with output.joinpath(*PurePosixPath(name).parts).open('xb') as stream:
            stream.write(raw)
    plan_raw = canonical(plan)
    with (output / 'file-install-plan.json').open('xb') as stream:
        stream.write(plan_raw)
    receipt = {
        'schema': 'SUP16-source-owner-sibling-local-layout/1',
        'base_plan_sha256': BASE_PLAN_SHA, 'file_count': 42,
        'original_rows_unchanged': plan['files'][:41] == rows,
        'original_payload_count_unchanged': 41, 'added_row': added,
        'plan_sha256': sha(plan_raw), 'plan_bytes': len(plan_raw),
        'resolver_sha256_unchanged': RESOLVER_SHA, 'capture_sha256_unchanged': CAPTURE_SHA,
        'source_owner_sha256_unchanged': OWNER_SHA,
        'guest_paths': 'R3 logical contract paths only; occupied guest R3 must never receive this candidate',
        'installer': 'Original exact41 installer rejects this plan; future fixed installer/payload pins are not produced here',
        'new_operating_generation_or_admission': False, 'AWS_or_model_executed': False,
        'current_R3_files_modified': False, 'runtime_ready': False,
    }
    with (output / 'build-receipt.json').open('xb') as stream:
        stream.write(canonical(receipt))
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.base_root, args.output), sort_keys=True))
