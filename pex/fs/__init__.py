# Copyright 2022 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import

import os
import sys

from pex.os import WINDOWS
from pex.typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Text


if WINDOWS and not hasattr(os, "replace"):
    _MOVEFILE_REPLACE_EXISTING = 0x1

    _MF = None

    def safe_rename(
        src,  # type: Text
        dst,  # type: Text
    ):
        # type: (...) -> None

        import ctypes

        global _MF
        if _MF is None:
            mf = ctypes.windll.kernel32.MoveFileExW
            mf.argtypes = (
                # lpExistingFileName
                ctypes.c_wchar_p,
                # lpNewFileName
                ctypes.c_wchar_p,
                # dwFlags
                ctypes.c_uint32,
            )
            mf.restype = ctypes.c_bool
            _MF = mf

        # See: https://docs.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw
        if not _MF(src, dst, _MOVEFILE_REPLACE_EXISTING):
            raise ctypes.WinError()

else:
    safe_rename = getattr(os, "replace", os.rename)


# N.B.: Python 3.7 has os.symlink on Windows, but the implementation does not pass the
# _SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE flag.
if WINDOWS and (not hasattr(os, "symlink") or sys.version_info[:2] < (3, 8)):
    _SYMBOLIC_LINK_FLAG_FILE = 0x0
    _SYMBOLIC_LINK_FLAG_DIRECTORY = 0x1
    _SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE = 0x2

    _CSL = None

    def safe_symlink(
        src,  # type: Text
        dst,  # type: Text
    ):
        # type: (...) -> None

        import ctypes

        global _CSL
        if _CSL is None:
            csl = ctypes.windll.kernel32.CreateSymbolicLinkW
            csl.argtypes = (
                # lpSymlinkFileName
                ctypes.c_wchar_p,
                # lpTargetFileName
                ctypes.c_wchar_p,
                # dwFlags
                ctypes.c_uint32,
            )
            csl.restype = ctypes.c_bool
            _CSL = csl

        # See: https://docs.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-createsymboliclinkw
        flags = _SYMBOLIC_LINK_FLAG_DIRECTORY if os.path.isdir(src) else _SYMBOLIC_LINK_FLAG_FILE
        flags |= _SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE
        if not _CSL(dst, src, flags):
            raise ctypes.WinError()

else:
    safe_realpath = os.path.realpath
    safe_symlink = getattr(os, "symlink")


if WINDOWS and not hasattr(os, "link"):
    _CHL = None

    def safe_link(
        src,  # type: Text
        dst,  # type: Text
    ):
        # type: (...) -> None

        import ctypes

        global _CHL
        if _CHL is None:
            # See: https://docs.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-createhardlinkw
            chl = ctypes.windll.kernel32.CreateHardLinkW
            chl.argtypes = (
                # lpFileName
                ctypes.c_wchar_p,
                # lpExistingFileName
                ctypes.c_wchar_p,
                # lpSecurityAttributes (Reserved; must be NULL)
                ctypes.c_void_p,
            )
            chl.restype = ctypes.c_bool
            _CHL = chl

        if not _CHL(os.path.join(r"\\?", dst), os.path.join(r"\\?", src), None):
            raise ctypes.WinError()

else:
    safe_link = getattr(os, "link")
