# Copyright 2022 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import

import errno
import msvcrt

from pex.common import safe_sleep
from pex.fs.lock import FileLock


class WindowsFileLock(FileLock):
    @classmethod
    def acquire(cls, fd):
        # type: (int) -> WindowsFileLock

        # Force the non-blocking lock to be blocking. LK_LOCK is msvcrt's implementation of a
        # blocking lock, but it only tries 10 times, once per second before raising an OSError.
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                return cls(locked_fd=fd, unlock=lambda: msvcrt.locking(fd, msvcrt.LK_UNLCK, 1))
            except (IOError, OSError) as e:
                # Deadlock error is raised after failing to lock the file.
                if e.errno != errno.EDEADLOCK:
                    raise
                safe_sleep(1)
