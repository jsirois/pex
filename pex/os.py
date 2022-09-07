# Copyright 2022 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import

import os
import sys

from pex.typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import List, NoReturn


# N.B.: Python 2.7 uses "linux2".
LINUX = sys.platform.startswith("linux")
MAC = sys.platform == "darwin"
WINDOWS = sys.platform == "win32"


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
