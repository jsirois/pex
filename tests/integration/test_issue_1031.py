# Copyright 2021 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

import os

import pytest

from pex.interpreter import PythonInterpreter
from pex.orderedset import OrderedSet
from pex.testing import (
    PY310,
    ensure_python_venv,
    make_env,
    run_pex_command,
    skip_unless_python27_venv,
)
from pex.typing import TYPE_CHECKING
from pex.venv.virtualenv import Virtualenv

if TYPE_CHECKING:
    from typing import Callable, MutableSet


@pytest.mark.parametrize(
    "create_venv",
    [
        pytest.param(
            lambda system_site_packages: skip_unless_python27_venv(
                system_site_packages=system_site_packages
            ),
            id="virtualenv-16.7.10",
        ),
        pytest.param(
            lambda system_site_packages: ensure_python_venv(
                PY310, system_site_packages=system_site_packages
            ),
            id="pyvenv",
        ),
    ],
)
def test_setuptools_isolation_with_system_site_packages(
    create_venv,  # type: Callable[[bool], Virtualenv]
):
    # type: (...) -> None
    system_site_packages_venv = create_venv(True)
    standard_venv = create_venv(False)

    print_sys_path_code = "import os, sys; print('\\n'.join(map(os.path.realpath, sys.path)))"

    def get_sys_path(venv):
        # type: (Virtualenv) -> MutableSet[str]
        return OrderedSet(os.path.realpath(entry) for entry in venv.interpreter.sys_path)

    system_site_packages_venv_sys_path = get_sys_path(system_site_packages_venv)
    standard_venv_sys_path = get_sys_path(standard_venv)

    def venv_dir(venv):
        # type: (Virtualenv) -> str
        return os.path.realpath(venv.venv_dir)

    system_site_packages = {
        p
        for p in (system_site_packages_venv_sys_path - standard_venv_sys_path)
        if (
            "site-packages" == os.path.basename(p)
            and not p.startswith((venv_dir(system_site_packages_venv), venv_dir(standard_venv)))
        )
    }
    assert len(system_site_packages) == 1, (
        "system_site_packages_venv_sys_path:\n"
        "\t{}\n"
        "standard_venv_sys_path:\n"
        "\t{}\n"
        "difference:\n"
        "\t{}".format(
            "\n\t".join(system_site_packages_venv_sys_path),
            "\n\t".join(standard_venv_sys_path),
            "\n\t".join(system_site_packages),
        )
    )
    system_site_packages_path = system_site_packages.pop()

    def get_system_site_packages_pex_sys_path(
        *args,  # type: str
        **env  # type: str
    ):
        # type: (...) -> MutableSet[str]
        result = run_pex_command(
            args=args + ("--", "-c", print_sys_path_code),
            python=system_site_packages_venv.interpreter.binary,
            env=make_env(**env),
        )
        result.assert_success()
        return OrderedSet(result.output.strip().splitlines())

    assert system_site_packages_path not in get_system_site_packages_pex_sys_path()

    assert system_site_packages_path not in get_system_site_packages_pex_sys_path(
        "--inherit-path=false"
    )
    assert system_site_packages_path not in get_system_site_packages_pex_sys_path(
        PEX_INHERIT_PATH="false"
    )

    assert system_site_packages_path in get_system_site_packages_pex_sys_path(
        "--inherit-path=prefer"
    )
    assert system_site_packages_path in get_system_site_packages_pex_sys_path(
        PEX_INHERIT_PATH="prefer"
    )

    assert system_site_packages_path in get_system_site_packages_pex_sys_path(
        "--inherit-path=fallback"
    )
    assert system_site_packages_path in get_system_site_packages_pex_sys_path(
        PEX_INHERIT_PATH="fallback"
    )
