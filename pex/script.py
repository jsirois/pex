# Copyright 2022 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import, print_function

import os
import re
import sys
import zipfile
from typing import Optional

from pex.os import WINDOWS, is_exe
from pex.ziputils import Zip

_SHEBANG_MAGIC = b"#!"


def is_script(
    path,  # type: str
    pattern=None,  # type: Optional[bytes]
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
            return bool(re.match(pattern, shebang[len(_SHEBANG_MAGIC) :].strip()))

    with open(path, "rb") as fp:
        if _SHEBANG_MAGIC != fp.read(len(_SHEBANG_MAGIC)):
            return False
        if not pattern:
            return True
        return bool(re.match(pattern, fp.readline()))


def is_python_script(
    path,  # type: str
    check_executable=True,  # type: bool
):
    # type: (...) -> bool
    return is_script(
        path,
        pattern=(
            br"""(?x)
            ^
            (?:
                # Support the `#!python` shebang that wheel installers should recognize as a special
                # form to convert to a localized shebang upon install.
                # See: https://www.python.org/dev/peps/pep-0427/#recommended-installer-features
                python |
                (?:
                    # The aim is to admit the common shebang forms:
                    # + /usr/bin/env <python bin name> (<options>)?
                    # + /absolute/path/to/<python bin name> (<options>)?
                    .+
                    \W
                    (?i:
                        # Python executable names Pex supports (see PythonIdentity).
                        python |
                        pypy
                    )
                )
            )
            """
        ),
        check_executable=check_executable,
    )
