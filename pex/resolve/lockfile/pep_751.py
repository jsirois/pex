# Copyright 2025 Pex project contributors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import, print_function

from collections import OrderedDict, defaultdict

from pex.dist_metadata import Requirement
from pex.exceptions import production_assert
from pex.orderedset import OrderedSet
from pex.pep_503 import ProjectName
from pex.requirements import URLRequirement, parse_requirement_string
from pex.resolve.locked_resolve import (
    DownloadableArtifact,
    FileArtifact,
    LocalProjectArtifact,
    LockedRequirement,
    LockedResolve,
    TargetSystem,
    VCSArtifact,
)
from pex.resolve.lockfile.requires_dist import remove_unused_requires_dist
from pex.resolve.resolved_requirement import Pin
from pex.third_party.packaging.markers import Marker
from pex.typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import (
        Any,
        DefaultDict,
        Dict,
        Iterable,
        Iterator,
        List,
        Mapping,
        Optional,
        Tuple,
        Union,
    )


def _to_environment(system):
    # type: (TargetSystem.Value) -> str
    if system is TargetSystem.LINUX:
        return "platform_system = 'Linux'"
    elif system is TargetSystem.MAC:
        return "platform_system = 'Darwin'"
    else:
        production_assert(system is TargetSystem.WINDOWS)
        return "platform_system = 'Windows'"


_LOCK_BOILERPLATE = OrderedDict(
    (
        ("lock-version", "1.0"),
        ("created-by", "Pex"),
    )
)  # type: OrderedDict[str, Any]


def calculate_marker(
    project_name,  # type: ProjectName
    dependants_by_project_name,  # type: Mapping[ProjectName, OrderedSet[Tuple[ProjectName, Optional[Marker]]]]
):
    # type: (...) -> Optional[Marker]

    dependants = dependants_by_project_name.get(project_name)
    if not dependants:
        return None

    or_markers = []  # type: List[Marker]
    for dependant_project_name, marker in dependants:
        and_markers = [marker] if marker else []  # type: List[Marker]
        guard_marker = calculate_marker(dependant_project_name, dependants_by_project_name)
        if guard_marker:
            and_markers.append(guard_marker)

        if not and_markers:
            # This indicates a dependency path that is not conditioned by any markers; i.e.:
            # `project_name` is always required by this dependency path; trumping all others.
            return None

        if len(and_markers) == 1:
            or_markers.append(and_markers[0])
        else:
            or_markers.append(
                Marker("({anded})".format(anded=") and (".join(map(str, and_markers))))
            )

    if not or_markers:
        # No dependency path was conditioned by any marker at all; so `project_name` is always
        # strongly reachable.
        return None

    if len(or_markers) == 1:
        return or_markers[0]

    return Marker("({ored})".format(ored=") or (".join(map(str, or_markers))))


def process_marker_list(marker_list):
    # type: (List[Any]) -> List[Any]

    reduced_markers = []  # type: List[Any]

    for expression in marker_list:
        if isinstance(expression, list):
            reduced = process_marker_list(expression)
            if reduced:
                reduced_markers.append(reduced)
        elif isinstance(expression, tuple):
            lhs, op, rhs = expression
            if lhs.value == "extra" or rhs.value == "extra":
                continue
            reduced_markers.append(expression)
        else:
            assert expression in ("and", "or")
            if reduced_markers:
                # A conjunction is only needed if there is a LHS and a RHS. We can check the LHS
                # now.
                reduced_markers.append(expression)

    # And we can now make sure conjunctions have a RHS.
    if reduced_markers and reduced_markers[-1] in ("and", "or"):
        reduced_markers.pop()

    return reduced_markers


def elide_extras(marker):
    # type: (Marker) -> Optional[Marker]

    markers = process_marker_list(marker._markers)
    if not markers:
        return None

    marker._markers = markers
    return marker


def calculate_markers(locked_requirements):
    # type: (Iterable[LockedRequirement]) -> Iterator[Tuple[LockedRequirement, Optional[Marker]]]

    dependants_by_project_name = defaultdict(
        OrderedSet
    )  # type: DefaultDict[ProjectName, OrderedSet[Tuple[ProjectName, Optional[Marker]]]]
    for locked_requirement in locked_requirements:
        for dist in locked_requirement.requires_dists:
            marker = elide_extras(dist.marker) if dist.marker else None  # type: Optional[Marker]
            dependants_by_project_name[dist.project_name].add(
                (locked_requirement.pin.project_name, marker)
            )

    for locked_requirement in locked_requirements:
        yield locked_requirement, calculate_marker(
            locked_requirement.pin.project_name, dependants_by_project_name
        )


