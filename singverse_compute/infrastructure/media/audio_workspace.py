"""Process leases for audio workspaces shared by nodes using one cache volume."""
import os
from pathlib import Path
import stat
import uuid


def acquire(root: Path, identifier: str):
    """Return an owned handle, or None while another process owns this task."""
    if str(uuid.UUID(identifier)) != identifier:
        raise ValueError('Invalid audio workspace owner')
    path = root / ('.lease-' + identifier)
    if path.is_symlink():
        raise OSError('Audio workspace lease must be a regular file')
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    handle = os.fdopen(fd, 'r+b')
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError('Audio workspace lease must be a regular file')
        if os.name == 'nt':
            import msvcrt
            if os.fstat(fd).st_size == 0:
                handle.write(b'\0'); handle.flush()
            handle.seek(0)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except (BlockingIOError, PermissionError):
        handle.close()
        return None
    except BaseException:
        handle.close()
        raise
