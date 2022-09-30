# Copyright 2022 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import

import os
import sys

from pex.typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import List, NoReturn, Text

# N.B.: Python 2.7 uses "linux2".
LINUX = sys.platform.startswith("linux")
MAC = sys.platform == "darwin"
WINDOWS = sys.platform == "win32"


HOME_ENV_VAR = "USERPROFILE" if WINDOWS else "HOME"


if WINDOWS:

    def safe_execv(argv):
        # type: (List[str]) -> NoReturn
        import subprocess
        import sys

        sys.exit(subprocess.call(args=argv))

else:

    def safe_execv(argv):
        # type: (List[str]) -> NoReturn
        os.execv(argv[0], argv)


if WINDOWS:
    _GBT = None

    _EXE_EXTENSIONS = frozenset(e.lower() for e in os.environ.get("PATHEXT", "").split(os.pathsep))

    def is_exe(path):
        # type: (Text) -> bool

        if not os.path.isfile(path):
            return False

        _, ext = os.path.splitext(path)
        if ext.lower() in _EXE_EXTENSIONS:
            return True

        import ctypes
        from ctypes.wintypes import BOOL, DWORD, LPCWSTR, LPDWORD

        global _GBT
        if _GBT is None:
            gbt = ctypes.windll.kernel32.GetBinaryTypeW
            gbt.argtypes = (
                # lpApplicationName
                LPCWSTR,
                # lpBinaryType
                LPDWORD,
            )
            gbt.restype = BOOL
            _GBT = gbt

        # See: https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getbinarytypew
        # N.B.: We don't care about the binary type, just the bool which tells us it is or is not an
        # executable.
        _binary_type = DWORD()
        return bool(_GBT(path, ctypes.byref(_binary_type)))

else:

    def is_exe(path):
        # type: (Text) -> bool
        """Determines if the given path is a file executable by the current user.

        :param path: The path to check.
        :return: `True if the given path is a file executable by the current user.
        """
        return os.path.isfile(path) and os.access(path, os.R_OK | os.X_OK)
