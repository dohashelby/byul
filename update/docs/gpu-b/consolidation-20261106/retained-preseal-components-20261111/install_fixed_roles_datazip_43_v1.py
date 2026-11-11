"""Install only the newly pinned 43-file SUP16 candidate into absent roots.

No retained path repair, source/environment installation, model or AWS action.
"""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import zipfile

BASE = Path('/home/ubuntu/byul-gpu-b-tp2-20261103')
PLAN_SHA = 'b6a801be7de288662c7fb660501b9a444260cf177a76620e1d54a8bbe000eb23'
DIRS_SHA = '696fb75b17285cb3e7fd25ccee7d098f4add3620b8c9cb2a44301eedcb2857e0'
PREFIX = 'SUP16-ROLES-20261111-R8-'


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def ordinary(path):
    require(path.is_absolute() and not any(p.is_symlink() for p in (path, *path.parents)),
            'INSTALL_NO_SYMLINK_OR_RELATIVE_PATH')


def identity(meta):
    return (meta.st_dev, meta.st_ino, meta.st_mode, meta.st_uid, meta.st_gid,
            meta.st_nlink, meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns)


def read_input(path, digest):
    ordinary(path)
    initial = path.lstat()
    require(stat.S_ISREG(initial.st_mode) and initial.st_size <= 2 * 1024**2,
            'INSTALL_PREOPEN_REGULAR_BOUNDED_REQUIRED')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(identity(initial) == identity(before), 'INSTALL_PREOPEN_FD_CHANGED')
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 1000
                and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o600
                and before.st_size <= 2 * 1024**2, 'INSTALL_UID1000_INPUT600_REQUIRED')
        raw = stream.read(2 * 1024**2 + 1)
        require(identity(before) == identity(os.fstat(stream.fileno())) == identity(path.lstat())
                and len(raw) == before.st_size and sha(raw) == digest, 'INSTALL_INPUT_CHANGED')
    return raw



PAYLOAD_JSON_MEMBER = 'fixed43-payload.json'
PAYLOAD_JSON_BYTES = 917149
PAYLOAD_JSON_SHA256 = '8d484ce4604912099fab32219499bc62ca07be34e27a8da0608def30c0a04eb8'


def read_payload_zip(path, digest):
    # read_input keeps the original REG/lstat/FD/O_NONBLOCK/UID1000/0600 pin.
    raw = read_input(path, digest)
    require(raw.startswith(b'PK\x03\x04') and raw[-22:-18] == b'PK\x05\x06',
            'INSTALL_ZIP_CONTAINER_REQUIRED')
    try:
        with zipfile.ZipFile(io.BytesIO(raw), 'r') as archive:
            entries = archive.infolist()
            require(len(entries) == 1, 'INSTALL_ZIP_SINGLE_MEMBER_REQUIRED')
            entry = entries[0]
            require(entry.filename == entry.orig_filename == PAYLOAD_JSON_MEMBER and not entry.is_dir(),
                    'INSTALL_ZIP_FIXED_JSON_MEMBER_REQUIRED')
            require(not archive.comment and not entry.comment and not entry.extra and entry.flag_bits == 0
                    and entry.header_offset == 0 and entry.create_system == 3
                    and stat.S_ISREG(entry.external_attr >> 16) and not (entry.external_attr >> 16) & 0o111,
                    'INSTALL_ZIP_DATA_METADATA_REQUIRED')
            require(entry.compress_type == zipfile.ZIP_STORED
                    and entry.file_size == entry.compress_size == PAYLOAD_JSON_BYTES,
                    'INSTALL_ZIP_EXACT_STORED_BYTES_REQUIRED')
            data_end = 30 + len(PAYLOAD_JSON_MEMBER.encode('ascii')) + PAYLOAD_JSON_BYTES
            require(archive.start_dir == data_end
                    and len(raw) == data_end + 46 + len(PAYLOAD_JSON_MEMBER.encode('ascii')) + 22,
                    'INSTALL_ZIP_CANONICAL_LAYOUT_REQUIRED')
            with archive.open(entry, 'r') as stream:
                payload = stream.read(PAYLOAD_JSON_BYTES + 1)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError) as error:
        raise ValueError('INSTALL_ZIP_INVALID') from error
    require(len(payload) == PAYLOAD_JSON_BYTES and sha(payload) == PAYLOAD_JSON_SHA256,
            'INSTALL_ZIP_JSON_BYTES_CHANGED')
    return payload