def convert(
    root_requirements,  # type: Iterable[Requirement]
    locked_resolve,  # type: LockedResolve
    requires_python=None,  # type: Optional[str]
    target_systems=(),  # type: Iterable[TargetSystem.Value]
    subset=(),  # type: Iterable[DownloadableArtifact]
):
    # type: (...) -> Dict[str, Any]

    locked_resolve = remove_unused_requires_dist(
        resolve_requirements=root_requirements,
        locked_resolve=locked_resolve,
        requires_python=[requires_python] if requires_python else [],
        target_systems=target_systems,
    )

    pylock = _LOCK_BOILERPLATE.copy()
    if target_systems:
        pylock["environments"] = [_to_environment(system) for system in target_systems]
    if requires_python:
        pylock["requires-python"] = requires_python

    artifact_subset_by_pin = defaultdict(
        list
    )  # type: DefaultDict[Pin, List[Union[FileArtifact, LocalProjectArtifact, VCSArtifact]]]
    for downloadable_artifact in subset:
        artifact_subset_by_pin[downloadable_artifact.pin].append(downloadable_artifact.artifact)

    archive_requirements = {
        req.project_name: req
        for req in root_requirements
        if req.url and isinstance(parse_requirement_string(str(req)), URLRequirement)
    }  # type: Dict[ProjectName, Requirement]

    packages = OrderedDict()  # type: OrderedDict[LockedRequirement, Dict[str, Any]]
    for locked_requirement in locked_resolve.locked_requirements:
        artifact_subset = artifact_subset_by_pin[locked_requirement.pin]
        if subset and not artifact_subset:
            continue

        # TODO: XXX: Use OrderedDicts throughout to ensure toml emit stability.
        # TODO: XXX: Investigate output across 2.7 -> 3.14 (toml, tomli-w, tomllib) and use or else
        #            invent output template system :/.
        package = {
            "name": str(locked_requirement.pin.project_name),
            "version": str(locked_requirement.pin.version),
        }  # type: Dict[str, Any]

        if locked_requirement.requires_python:
            package["requires-python"] = str(locked_requirement.requires_python)

        if locked_requirement.requires_dists:
            dependencies = []  # type: List[Dict[str, Any]]
            for dep in locked_requirement.requires_dists:
                dependencies.append({"name": str(dep.project_name)})
            package["dependencies"] = dependencies

        artifacts = artifact_subset or list(locked_requirement.iter_artifacts())

        archive_requirement = archive_requirements.get(locked_requirement.pin.project_name)
        if archive_requirement:
            artifact_count = len(artifacts)
            production_assert(
                artifact_count == 1,
                "Expected a direct URL requirement to have exactly one artifact but "
                "{requirement} has {count}.".format(
                    requirement=archive_requirement, count=artifact_count
                ),
            )
            artifact = artifacts[0]
            package["archive"] = {
                "url": artifact.url.download_url,
                "hashes": {artifact.fingerprint.algorithm: artifact.fingerprint.hash},
            }
        else:
            wheels = []  # type: List[Dict[str, Any]]
            for artifact in artifacts:
                if isinstance(artifact, FileArtifact):
                    file_artifact = {
                        "name": artifact.filename,
                        "url": artifact.url.download_url,
                        "hashes": {artifact.fingerprint.algorithm: artifact.fingerprint.hash},
                    }
                    if artifact.is_source:
                        package["sdist"] = file_artifact
                    elif artifact.is_wheel:
                        wheels.append(file_artifact)
                    else:
                        # We dealt with direct URL archives above outside this loop; so this
                        # FileArtifact is unexpected.
                        production_assert(
                            False,
                            "Unexpected file artifact {filename} for locked requirement {pin}: "
                            "{url}".format(
                                filename=artifact.filename,
                                pin=locked_requirement.pin,
                                url=artifact.url.download_url,
                            ),
                        )
                elif isinstance(artifact, VCSArtifact):
                    if not artifact.commit_id:
                        raise ValueError(
                            "Cannot export {url} in a PEP-751 lock.\n"
                            "\n"
                            "A commit id is required to be resolved for VCS artifacts and none "
                            "was.\n"
                            "This most likely means the lock file was created by Pex older than "
                            "2.37.0 or that the lock was created using Python 2.7.\n"
                            "You'll need to re-create the lock with a newer Pex or newer Python or "
                            "both to be able to export it in PEP-851 format.".format(
                                url=artifact.url.raw_url
                            )
                        )
                    vcs_artifact = {
                        "type": artifact.vcs.value,
                        "url": artifact.vcs_url,
                        "commit-id": artifact.commit_id,
                    }
                    if artifact.requested_revision:
                        vcs_artifact["requested-revision"] = artifact.requested_revision
                    if artifact.subdirectory:
                        vcs_artifact["subdirectory"] = artifact.subdirectory
                    package["vcs"] = vcs_artifact
                else:
                    production_assert(isinstance(artifact, LocalProjectArtifact))
                    package["directory"] = {
                        "path": artifact.directory,
                        "editable": artifact.editable,
                    }
            if wheels:
                package["wheels"] = wheels

        packages[locked_requirement] = package

    for locked_requirement, marker in calculate_markers(packages):
        if marker:
            packages[locked_requirement]["marker"] = str(marker)

    pylock["packages"] = list(packages.values())
    return pylock
