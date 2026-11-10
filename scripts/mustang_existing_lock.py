"""Use an existing canonical lock without creating or opening it for writing."""
import contextlib
import fcntl
import os
from pathlib import Path

import mustang_hosttools as readers


class LockUnavailable(ValueError):
    pass


def open_stream(path,*,expected):
    if os.fspath(path)!=os.fspath(expected) or readers._body(os.fspath(expected)) is None:
        raise LockUnavailable('dedicated canonical lock path differs')
    try:fd,info=readers._open_regular(str(Path(expected).parent),os.fspath(expected))
    except (OSError,readers._Refused):
        raise LockUnavailable('dedicated canonical lock is unavailable') from None
    try:
        if info.st_nlink!=1 or info.st_size!=0:
            raise LockUnavailable('dedicated canonical lock is not a single empty regular file')
        handle=os.fdopen(fd,'rb');fd=None
        return handle
    finally:
        if fd is not None:os.close(fd)


@contextlib.contextmanager
def shared(path,*,expected):
    with open_stream(path,expected=expected) as handle:
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise LockUnavailable('dedicated canonical lock is held') from None
        yield handle


def probe(path,*,expected):
    try:
        with shared(path,expected=expected):pass
        return True
    except (OSError,LockUnavailable):return False