"""Create one new role-private client root below the pinned private assets root.

The installer calls this for its newly created assets root. Existing paths are
never repaired, changed or removed. This function does not run a client/model.
"""
import os
from pathlib import Path
import stat

ROLES = frozenset(('normal-before', 'challenge-partial', 'normal-recovered'))


def _identity(value):
    return (value.st_dev, value.st_ino, value.st_uid, value.st_gid,
            value.st_mode, value.st_nlink, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def create_client_private_root(assets_root, role):
    """Return a newly created UID1000/0700 root; reject any existing child."""
    root = Path(assets_root)
    if role not in ROLES or not root.is_absolute() or '..' in root.parts:
        raise ValueError('CLIENT_PRIVATE_FIXED_ABSOLUTE_ROOT_AND_ROLE_REQUIRED')
    if os.getuid() != os.geteuid() or os.geteuid() not in (0, 1000):
        raise PermissionError('CLIENT_PRIVATE_INSTALLER_IDENTITY_REQUIRED')
    parent = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    child = None
    name = 'client-private-' + role
    try:
        for part in root.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=parent)
            os.close(parent)
            parent = next_fd
        before = os.fstat(parent)
        if (before.st_uid != 1000 or stat.S_IMODE(before.st_mode) != 0o700
                or _identity(before) != _identity(root.lstat())):
            raise PermissionError('CLIENT_PRIVATE_PINNED_ASSETS700_REQUIRED')
        # mkdir is exclusive: a retained directory, file, or symlink is refused.
        os.mkdir(name, 0o700, dir_fd=parent)
        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=parent)
        if os.geteuid() == 0:
            # Only this newly created child is assigned to the selected owner.
            os.fchown(child, 1000, 1000)
        value = os.fstat(child)
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        after = os.fstat(parent)
        if (not stat.S_ISDIR(value.st_mode) or value.st_uid != 1000
                or stat.S_IMODE(value.st_mode) != 0o700
                or _identity(value) != _identity(named)
                or (before.st_dev, before.st_ino, before.st_uid, before.st_gid,
                    before.st_mode) != (after.st_dev, after.st_ino, after.st_uid,
                                        after.st_gid, after.st_mode)
                or _identity(after) != _identity(root.lstat())):
            raise PermissionError('CLIENT_PRIVATE_NEW_ROOT_IDENTITY_REQUIRED')
        return {'path': str(root / name), 'role': role, 'UID': value.st_uid,
                'GID': value.st_gid, 'mode': oct(stat.S_IMODE(value.st_mode)),
                'dev': value.st_dev, 'inode': value.st_ino,
                'existing_paths_changed': False, 'client_executed': False}
    finally:
        if child is not None:
            os.close(child)
        os.close(parent)


def create_directory(path, owner):
    ordinary(path)
    os.mkdir(path, 0o700)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fchown(fd, owner, owner)
        value = os.fstat(fd)
        require(value.st_uid == owner and stat.S_IMODE(value.st_mode) == 0o700
                and identity(value) == identity(path.lstat()), 'INSTALL_NEW_DIRECTORY_METADATA')
    finally:
        os.close(fd)


def create_file(path, raw, owner):
    ordinary(path)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        os.fchown(stream.fileno(), owner, owner)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
        value = os.fstat(stream.fileno())
        require(value.st_uid == owner and value.st_nlink == 1
                and stat.S_IMODE(value.st_mode) == 0o600
                and value.st_size == len(raw)
                and identity(value) == identity(path.lstat()), 'INSTALL_NEW_FILE_METADATA')


