# Copyright 2025 Pex project contributors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import

from pex.resolve.lockfile.pep_751 import elide_extras
from pex.third_party.packaging.markers import Marker


def test_elide_extras():
    assert elide_extras(Marker("extra == 'bob'")) is None
    assert elide_extras(Marker("extra == 'bob' or extra == 'bill'")) is None

    assert Marker("python_version == '3.14.*'") == elide_extras(
        Marker("(extra == 'bob' or extra == 'bill') and python_version == '3.14.*'")
    )
    assert Marker("python_version == '3.14.*'") == elide_extras(
        Marker("python_version == '3.14.*' and (extra == 'bob' or extra == 'bill')")
    )
    assert Marker("python_version == '3.14.*'") == elide_extras(
        Marker("(extra == 'bob' and python_version == '3.14.*') or extra == 'bill'")
    )

    assert Marker(
        "(python_version == '3.14.*' and sys_platform == 'win32') or python_version == '3.11.*'"
    ) == elide_extras(
        Marker(
            "("
            "python_version == '3.14.*' and sys_platform == 'win32' and ("
            "extra == 'bob' or extra == 'bill'"
            ")"
            ") or python_version == '3.11.*'"
        )
    )
