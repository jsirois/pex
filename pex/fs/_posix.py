# Copyright 2022 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import

import fcntl

from pex.fs.lock import FileLock, FileLockStyle
from pex.typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from typing import Callable


def _is_bsd_lock(lock_style=None):
    # type: (Optional[FileLockStyle.Value]) -> bool

    # The atomic_directory file locking has used POSIX locks since inception. These have maximum
    # compatibility across OSes and stand a decent chance of working over modern NFS. With the
    # introduction of `pex3 lock ...` a limited set of atomic_directory uses started asking for BSD
    # locks since they operate in a thread pool. Only those uses actually pass an explicit value for
    # `lock_style` to atomic_directory. In order to allow experimenting with / debugging possible
    # file locking bugs, we allow a `_PEX_FILE_LOCK_STYLE` back door private ~API to upgrade all
    # locks to BSD style locks. This back door can be removed at any time.
    file_lock_style = lock_style or FileLockStyle.for_value(
        os.environ.get("_PEX_FILE_LOCK_STYLE", FileLockStyle.POSIX.value)
    )
    return file_lock_style is FileLockStyle.BSD


class PosixFileLock(FileLock):
    @classmethod
    def acquire(
        cls,
        fd,  # type: int
        style,  # type: FileLockStyle.Value
    ):
        # type: (...) -> PosixFileLock
        lock_api = cast(
            "Callable[[int, int], None]", fcntl.flock if _is_bsd_lock(style) else fcntl.lockf
        )
        lock_api(fd, fcntl.LOCK_EX)
        return cls(locked_fd=fd, unlock=lambda: lock_api(fd, fcntl.LOCK_UN))
