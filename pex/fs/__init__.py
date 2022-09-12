# Copyright 2022 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import

import os

from pex.os import WINDOWS

safe_rename = getattr(os, "replace", os.rename)


if WINDOWS and not hasattr(os, "symlink"):
    _CSL = None

    def safe_symlink(
        src,  # type: str
        dst,  # type: str
    ):
        # type: (...) -> None
        import ctypes

        global _CSL
        if _CSL is None:
            csl = ctypes.windll.kernel32.CreateSymbolicLinkW
            csl.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
            csl.restype = ctypes.c_bool
            _CSL = csl

        # See: https://docs.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-createsymboliclinkw
        if not _CSL(dst, src, 1 if os.path.isdir(src) else 0):
            raise ctypes.WinError()

else:
    safe_symlink = os.symlink


if WINDOWS and not hasattr(os, "link"):
    _CHL = None

    def safe_link(
        src,  # type: str
        dst,  # type: str
    ):
        # type: (...) -> None
        import ctypes

        global _CHL
        if _CHL is None:
            # See: https://docs.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-createhardlinkw
            chl = ctypes.windll.kernel32.CreateHardLinkW
            chl.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p)
            chl.restype = ctypes.c_bool
            _CHL = chl

        if not _CHL(dst, src, None):
            raise ctypes.WinError()

else:
    safe_link = os.link
