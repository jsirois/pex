# Copyright 2023 Pex project contributors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

import os.path
import shutil
import warnings

import pytest

from pex.common import touch
from pex.dependency_manager import DependencyManager
from pex.dist_metadata import DistMetadata, Distribution, Requirement
from pex.fingerprinted_distribution import FingerprintedDistribution
from pex.orderedset import OrderedSet
from pex.pep_427 import InstalledWheel
from pex.pep_440 import Version
from pex.pep_503 import ProjectName
from pex.pex_builder import PEXBuilder
from pex.pex_warnings import PEXWarning
from pex.typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Tuple

    import attr  # vendor:skip
else:
    from pex.third_party import attr


@attr.s(frozen=True)
class DistFactory(object):
    install_base_dir = attr.ib()  # type: str

    def create(
        self,
        name,  # type: str
        *requires  # type: str
    ):
        # type: (...) -> FingerprintedDistribution

        work_dir = os.path.join(self.install_base_dir, ".work_dir", name)
        version = Version("0.1.0")
        record_relpath = os.path.join(
            "{name}-{version}.dist-info".format(name=name, version=version), "RECORD"
        )
        touch(os.path.join(work_dir, record_relpath))
        installed_wheel = InstalledWheel.save(work_dir, record_relpath=record_relpath)
        fingerprint = installed_wheel.fingerprint
        assert fingerprint is not None

        location = os.path.join(self.install_base_dir, fingerprint, name)
        shutil.move(work_dir, location)
        return FingerprintedDistribution(
            distribution=Distribution(
                location=location,
                metadata=DistMetadata(
                    project_name=ProjectName(name),
                    version=version,
                    requires_dists=tuple(Requirement.parse(req) for req in requires),
                ),
            ),
            fingerprint=fingerprint,
        )


@pytest.fixture
def dist_factory(tmpdir):
    # type: (Any) -> DistFactory
    return DistFactory(os.path.join(str(tmpdir), "installed_wheels"))


@attr.s(frozen=True)
class DistGraph(object):
    root_reqs = attr.ib()  # type: Tuple[Requirement, ...]
    dists = attr.ib()  # type: Tuple[FingerprintedDistribution, ...]

    def dist(self, name):
        # type: (str) -> FingerprintedDistribution
        project_name = ProjectName(name)
        dists = [
            dist for dist in self.dists if project_name == dist.distribution.metadata.project_name
        ]
        assert len(dists) == 1, "Expected {name} to match one dist, found {found}".format(
            name=name, found=" ".join(map(str, dists)) if dists else "none"
        )
        return dists[0]


@pytest.fixture
def dist_graph(dist_factory):
    # type: (DistFactory) -> DistGraph

    # distA     distB <--------\
    #      \   /     \         |
    #       v v       v        |
    #      distC     distD  (cycle)
    #     /     \   /          |
    #    V       v v           |
    # distE     distF ---------/

    return DistGraph(
        root_reqs=(Requirement.parse("a"), Requirement.parse("b")),
        dists=(
            dist_factory.create("A", "c"),
            dist_factory.create("B", "c", "d"),
            dist_factory.create("C", "e", "f"),
            dist_factory.create("D", "f"),
            dist_factory.create("E"),
            dist_factory.create("F", "b"),
        ),
    )


@pytest.fixture
def pex_builder(tmpdir):
    # type: (Any) -> PEXBuilder
    return PEXBuilder(path=os.path.join(str(tmpdir), "chroot"))


def test_exclude_root_reqs(
    dist_graph,  # type: DistGraph
    pex_builder,  # type: PEXBuilder
):
    # type: (...) -> None

    dependency_manager = DependencyManager(
        requirements=OrderedSet(dist_graph.root_reqs), distributions=OrderedSet(dist_graph.dists)
    )

    with warnings.catch_warnings(record=True) as events:
        dependency_manager.configure(pex_builder, excluded=["a", "b"])
    assert 2 == len(events)

    warning = events[0]
    assert PEXWarning == warning.category
    assert (
        "The distribution A 0.1.0 was required by the input requirement a but excluded by "
        "configured excludes: a"
    ) == str(warning.message)

    warning = events[1]
    assert PEXWarning == warning.category
    assert (
        "The distribution B 0.1.0 was required by the input requirement b but excluded by "
        "configured excludes: b"
    ) == str(warning.message)

    pex_builder.freeze()

    assert ["a", "b"] == list(pex_builder.info.requirements)
    assert ["a", "b"] == list(pex_builder.info.excluded)
    assert {} == pex_builder.info.distributions


def test_exclude_complex(
    dist_graph,  # type: DistGraph
    pex_builder,  # type: PEXBuilder
):
    # type: (...) -> None

    dependency_manager = DependencyManager(
        requirements=OrderedSet(dist_graph.root_reqs), distributions=OrderedSet(dist_graph.dists)
    )

    dependency_manager.configure(pex_builder, excluded=["c"])
    pex_builder.freeze()

    assert ["a", "b"] == list(pex_builder.info.requirements)
    assert ["c"] == list(pex_builder.info.excluded)
    expected_dists = [dist_graph.dist(name) for name in ("A", "B", "D", "F")]
    assert {
        os.path.basename(dist.location): dist.fingerprint for dist in expected_dists
    } == pex_builder.info.distributions
