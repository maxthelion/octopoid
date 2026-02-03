"""File locking utilities using fcntl.flock."""

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Generator


def acquire_lock(path: Path | str, blocking: bool = False) -> int | None:
    """Acquire an exclusive lock on a file.

    Args:
        path: Path to the lock file
        blocking: If True, wait for lock. If False, return None if can't acquire.

    Returns:
        File descriptor if lock acquired, None if non-blocking and lock unavailable.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Open or create the lock file
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)

    try:
        flags = fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB

        fcntl.flock(fd, flags)
        return fd
    except (BlockingIOError, OSError):
        # Lock is held by another process
        os.close(fd)
        return None


def release_lock(fd: int) -> None:
    """Release a lock by closing the file descriptor.

    Args:
        fd: File descriptor returned by acquire_lock
    """
    if fd is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            os.close(fd)


@contextmanager
def locked(path: Path | str, blocking: bool = False) -> Generator[bool, None, None]:
    """Context manager for file locking.

    Args:
        path: Path to the lock file
        blocking: If True, wait for lock. If False, yield False if can't acquire.

    Yields:
        True if lock acquired, False otherwise (only when blocking=False)

    Example:
        with locked('/path/to/lock') as acquired:
            if acquired:
                # do work with lock held
            else:
                # lock not available
    """
    fd = acquire_lock(path, blocking=blocking)
    acquired = fd is not None

    try:
        yield acquired
    finally:
        if fd is not None:
            release_lock(fd)


@contextmanager
def locked_or_skip(path: Path | str) -> Generator[bool, None, None]:
    """Context manager that skips if lock unavailable.

    Convenience wrapper around locked() with blocking=False.

    Yields:
        True if lock acquired, False if should skip
    """
    with locked(path, blocking=False) as acquired:
        yield acquired
