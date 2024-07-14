# Copyright 2022 Pex project contributors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import

import base64
import csv
import hashlib
import os
from fileinput import FileInput

from pex import hashing
from pex.common import safe_open, touch
from pex.interpreter import PythonInterpreter
from pex.typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from typing import Any, Callable, Iterable, Iterator, Optional, Protocol, Text, Tuple, Union

    import attr  # vendor:skip

    from pex.hashing import Hasher

    class CSVWriter(Protocol):
        def writerow(self, row):
            # type: (Iterable[Union[str, int]]) -> None
            pass

else:
    from pex.third_party import attr


@attr.s(frozen=True)
class Hash(object):
    @classmethod
    def create(cls, hasher):
        # type: (Hasher) -> Hash

        # The fingerprint encoding is defined for PEP-376 RECORD files as `urlsafe-base64-nopad`
        # which is fully spelled out in code in PEP-427:
        # + https://peps.python.org/pep-0376/#record
        # + https://peps.python.org/pep-0427/#appendix
        fingerprint = base64.urlsafe_b64encode(hasher.digest()).rstrip(b"=")
        return cls(value="{alg}={hash}".format(alg=hasher.name, hash=fingerprint.decode("ascii")))

    value = attr.ib()  # type: str

    def __str__(self):
        # type: () -> str
        return self.value


def find_and_replace_path_components(
    path,  # type: Text
    find,  # type: str
    replace,  # type: str
):
    # type: (...) -> Text
    """Replace components of `path` that are exactly `find` with `replace`.

    >>> find_and_replace_path_components("foo/bar/baz", "bar", "spam")
    foo/spam/baz
    >>>
    """
    if not find or not replace:
        raise ValueError(
            "Both find and replace must be non-empty strings. Given find={find!r} "
            "replace={replace!r}".format(find=find, replace=replace)
        )
    if not path:
        return path

    components = []
    head = path
    while head:
        new_head, tail = os.path.split(head)
        if new_head == head:
            components.append(head)
            break
        components.append(tail)
        head = new_head
    components.reverse()
    return os.path.join(*(replace if component == find else component for component in components))


@attr.s(frozen=True)
class InstalledFile(object):
    """The record of a single installed file from a PEP 376 RECORD file.

    See: https://www.python.org/dev/peps/pep-0376/#record
    """

    _PYTHON_VER_PLACEHOLDER = "pythonX.Y"

    @staticmethod
    def _python_ver(interpreter=None):
        # type: (Optional[PythonInterpreter]) -> str
        python = interpreter or PythonInterpreter.get()
        return "python{major}.{minor}".format(major=python.version[0], minor=python.version[1])

    @classmethod
    def denormalized_path(
        cls,
        path,  # type: str
        interpreter=None,  # type: Optional[PythonInterpreter]
    ):
        # type: (...) -> Text

        # N.B.: Old versions of the installed wheel chroot layout will have normalized paths in
        # their stash; so this function is retained in order to be able to read old PEX_ROOTs when
        # using new `pex-tools`.
        return find_and_replace_path_components(
            path, cls._PYTHON_VER_PLACEHOLDER, cls._python_ver(interpreter=interpreter)
        )

    @classmethod
    def create(
        cls,
        path,  # type: Text
        base,  # type: Text
    ):
        # type: (...) -> InstalledFile
        hasher = hashlib.sha256()
        hashing.file_hash(path, digest=hasher)
        return cls(
            path=os.path.relpath(path, base), hash=Hash.create(hasher), size=os.stat(path).st_size
        )

    path = attr.ib()  # type: Text
    hash = attr.ib(default=None)  # type: Optional[Hash]
    size = attr.ib(default=None)  # type: Optional[int]


@attr.s(frozen=True)
class Record(object):
    """Represents the PEP-376 RECORD of an installed wheel.

    See: https://www.python.org/dev/peps/pep-0376/#record
    """

    @classmethod
    def read(
        cls,
        lines,  # type: Union[FileInput[str], Iterator[str]]
        exclude=None,  # type: Optional[Callable[[str], bool]]
    ):
        # type: (...) -> Iterator[InstalledFile]

        # TODO(John Sirois): This appears to be unused currently but is needed to slurp up installed
        #  wheels from venvs; see: https://github.com/pantsbuild/pex/issues/1361.
        #  It should probably take base and record_relpath and return a Record.

        # The RECORD is a csv file with the path to each installed file in the 1st column.
        # See: https://www.python.org/dev/peps/pep-0376/#record
        for line, (path, fingerprint, file_size) in enumerate(
            csv.reader(lines, delimiter=",", quotechar='"'), start=1
        ):
            resolved_path = path
            if exclude and exclude(resolved_path):
                continue
            file_hash = Hash(fingerprint) if fingerprint else None
            size = int(file_size) if file_size else None
            yield InstalledFile(path=path, hash=file_hash, size=size)

    base = attr.ib()  # type: str
    relpath = attr.ib()  # type: str
    installed_files = attr.ib()  # type: Tuple[InstalledFile, ...]

    @relpath.validator
    def _relpath_validator(
        self,
        _attribute,  # type: Any
        value,  # type: str
    ):
        # type: (...) -> None
        if not os.path.dirname(value).endswith(".dist-info"):
            raise ValueError(
                "Expected RECORD relative path to include its containing .dist-info directory. "
                "Given {value}".format(value=value)
            )

    def write(self, requested=True):
        # type: (bool) -> None

        installed_files = list(self.installed_files)
        if requested:
            requested_path = os.path.join(self.base, os.path.dirname(self.relpath), "REQUESTED")
            touch(requested_path)
            installed_files.append(InstalledFile.create(path=requested_path, base=self.base))
        installed_files.append(InstalledFile(path=self.relpath, hash=None, size=None))

        # The RECORD is a csv file with the path to each installed file in the 1st column.
        # See: https://www.python.org/dev/peps/pep-0376/#record
        with safe_open(os.path.join(self.base, self.relpath), "w") as fp:
            csv_writer = cast(
                "CSVWriter",
                csv.writer(fp, delimiter=",", quotechar='"', lineterminator="\n"),
            )
            for installed_file in sorted(installed_files, key=lambda installed: installed.path):
                csv_writer.writerow(attr.astuple(installed_file, recurse=False))