def decode(payload):
    require(set(payload) == {'plan_base64', 'directories_base64', 'files'}, 'INSTALL_PAYLOAD_KEYS')
    plan_raw = base64.b64decode(payload['plan_base64'], validate=True)
    dirs_raw = base64.b64decode(payload['directories_base64'], validate=True)
    require(sha(plan_raw) == PLAN_SHA and sha(dirs_raw) == DIRS_SHA, 'INSTALL_REVIEWED_PLAN_CHANGED')
    plan, dirs = json.loads(plan_raw), json.loads(dirs_raw)
    require(len(plan['files']) == 43, 'INSTALL_EXACT43_REQUIRED')
    names = [row['local_file'] for row in plan['files']]
    require(len(set(names)) == len(names) and set(payload['files']) == set(names), 'INSTALL_EXACT_FILE_SET')
    prepared = []
    for row in plan['files']:
        path = Path(row['guest'])
        require(path.is_relative_to(BASE) and path.relative_to(BASE).parts[0].startswith(PREFIX)
                and row['mode'] == '0600' and row['owner'] in (0, 1000), 'INSTALL_FIXED_DESTINATION')
        raw = base64.b64decode(payload['files'][row['local_file']], validate=True)
        require(len(raw) == row['bytes'] and sha(raw) == row['sha256'], 'INSTALL_FIXED_FILE_BYTES')
        prepared.append((path, raw, row['owner'], row['sha256']))
    return prepared, dirs


def prevalidate(prepared, dirs):
    declared = {Path(row['path']): row['owner'] for row in dirs['required_existing_input_parents']}
    require(all(path.is_relative_to(BASE) and path.relative_to(BASE).parts[0].startswith(PREFIX)
                for path in declared), 'INSTALL_FIXED_DIRECTORIES')
    # Resolve all collisions before the first output mutation. Existing paths
    # never become repair candidates, including outputs from an incomplete run.
    targets = [*declared, *(path for path, _, _, _ in prepared),
               *(Path(x) for x in dirs['must_stay_absent_before_role_open']),
               Path(dirs['health_archive_root']['path'])]
    for path in targets:
        ordinary(path)
        require(not path.exists() and not path.is_symlink(), 'INSTALL_PRESERVE_EXISTING_PATH:' + str(path))
    for path, _, _, _ in prepared:
        require(path.parent in declared, 'INSTALL_UNDECLARED_PARENT:' + str(path.parent))
    return declared


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', required=True)
    parser.add_argument('--payload-sha256', required=True)
    args = parser.parse_args()
    require(os.getuid() == os.geteuid() == 0, 'INSTALL_ROOT_REQUIRED')
    require(Path('/sys/devices/virtual/dmi/id/board_asset_tag').read_text().strip()
            == 'i-05e8da8ea99593196', 'INSTALL_SELECTED_INSTANCE_REQUIRED')
    os.umask(0o077)
    prepared, dirs = decode(json.loads(read_payload_zip(Path(args.payload), args.payload_sha256)))
    declared = prevalidate(prepared, dirs)
    private_roots=[]
    assets_root=BASE/(PREFIX+"ASSETS")
    for role in ROLES:
        require(declared.get(assets_root/("client-private-"+role))==1000,"INSTALL_EXACT_PRIVATE_ROLE_ROOTS")
    for path in sorted(declared, key=lambda item: (len(item.parts), str(item))):
        if path.parent==assets_root and path.name in {"client-private-"+role for role in ROLES}:
            private_roots.append(create_client_private_root(assets_root,path.name[len("client-private-"):]))
        else:
            create_directory(path, declared[path])
    for path, raw, owner, _ in prepared:
        create_file(path, raw, owner)
    receipt = {'plan_sha256': PLAN_SHA, 'directories_sha256': DIRS_SHA,
               'client_private_roots':private_roots,
               'files': [{'path': str(path), 'sha256': digest, 'bytes': len(raw), 'owner': owner}
                         for path, raw, owner, digest in prepared],
               'existing_paths_changed': False, 'health_root_precreated': False,
               'source_guard_venv_modified': False, 'model_or_GPU_executed': False}
    root = BASE / (PREFIX + 'OPERATOR')
    create_file(root / 'fixed-install-receipt.json',
                (json.dumps(receipt, sort_keys=True, indent=2) + '\n').encode(), 0)
    print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    main()
