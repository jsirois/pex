# Copyright 2025 Pex project contributors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import

from pex.resolve.lockfile.pep_751 import elide_extras
from pex.third_party.packaging.markers import Marker


def test_elide_extras():
    assert elide_extras(Marker("extra == 'bob'")) is None
    assert elide_extras(Marker("extra == 'bob' or extra == 'bill'")) is None

    def assert_elide_extras(
        expected,
        original,
    ):
        # type: (...) -> None

        # N.B.: The string conversion is needed to cover Python 2.7, 3.5 and 3.6 which use vendored
        # packaging 20.9 and 21.3. In those versions of packaging, `__eq__` is not defined for
        # `Marker`. In later versions it is (and is based off of `str`).
        assert str(Marker(expected)) == str(elide_extras(Marker(original)))

    assert_elide_extras(
        "python_version == '3.14.*'",
        "(extra == 'bob' or extra == 'bill') and python_version == '3.14.*'",
    )
    assert_elide_extras(
        "python_version == '3.14.*'",
        "python_version == '3.14.*' and (extra == 'bob' or extra == 'bill')",
    )
    assert_elide_extras(
        "python_version == '3.14.*'",
        "(extra == 'bob' and python_version == '3.14.*') or extra == 'bill'",
    )
    assert_elide_extras(
        (
            "("
            "python_version == '3.14.*' and sys_platform == 'win32'"
            ") or python_version == '3.11.*'"
        ),
        (
            "("
            "python_version == '3.14.*' and sys_platform == 'win32' and ("
            "extra == 'bob' or extra == 'bill'"
            ")"
            ") or python_version == '3.11.*'"
        ),
    )
