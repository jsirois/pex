# Copyright 2021 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

import os

from pex.testing import pex_check_call, run_pex_command
from pex.typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any


def test_resolve_cyclic_dependency_graph(tmpdir):
    # type: (Any) -> None
    naked_pex = os.path.join(str(tmpdir), "naked.pex")
    run_pex_command(args=["Naked==0.1.31", "-o", naked_pex]).assert_success()
    pex_check_call(args=[naked_pex, "-c", "import Naked"])
