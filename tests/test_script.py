# Copyright 2022 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

import os
import pkgutil
import shutil
from typing import Any

from pex.common import chmod_plus_x, open_zip, touch
from pex.os import WINDOWS, is_exe
from pex.script import is_script


def create_exe(path):
    # type: (str) -> None
    if WINDOWS:
        win_exe = pkgutil.get_data(__name__, "windows\\win.exe")
        assert win_exe is not None
        with open(path, "wb") as fp:
            fp.write(win_exe)
    else:
        touch(path)
        chmod_plus_x(path)


def test_is_script(tmpdir):
    # type: (Any) -> None
    exe = os.path.join(str(tmpdir), "exe")

    touch(exe)
    assert not is_exe(exe)
    assert not is_script(exe)

    # TODO(John Sirois): XXX
    create_exe(exe)
    assert is_exe(exe)
    assert not is_script(exe)

    with open(exe, "wb") as fp:
        fp.write(bytearray([0xCA, 0xFE, 0xBA, 0xBE]))
    assert not is_script(fp.name)

    bin_file = os.path.join(str(tmpdir), "bin")
    with open(bin_file, "wb") as fp:
        fp.write(bytearray([0xCA, 0xFE, 0xBA, 0xBE]))
    zip_file = os.path.join(str(tmpdir), "zip")
    with open_zip(zip_file, "w") as zf:
        zf.write(bin_file, "enigma")

    create_exe(exe)
    with open(exe, "ab") as fp, open(zip_file, "rb") as zfp:
        fp.write(b"#!/mystery\n")
        shutil.copyfileobj(zfp, fp)
    assert is_script(exe)
    assert is_script(exe, pattern=r"^/mystery")
    assert not is_script(exe, pattern=r"^python")

    os.chmod(exe, 0o665)
    assert is_script(exe, check_executable=False)
    assert (
        not is_script(exe) or WINDOWS
    ), "Changing permission bits does not affect executability on Windows."
    assert (
        not is_exe(exe) or WINDOWS
    ), "Changing permission bits does not affect executability on Windows."
