# Copyright 2025 Pex project contributors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import

import os.path
from sysconfig import get_config_var

from pex.os import WINDOWS

EXE_EXTENSION = get_config_var("EXE") or ""
EXE_EXTENSIONS = (
    tuple(ext.lower() for ext in os.environ.get("PATHEXT", EXE_EXTENSION).split(os.pathsep))
    if EXE_EXTENSION
    else ()
)


def script_name(name):
    # type: (str) -> str
    if not EXE_EXTENSION:
        return name
    stem, ext = os.path.splitext(name)
    return name if (ext and ext.lower() in EXE_EXTENSIONS) else name + EXE_EXTENSION


# TODO(John Sirois): XXX: Use sysconfig.get_path("scripts", expand=False) +
#  sysconfig.get_config_vars()
SCRIPT_DIR = "Scripts" if WINDOWS else "bin"
