# Copyright 2022 Pex project contributors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

import itertools
import os
import re
import shutil

import pytest

from pex.common import safe_open
from pex.fetcher import URLFetcher
from pex.pip.version import PipVersion
from pex.resolve.locked_resolve import Artifact, FileArtifact, LockedRequirement
from pex.resolve.lockfile import json_codec
from pex.resolve.lockfile.model import Lockfile
from pex.resolve.resolved_requirement import ArtifactURL
from pex.typing import TYPE_CHECKING
from testing import make_env, run_pex_command, subprocess
from testing.cli import run_pex3

if TYPE_CHECKING:
    from typing import Any, Iterator


def iter_requirements(lf):
    # type: (Lockfile) -> Iterator[LockedRequirement]
    return itertools.chain.from_iterable(
        locked_resolve.locked_requirements for locked_resolve in lf.locked_resolves
    )


def assert_file_artifact(artifact):
    # type: (Artifact) -> FileArtifact
    assert isinstance(artifact, FileArtifact)
    return artifact


def iter_artifacts(lf):
    # type: (Lockfile) -> Iterator[FileArtifact]
    return itertools.chain.from_iterable(
        map(assert_file_artifact, locked_requirement.iter_artifacts())
        for locked_requirement in iter_requirements(lf)
    )


@pytest.fixture
def lock(tmpdir):
    # type: (Any) -> str
    return os.path.join(str(tmpdir), "lock")


@pytest.fixture
def lock_file(lock):
    # type: (str) -> Lockfile
    run_pex3(
        "lock", "create", "ansicolors==1.1.8", "--style", "universal", "-o", lock, "--indent", "2"
    ).assert_success()
    return json_codec.load(lock)


@pytest.fixture
def find_links(
    tmpdir,  # type: Any
    lock_file,  # type: Lockfile
):
    # type: (...) -> str

    url_fetcher = URLFetcher()
    find_links = os.path.join(str(tmpdir), "find_links")
    for artifact in iter_artifacts(lock_file):
        with url_fetcher.get_body_stream(artifact.url.download_url) as url_fp:
            with safe_open(os.path.join(find_links, artifact.filename), "wb") as fl_fp:
                shutil.copyfileobj(url_fp, fl_fp)

    # We need the current default --pip-version requirements for some tests that do PyPI offline
    # resolves.
    pip_version = PipVersion.DEFAULT
    repository_pex = os.path.join(str(tmpdir), "repository.pex")
    run_pex_command(
        args=[
            str(pip_version.setuptools_requirement),
            str(pip_version.wheel_requirement),
            "--include-tools",
            "-o",
            repository_pex,
        ]
    ).assert_success()
    subprocess.check_call(
        args=[repository_pex, "repository", "extract", "-f", find_links], env=make_env(PEX_TOOLS=1)
    )
    return find_links


def test_lock_update_repo_migration_dry_run(
    lock,  # type: str
    lock_file,  # type: Lockfile
    find_links,  # type: str
):
    # type: (...) -> None

    result = run_pex3(
        "lock", "update", "--pin", "--no-pypi", "--find-links", find_links, lock, "--dry-run"
    )
    result.assert_success()
    assert re.match(
        r"^Updates for lock generated by universal:\n"
        r"  Would update ansicolors 1\.1\.8 artifacts:\n"
        r"    https?://\S+/ansicolors-1\.1\.8-py2\.py3-none-any\.whl -> "
        r"file://{find_links}/ansicolors-1\.1\.8-py2\.py3-none-any\.whl\n"
        r"    https?://\S+/ansicolors-1\.1\.8\.zip -> "
        r"file://{find_links}/ansicolors-1\.1\.8\.zip\n$".format(find_links=re.escape(find_links)),
        result.output,
    ), result.output


def test_lock_update_repo_migration_dry_run_path_mapping(
    lock,  # type: str
    lock_file,  # type: Lockfile
    find_links,  # type: str
):
    # type: (...) -> None

    result = run_pex3(
        "lock",
        "update",
        "--pin",
        "--no-pypi",
        "--find-links",
        find_links,
        lock,
        "--dry-run",
        "--path-mapping",
        "FOO|{find_links}".format(find_links=find_links),
    )
    result.assert_success()
    assert re.match(
        r"^Updates for lock generated by universal:\n"
        r"  Would update ansicolors 1\.1\.8 artifacts:\n"
        r"    https?://\S+/ansicolors-1\.1\.8-py2\.py3-none-any\.whl -> "
        r"file://\$\{FOO}/ansicolors-1\.1\.8-py2\.py3-none-any\.whl\n"
        r"    https?://\S+/ansicolors-1\.1\.8\.zip -> "
        r"file://\$\{FOO}/ansicolors-1\.1\.8\.zip\n$",
        result.output,
    ), result.output


