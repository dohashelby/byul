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
