# Copyright 2022 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import

import argparse
import os
import sys

from pex.atomic_directory import atomic_directory
from pex.common import safe_open
from pex.interpreter import PythonIdentity, PythonInterpreter


def identify(
    binary,  # type: str
    cache_dir,  # type: str
):
    # type: (...) -> None
    encoded_identity = PythonIdentity.get(binary=binary).encode()
    with atomic_directory(cache_dir, exclusive=False) as atomic_dir:
        if not atomic_dir.is_finalized():
            with safe_open(
                os.path.join(atomic_dir.work_dir, PythonInterpreter.INTERP_INFO_FILE), "w"
            ) as fp:
                fp.write(encoded_identity)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", default=sys.executable)
    parser.add_argument("cache_dir", nargs=1)
    options = parser.parse_args()
    identify(binary=options.binary, cache_dir=options.cache_dir[0])
