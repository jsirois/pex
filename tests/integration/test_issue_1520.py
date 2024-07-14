# Copyright 2021 Pex project contributors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

import json
import os
import shutil
import subprocess
from textwrap import dedent

import pytest

from pex.typing import TYPE_CHECKING
from pex.venv.virtualenv import Virtualenv
from testing import IS_LINUX, IS_MAC, PY_VER, run_pex_command

if TYPE_CHECKING:
    from typing import Any


@pytest.mark.skipif(
    PY_VER < (3, 6), reason="The mypy_protobuf 2.4 distribution is only available for Python 3.6+"
)
@pytest.mark.xfail(
    IS_MAC,
    reason=(
        "On modern Linux (starting with the 5.1 kernel shipped on May 19th 2019), the default max "
        "shebang length limit is 256 but the hardcoded limit in Pip that #1520 fixes is 127; so "
        "the work-around here should test green on Linux. On Mac, however, the hardcoded limit in "
        "Pip that #1520 fixes is 512 and that limit has been stable on macOS; so we expect the PEX "
        "creation to fail with something like: [Errno 63] File name too long: '/tmp/"
        "pytest-of-runner/pytest-0/popen-gw2/test_hermetic_console_scripts0/<512 of `_`>/pex_root/"
        "isolated/.488310d43ea7ca80b559c306f2db44914a184e37.atomic_directory.lck'."
    ),
)
def test_hermetic_console_scripts(tmpdir):
    # type: (Any) -> None

    # N.B.: See pex/vendor/_vendored/pip/pip/_vendor/distlib/scripts.py lines 127-156.
    # https://github.com/pex-tool/pex/blob/196b4cd5b8dd4b4af2586460530e9a777262be7d/pex/vendor/_vendored/pip/pip/_vendor/distlib/scripts.py#L127-L156
    length_pad = 127 if IS_LINUX else 512
    pex_root = os.path.join(str(tmpdir), "_" * length_pad, "pex_root")
    assert len(pex_root) > length_pad

    mypy_protobuf_pex = os.path.join(str(tmpdir), "mypy_protobuf.pex")

    # Although mypy_protobuf 2.4 is quite old, it depends on an open ended-protobuf and is broken
    # in conjunction with protobuf 4+
    constraints = os.path.join(str(tmpdir), "constraints.txt")
    with open(constraints, "w") as fp:
        fp.write("protobuf<4")

    result = run_pex_command(
        args=[
            "--pex-root",
            pex_root,
            "--runtime-pex-root",
            pex_root,
            "mypy_protobuf==2.4",
            "--constraints",
            constraints,
            "-o",
            mypy_protobuf_pex,
            "--venv",
            "prepend",
            "--seed",
            "verbose",
        ],
    )
    result.assert_success()
    venv = Virtualenv(venv_dir=os.path.dirname(json.loads(result.output)["pex"]))

    def read_protoc_gen_mypy_script_shebang(search_from):
        # type: (str) -> str
        scripts = [
            os.path.join(root, f)
            for root, dirs, files in os.walk(search_from)
            for f in files
            if "protoc-gen-mypy" == f
        ]
        assert 1 == len(scripts)
        with open(scripts[0]) as fp:
            assert "#!/bin/sh" == fp.readline().strip()
            assert "'''': pshprs" == fp.readline().strip()
            sh_shebang_content, sep, _ = fp.read().partition("'''")
            assert "'''" == sep
            return sh_shebang_content

    # We expect the chroot reified script should have a special sh re-director $PEX ~shebang which
    # would fail if copied into the venv.
    chroot_shebang_content = read_protoc_gen_mypy_script_shebang(
        search_from=os.path.join(pex_root, "installed_wheels")
    )
    assert "$PEX" in chroot_shebang_content
    assert pex_root not in chroot_shebang_content

    # We expect the venv reified script should have a special sh re-director shebang that forwards to
    # the too long path of the pex_root venv interpreter.
    venv_shebang_content = read_protoc_gen_mypy_script_shebang(search_from=venv.bin_dir)
    assert "$PEX" not in venv_shebang_content
    assert pex_root in venv_shebang_content

    shutil.rmtree(pex_root)
    # This should no-op (since there is no proto sent on stdin) and exit success.
    subprocess.check_call(
        args=[
            mypy_protobuf_pex,
            "-c",
            dedent(
                """\
                import subprocess
                import sys


                process = subprocess.Popen(['protoc-gen-mypy'], stdin=subprocess.PIPE)
                process.communicate()
                sys.exit(process.returncode)
                """
            ),
        ],
    )
