# Copyright 2025 Pex project contributors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from pex.exceptions import production_assert
from pex.resolve.locked_resolve import (
    FileArtifact,
    LocalProjectArtifact,
    LockStyle,
    TargetSystem,
    VCSArtifact,
)
from pex.resolve.lockfile.model import Lockfile
from pex.typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Dict, List


def _to_environment(system):
    # type: (TargetSystem.Value) -> str
    if system is TargetSystem.LINUX:
        return "platform_system = 'Linux'"
    elif system is TargetSystem.MAC:
        return "platform_system = 'Darwin'"
    else:
        production_assert(system is TargetSystem.WINDOWS)
        return "platform_system = 'Windows'"


_LOCK_BOILERPLATE = {
    "lock-version": "1.0",
    "created-by": "Pex",
}  # type: Dict[str, Any]


def _boilerplate(lock_file):
    # type: (Lockfile) -> Dict[str, Any]
    pylock = _LOCK_BOILERPLATE.copy()
    if lock_file.requires_python:
        if len(lock_file.requires_python) > 1:
            # TODO: XXX: Better error message - we can guide on OR -> AND with != to remove disjoint
            #  portions of the range.
            raise ValueError("Can only export a lock file with a single interpreter constraint.")
        pylock["requires-python"] = lock_file.requires_python[0]
    return pylock


def convert(lock_file):
    # type: (Lockfile) -> Dict[str, Any]

    production_assert(lock_file.style is LockStyle.UNIVERSAL)
    locked_resolve = lock_file.locked_resolves[0]

    pylock = _boilerplate(lock_file)
    if lock_file.target_systems:
        pylock["environments"] = [_to_environment(system) for system in lock_file.target_systems]

    packages = []  # type: List[Dict[str, Any]]
    for locked_requirement in locked_resolve.locked_requirements:
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
        for artifact in locked_requirement.iter_artifacts():
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
                    production_assert(
                        False,
                        "TODO: XXX: figure out if file artifact is from url requirement or name "
                        "req to distinguish archive from other forms.",
                    )
            elif isinstance(artifact, VCSArtifact):
                package["vcs"] = {
                    "type": artifact.vcs,
                    "url": artifact.url,
                    # TODO: XXX: path
                    # TODO: XXX: requested-revision / commit-id
                    # TODO: XXX: subdirectory
                }
            else:
                production_assert(isinstance(artifact, LocalProjectArtifact))
                package["directory"] = {
                    "path": artifact.directory,
                    # TODO: XXX: editable
                }
        if wheels:
            package["wheels"] = wheels

        packages.append(package)

    pylock["packages"] = packages
    return pylock
