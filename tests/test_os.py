# Copyright 2022 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

import os
import pkgutil

from pex.common import chmod_plus_x, touch
from pex.os import WINDOWS, is_exe
from pex.typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any


def create_exe(path):
    # type: (str) -> None
    if WINDOWS:
        win_exe = pkgutil.get_data(__name__, "windows\\win.exe")
        assert win_exe is not None
        with open(path, "wb") as fp:
            fp.write(win_exe)
    else:
        touch(path)


def test_is_exe(tmpdir):
    # type: (Any) -> None

    all_exe = os.path.join(str(tmpdir), "all.exe")
    create_exe(all_exe)
    chmod_plus_x(all_exe)
    assert is_exe(all_exe)

    other_exe = os.path.join(str(tmpdir), "other.exe")
    create_exe(other_exe)
    os.chmod(other_exe, 0o665)
    assert (
        not is_exe(other_exe) or WINDOWS
    ), "Changing permission bits does not affect executability on Windows."

    not_exe = os.path.join(str(tmpdir), "not.exe")
    touch(not_exe)
    assert not is_exe(not_exe)

    exe_dir = os.path.join(str(tmpdir), "dir.exe")
    os.mkdir(exe_dir)
    chmod_plus_x(exe_dir)
    assert not is_exe(exe_dir)
