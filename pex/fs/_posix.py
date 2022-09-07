# Copyright 2022 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import

import fcntl

from pex.fs.lock import FileLock, FileLockStyle
from pex.typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from typing import Callable


class PosixFileLock(FileLock):
    @classmethod
    def acquire(
        cls,
        fd,  # type: int
        style,  # type: FileLockStyle.Value
    ):
        # type: (...) -> PosixFileLock
        lock_api = cast(
            "Callable[[int, int], None]", fcntl.flock if style is FileLockStyle.BSD else fcntl.lockf
        )
        lock_api(fd, fcntl.LOCK_EX)
        return cls(locked_fd=fd, unlock=lambda: lock_api(fd, fcntl.LOCK_UN))
