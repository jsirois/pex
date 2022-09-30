# Copyright 2022 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import, print_function

import os
import re
import zipfile
from typing import Optional

from pex.os import WINDOWS, is_exe
from pex.ziputils import Zip

_SHEBANG_MAGIC = b"#!"


def is_script(
    path,  # type: str
    pattern=None,  # type: Optional[str]
    check_executable=True,  # type: bool
):
    # type: (...) -> bool
    """Determines if the given path is a script.

    A script is a file that starts with a shebang (#!...) line.

    :param path: The path to check.
    :param pattern: An optional pattern to match against the shebang (excluding the leading #!).
    :param check_executable: Check that the script is executable by the current user.
    :return: True if the given path is a script.
    """
    if check_executable and not is_exe(path):
        return False

    if WINDOWS and zipfile.is_zipfile(path):
        zip_script = Zip.load(path)
        if not zip_script.has_header:
            return False

        with open(os.devnull, "wb") as fp:
            shebang = zip_script.isolate_header(fp, stop_at=_SHEBANG_MAGIC)
            if not shebang:
                return False
            if not pattern:
                return True
            return bool(re.match(pattern, shebang[len(_SHEBANG_MAGIC) :].decode("utf-8").strip()))

    with open(path, "rb") as fp:
        if _SHEBANG_MAGIC != fp.read(len(_SHEBANG_MAGIC)):
            return False
        if not pattern:
            return True
        return bool(re.match(pattern, fp.readline().decode("utf-8")))


def is_python_script(
    path,  # type: str
    check_executable=True,  # type: bool
):
    # type: (...) -> bool
    return is_script(path, pattern=r"(?i)^.*(?:python|pypy)", check_executable=check_executable)
