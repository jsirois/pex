# Copyright 2014 Pex project contributors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import

import ast
import os

from pex.common import open_zip, safe_mkdtemp
from pex.dist_metadata import Distribution, DistributionType, EntryPoint
from pex.pep_427 import InstalledWheel, Wheel
from pex.pep_503 import ProjectName
from pex.scripts import is_python_script
from pex.typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from typing import Dict, Iterable, Optional

    import attr  # vendor:skip
else:
    from pex.third_party import attr


@attr.s(frozen=True)
class DistributionScript(object):
    @classmethod
    def find(
        cls,
        dist,  # type: Distribution
        name,  # type: str
    ):
        # type: (...) -> Optional[DistributionScript]
        if dist.type is DistributionType.WHEEL:
            script_path = Wheel.load(dist.location).data_path("scripts", name)
            with open_zip(dist.location) as zfp:
                try:
                    zfp.getinfo(script_path)
                except KeyError:
                    return None
            return cls(dist=dist, path=script_path)
        elif dist.type is DistributionType.INSTALLED:
            maybe_script_path = InstalledWheel.load(dist.location).script_path(name)
            if not maybe_script_path:
                return None
            return cls(dist=dist, path=maybe_script_path)

        raise ValueError(
            "Can only probe .whl files and installed wheel chroots for scripts; "
            "given sdist: {sdist}".format(sdist=dist.location)
        )

    dist = attr.ib()  # type: Distribution
    path = attr.ib()  # type: str

    def read_contents(self, path_hint=None):
        # type: (Optional[str]) -> bytes
        path = path_hint or self._maybe_extract()
        with open(path, "rb") as fp:
            return fp.read()

    def python_script(self):
        # type: () -> Optional[ast.AST]
        path = self._maybe_extract()
        if not is_python_script(path):
            return None

        try:
            return cast(
                ast.AST,
                compile(self.read_contents(path_hint=path), path, "exec", flags=0, dont_inherit=1),
            )
        except (SyntaxError, TypeError):
            return None

    def _maybe_extract(self):
        # type: () -> str
        if self.dist.type is not DistributionType.WHEEL:
            return self.path

        with open_zip(self.dist.location) as zfp:
            chroot = safe_mkdtemp()
            zfp.extract(self.path, chroot)
            return os.path.join(chroot, self.path)


def get_script_from_distributions(
    name,  # type: str
    dists,  # type: Iterable[Distribution]
):
    # type: (...) -> Optional[DistributionScript]
    for dist in dists:
        distribution_script = DistributionScript.find(dist, name)
        if distribution_script:
            return distribution_script
    return None


@attr.s(frozen=True)
class DistributionEntryPoint(object):
    dist = attr.ib()  # type: Distribution
    entry_point = attr.ib()  # type: EntryPoint


def get_entry_point_from_console_script(
    script,  # type: str
    dists,  # type: Iterable[Distribution]
):
    # type: (...) -> Optional[DistributionEntryPoint]
    # Check all distributions for the console_script "script". De-dup by dist key to allow for a
    # duplicate console script IFF the distribution is platform-specific and this is a
    # multi-platform pex.
    def get_entrypoint(dist):
        # type: (Distribution) -> Optional[EntryPoint]
        return dist.get_entry_map().get("console_scripts", {}).get(script)

    entries = {}  # type: Dict[ProjectName, DistributionEntryPoint]
    for dist in dists:
        entry_point = get_entrypoint(dist)
        if entry_point is not None:
            entries[dist.metadata.project_name] = DistributionEntryPoint(dist, entry_point)

    if len(entries) > 1:
        raise RuntimeError(
            "Ambiguous script specification {script} matches multiple entry points:\n\t"
            "{entry_points}".format(
                script=script,
                entry_points="\n\t".join(
                    "{entry_point} from {dist}".format(
                        entry_point=dist_entry_point.entry_point, dist=dist_entry_point.dist
                    )
                    for dist_entry_point in entries.values()
                ),
            )
        )

    dist_entry_point = None
    if entries:
        dist_entry_point = next(iter(entries.values()))
    return dist_entry_point
