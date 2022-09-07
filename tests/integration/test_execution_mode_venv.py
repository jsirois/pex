# Copyright 2020 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import

import json
import os
import re

from pex.os import WINDOWS
from pex.testing import IS_PYPY, PY_VER, run_pex_command
from pex.typing import TYPE_CHECKING
from pex.venv.testing import get_venv_prompt

if TYPE_CHECKING:
    from typing import Any, Callable


def test_custom_prompt(tmpdir):
    # type: (Any) -> None

    pex_root = os.path.join(str(tmpdir), "pex_root")

    venv_pex_name = "venv.pex"
    venv_pex = os.path.join(str(tmpdir), venv_pex_name)

    result = run_pex_command(
        args=[
            "--pex-root",
            pex_root,
            "--runtime-pex-root",
            pex_root,
            "-o",
            venv_pex,
            "--seed",
            "verbose",
            "--venv",
        ]
    )
    result.assert_success()
    venv_dir = os.path.dirname(json.loads(result.output)["pex"])

    if WINDOWS:
        expected_prompt = r"^\({venv_pex_name}\) \$P\$G$".format(
            venv_pex_name=re.escape(venv_pex_name)
        )
    elif PY_VER == (2, 7) or (IS_PYPY and PY_VER <= (3, 7)):
        # Neither CPython 2.7 not PyPy interpreters have (functioning) venv modules; so we create
        # their venvs with an old copy of virtualenv that does not surround the prompt with parens.
        expected_prompt = r"^{venv_pex_name}$".format(venv_pex_name=re.escape(venv_pex_name))
    elif PY_VER == (3, 5):
        # We can't set the prompt for CPython 3.5; so we expect a venv atomic_directory-style name.
        expected_prompt = r"^\({venv_dir}\.[0-9a-f]+\)$".format(
            venv_dir=re.escape(os.path.basename(venv_dir))
        )
    else:
        expected_prompt = r"^\({venv_pex_name}\)$".format(venv_pex_name=re.escape(venv_pex_name))

    assert (
        re.match(expected_prompt, get_venv_prompt(venv_dir, tmpdir=str(tmpdir)).strip()) is not None
    )
