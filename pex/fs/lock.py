# Copyright 2022 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import

import os

from pex.enum import Enum
from pex.os import WINDOWS
from pex.typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable

    import attr  # vendor: skip
else:
    from pex.third_party import attr


class FileLockStyle(Enum["FileLockStyle.Value"]):
    class Value(Enum.Value):
        pass

    BSD = Value("bsd")
    POSIX = Value("posix")


@attr.s(frozen=True)
class FileLock(object):
    _locked_fd = attr.ib()  # type: int
    _unlock = attr.ib()  # type: Callable[[], Any]

    def release(self):
        # type: () -> None
        try:
            self._unlock()
        finally:
            os.close(self._locked_fd)


def acquire(
    path,  # type: str
    style=FileLockStyle.POSIX,  # type: FileLockStyle.Value
):
    # type: (...) -> FileLock

    # N.B.: We don't actually write anything to the lock file but the fcntl file locking
    # operations only work on files opened for at least write.
    lock_fd = os.open(path, os.O_CREAT | os.O_WRONLY)

    if WINDOWS:
        from pex.fs._windows import WindowsFileLock

        return WindowsFileLock.acquire(lock_fd)
    else:
        from pex.fs._posix import PosixFileLock

        return PosixFileLock.acquire(lock_fd, style)
