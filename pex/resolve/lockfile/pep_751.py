# Copyright 2025 Pex project contributors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import

from collections import OrderedDict, defaultdict

from pex.exceptions import production_assert
from pex.resolve.locked_resolve import (
    DownloadableArtifact,
    FileArtifact,
    LocalProjectArtifact,
    TargetSystem,
    VCSArtifact,
)
from pex.resolve.resolved_requirement import Pin
from pex.typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, DefaultDict, Dict, List, Optional, Tuple, Union


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


def convert(
    locked_resolve,
    requires_python=None,  # type: Optional[str]
    target_systems=(),  # type: Tuple[TargetSystem.Value, ...]
    subset=(),  # type: Tuple[DownloadableArtifact, ...]
):
    # type: (...) -> Dict[str, Any]

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

    packages = []  # type: List[Dict[str, Any]]
    for locked_requirement in locked_resolve.locked_requirements:
        artifact_subset = artifact_subset_by_pin[locked_requirement.pin]
        if subset and not artifact_subset:
            continue

        package = {
            "name": str(locked_requirement.pin.project_name),
            "version": str(locked_requirement.pin.version),
        }  # type: Dict[str, Any]
        # TODO: XXX: Handle marker synthesizing.

        if locked_requirement.requires_python:
            package["requires-python"] = str(locked_requirement.requires_python)

        if locked_requirement.requires_dists:
            dependencies = []  # type: List[Dict[str, Any]]
            for dep in locked_requirement.requires_dists:
                dependencies.append({"name": str(dep.project_name)})
            package["dependencies"] = dependencies

        wheels = []  # type: List[Dict[str, Any]]

        for artifact in artifact_subset or locked_requirement.iter_artifacts():
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
                    # TODO: XXX: figure out if file artifact is from url requirement or name req to
                    #  distinguish archive from other forms.
                    production_assert(
                        False,
                        "TODO: XXX: figure out if file artifact is from url requirement or name "
                        "req to distinguish archive from other forms.",
                    )
            elif isinstance(artifact, VCSArtifact):
                if not artifact.commit_id:
                    raise ValueError(
                        "Cannot export {url} in a PEP-751 lock.\n"
                        "\n"
                        "A commit id is required to be resolved for VCS artifacts and none was.\n"
                        "This most likely means the lock file was created by Pex older than 2.36.0"
                        "or that the lock was created using Python 2.7.\n"
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

        packages.append(package)

    pylock["packages"] = packages
    return pylock