def test_lock_update_repo_migration_nominal(
    lock,  # type: str
    lock_file,  # type: Lockfile
    find_links,  # type: str
):
    # type: (...) -> None

    artifacts_by_filename = {artifact.filename: artifact for artifact in iter_artifacts(lock_file)}

    run_pex3(
        "lock", "update", "--pin", "--no-pypi", "--find-links", find_links, lock
    ).assert_success()

    for updated_artifact in iter_artifacts(json_codec.load(lock)):
        original_artifact = artifacts_by_filename.pop(updated_artifact.filename)
        assert original_artifact.fingerprint == updated_artifact.fingerprint
        assert original_artifact.url != updated_artifact.url
        assert (
            ArtifactURL.parse(
                "file://{}".format(os.path.join(find_links, original_artifact.filename))
            )
            == updated_artifact.url
        )
    assert not artifacts_by_filename


def test_lock_update_repo_migration_corrupted(
    lock,  # type: str
    lock_file,  # type: Lockfile
    find_links,  # type: str
):
    # type: (...) -> None

    with open(os.path.join(find_links, "ansicolors-1.1.8.zip"), "ab") as fp:
        fp.write(b"changed")

    result = run_pex3(
        "lock",
        "update",
        "--pin",
        "--no-pypi",
        "--find-links",
        find_links,
        lock,
        "--dry-run",
        "--path-mapping",
        "FOO|{find_links}".format(find_links=find_links),
    )
    result.assert_failure()
    assert re.match(
        r"^Updates for lock generated by universal:\n"
        r"  Would update ansicolors 1\.1\.8 artifacts:\n"
        r"    https?://\S+/ansicolors-1\.1\.8-py2\.py3-none-any\.whl -> "
        r"file://\$\{FOO}/ansicolors-1\.1\.8-py2\.py3-none-any\.whl\n"
        r"    https?://\S+/ansicolors-1\.1\.8\.zip"
        r"#sha256:99f94f5e3348a0bcd43c82e5fc4414013ccc19d70bd939ad71e0133ce9c372e0 -> "
        r"file://\$\{FOO}/ansicolors-1\.1\.8\.zip"
        r"#sha256:9c872ada674e45fe740ebd4d00267617875e76d9bec4b3ce2e194e83661680dc\n$",
        result.output,
    ), result.output
    assert (
        "Detected fingerprint changes in the following locked project for lock generated by "
        "universal!\n"
        "ansicolors 1.1.8\n"
    ) in result.error


def test_lock_update_repo_migration_artifacts_removed(
    lock,  # type: str
    lock_file,  # type: Lockfile
    find_links,  # type: str
):
    # type: (...) -> None

    os.unlink(os.path.join(find_links, "ansicolors-1.1.8-py2.py3-none-any.whl"))
    result = run_pex3(
        "lock",
        "update",
        "--pin",
        "--no-pypi",
        "--find-links",
        find_links,
        lock,
        "--dry-run",
        "--path-mapping",
        "FOO|{find_links}".format(find_links=find_links),
    )
    result.assert_success()
    assert re.match(
        r"^Updates for lock generated by universal:\n"
        r"  Would update ansicolors 1\.1\.8 artifacts:\n"
        r"    https?://\S+/ansicolors-1\.1\.8\.zip -> "
        r"file://\$\{FOO}/ansicolors-1\.1\.8\.zip\n"
        r"    - https?://\S+/ansicolors-1\.1\.8-py2\.py3-none-any\.whl\n$",
        result.output,
    ), result.output


def test_lock_update_repo_migration_artifacts_added(
    lock,  # type: str
    lock_file,  # type: Lockfile
    find_links,  # type: str
):
    # type: (...) -> None

    shutil.copy(
        os.path.join(find_links, "ansicolors-1.1.8.zip"),
        os.path.join(find_links, "ansicolors-1.1.8.tar.gz"),
    )
    result = run_pex3(
        "lock",
        "update",
        "--pin",
        "--no-pypi",
        "--find-links",
        find_links,
        lock,
        "--dry-run",
        "--path-mapping",
        "FOO|{find_links}".format(find_links=find_links),
    )
    result.assert_success()
    assert re.search(
        r"Updates for lock generated by universal:\n"
        r"  Would update ansicolors 1\.1\.8 artifacts:\n"
        r"    \+ file://\$\{FOO}/ansicolors-1\.1\.8\.tar\.gz\n"
        r"    https?://\S+/ansicolors-1\.1\.8-py2\.py3-none-any\.whl -> "
        r"file://\$\{FOO}/ansicolors-1\.1\.8-py2\.py3-none-any\.whl\n"
        r"    https?://\S+/ansicolors-1\.1\.8\.zip -> "
        r"file://\$\{FOO}/ansicolors-1\.1\.8\.zip\n$",
        result.output,
    ), result.output
