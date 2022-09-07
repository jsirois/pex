# Copyright 2022 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import

import os
from textwrap import dedent

from pex.common import safe_mkdtemp
from pex.os import WINDOWS
from pex.testing import make_env, pex_check_output
from pex.typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Optional, Text


def get_venv_prompt(
    venv_dir,  # type: str
    tmpdir=None,  # type: Optional[str]
):
    # type: (...) -> Text
    if WINDOWS:
        script = os.path.join(tmpdir or safe_mkdtemp(), "script.bat")
        with open(script, "w") as fp:
            fp.write(
                dedent(
                    """\
                    @echo off
                    call "{activate}"
                    echo %PROMPT%
                    """
                ).format(
                    activate=os.path.realpath(os.path.join(venv_dir, "Scripts", "activate.bat"))
                )
            )
        output = pex_check_output(args=[script])
    else:
        output = pex_check_output(
            args=[
                "/usr/bin/env",
                "bash",
                "-c",
                "source {} && echo $PS1".format(os.path.join(venv_dir, "bin", "activate")),
            ],
            env=make_env(TERM="dumb", COLS=80),
        )
    return output.decode("utf-8")
