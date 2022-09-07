# Copyright 2022 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import

import errno
import os
from contextlib import contextmanager
from uuid import uuid4

from pex.common import safe_mkdir, safe_rmtree
from pex.fs import lock, safe_rename
from pex.fs.lock import FileLock, FileLockStyle
from pex.os import WINDOWS
from pex.typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Iterator, Optional, Union


class AtomicDirectory(object):
    def __init__(self, target_dir):
        # type: (str) -> None
        self._target_dir = target_dir
        self._work_dir = "{}.{}".format(target_dir, uuid4().hex)

    @property
    def work_dir(self):
        # type: () -> str
        return self._work_dir

    @property
    def target_dir(self):
        # type: () -> str
        return self._target_dir

    def is_finalized(self):
        # type: () -> bool
        return os.path.exists(self._target_dir)

    def finalize(self, source=None):
        # type: (Optional[str]) -> None
        """Rename `work_dir` to `target_dir` using `os.rename()`.

        :param source: An optional source offset into the `work_dir`` to use for the atomic update
                       of `target_dir`. By default the whole `work_dir` is used.

        If a race is lost and `target_dir` already exists, the `target_dir` dir is left unchanged and
        the `work_dir` directory will simply be removed.
        """
        if self.is_finalized():
            return

        source = os.path.join(self._work_dir, source) if source else self._work_dir
        try:
            # Perform an atomic rename.
            #
            # Per the docs: https://docs.python.org/2.7/library/os.html#os.rename
            #
            #   The operation may fail on some Unix flavors if src and dst are on different
            #   filesystems. If successful, the renaming will be an atomic operation (this is a
            #   POSIX requirement).
            #
            # We have satisfied the single filesystem constraint by arranging the `work_dir` to be a
            # sibling of the `target_dir`.
            safe_rename(source, self._target_dir)
        except OSError as e:
            if e.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                raise e
        finally:
            self.cleanup()

    def cleanup(self):
        # type: () -> None
        safe_rmtree(self._work_dir)


@contextmanager
def atomic_directory(
    target_dir,  # type: str
    exclusive,  # type: Union[bool, FileLockStyle.Value]
    source=None,  # type: Optional[str]
):
    # type: (...) -> Iterator[AtomicDirectory]
    """A context manager that yields a potentially exclusively locked AtomicDirectory.

    :param target_dir: The target directory to atomically update.
    :param exclusive: If `True`, its guaranteed that only one process will be yielded a non `None`
                      workdir; otherwise two or more processes might be yielded unique non-`None`
                      workdirs with the last process to finish "winning". By default, a POSIX fcntl
                      lock will be used to ensure exclusivity. To change this, pass an explicit
                      `LockStyle` instead of `True`.
    :param source: An optional source offset into the work directory to use for the atomic update
                   of the target directory. By default the whole work directory is used.

    If the `target_dir` already exists the enclosed block will be yielded an AtomicDirectory that
    `is_finalized` to signal there is no work to do.

    If the enclosed block fails the `target_dir` will be undisturbed.

    The new work directory will be cleaned up regardless of whether or not the enclosed block
    succeeds.

    If the contents of the resulting directory will be subsequently mutated it's probably correct to
    pass `exclusive=True` to ensure mutations that race the creation process are not lost.
    """

    # TODO(John Sirois): XXX: Racing os.rename (os.replace) gets permission denied errors on
    #  Windows.
    exclusive = True if WINDOWS else exclusive

    atomic_dir = AtomicDirectory(target_dir=target_dir)
    if atomic_dir.is_finalized():
        # Our work is already done for us so exit early.
        yield atomic_dir
        return

    file_lock = None  # type: Optional[FileLock]
    if exclusive:
        head, tail = os.path.split(atomic_dir.target_dir)
        if head:
            safe_mkdir(head)
        file_lock = lock.acquire(
            path=os.path.join(head, ".{}.atomic_directory.lck".format(tail or "here")),
            style=FileLockStyle.BSD if exclusive is FileLockStyle.BSD else FileLockStyle.POSIX,
        )
        if atomic_dir.is_finalized():
            # We lost the double-checked locking race and our work was done for us by the race
            # winner so exit early.
            try:
                yield atomic_dir
            finally:
                file_lock.release()
            return

    try:
        os.makedirs(atomic_dir.work_dir)
        yield atomic_dir
        atomic_dir.finalize(source=source)
    finally:
        if file_lock:
            file_lock.release()
        atomic_dir.cleanup()
