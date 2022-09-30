# Copyright 2022 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import

from sysconfig import get_config_var

from pex.os import WINDOWS

EXE_EXTENSION = get_config_var("EXE") or ""


def script_name(name):
    # type: (str) -> str
    return name if name.endswith(EXE_EXTENSION) else name + EXE_EXTENSION


# TODO(John Sirois): XXX: Use sysconfig.get_path("scripts", expand=False) +
#  sysconfig.get_config_vars()
SCRIPT_DIR = "Scripts" if WINDOWS else "bin"
