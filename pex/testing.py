# Copyright 2014 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import, print_function

import contextlib
import itertools
import os
import random
import subprocess
import sys
from contextlib import contextmanager
from textwrap import dedent
from typing import Sequence

import pytest

from pex.atomic_directory import atomic_directory
from pex.common import open_zip, safe_mkdir, safe_mkdtemp, safe_rmtree, safe_sleep, temporary_dir
from pex.compatibility import to_unicode
from pex.dist_metadata import Distribution
from pex.enum import Enum
from pex.executor import Executor
from pex.interpreter import PythonInterpreter
from pex.os import LINUX, MAC, WINDOWS
from pex.pex import PEX
from pex.pex_builder import PEXBuilder
from pex.pex_info import PexInfo
from pex.pip.installation import get_pip
from pex.script import is_python_script
from pex.sysconfig import SCRIPT_DIR, script_name
from pex.targets import LocalInterpreter
from pex.typing import TYPE_CHECKING, cast
from pex.util import named_temporary_file
from pex.venv.virtualenv import InvalidVirtualenvError, Virtualenv

if TYPE_CHECKING:
    from typing import (
        Any,
        Callable,
        Dict,
        Iterable,
        Iterator,
        List,
        Mapping,
        Optional,
        Set,
        Text,
        Tuple,
        Union,
    )

    import attr  # vendor:skip
else:
    from pex.third_party import attr

PY_VER = sys.version_info[:2]
IS_PYPY = hasattr(sys, "pypy_version_info")
IS_PYPY2 = IS_PYPY and sys.version_info[0] == 2
IS_PYPY3 = IS_PYPY and sys.version_info[0] == 3
NOT_CPYTHON27 = IS_PYPY or PY_VER != (2, 7)
IS_LINUX = LINUX
IS_MAC = MAC
IS_NOT_LINUX = not IS_LINUX
NOT_CPYTHON27_OR_OSX = NOT_CPYTHON27 or IS_NOT_LINUX


@contextlib.contextmanager
def temporary_filename():
    # type: () -> Iterator[str]
    """Creates a temporary filename.

    This is useful when you need to pass a filename to an API. Windows requires all handles to a
    file be closed before deleting/renaming it, so this makes it a bit simpler.
    """
    with named_temporary_file() as fp:
        fp.write(b"")
        fp.close()
        yield fp.name


def random_bytes(length):
    # type: (int) -> bytes
    return "".join(map(chr, (random.randint(ord("a"), ord("z")) for _ in range(length)))).encode(
        "utf-8"
    )


def get_dep_dist_names_from_pex(pex_path, match_prefix=""):
    # type: (str, str) -> Set[str]
    """Given an on-disk pex, extract all of the unique first-level paths under `.deps`."""
    with open_zip(pex_path) as pex_zip:
        dep_gen = (f.split("/")[1] for f in pex_zip.namelist() if f.startswith(".deps/"))
        return set(item for item in dep_gen if item.startswith(match_prefix))


@contextlib.contextmanager
def temporary_content(content_map, interp=None, seed=31337, perms=0o644):
    # type: (Mapping[str, Union[int, str]], Optional[Dict[str, Any]], int, int) -> Iterator[str]
    """Write content to disk where content is map from string => (int, string).

    If target is int, write int random bytes.  Otherwise write contents of string.
    """
    random.seed(seed)
    interp = interp or {}
    with temporary_dir() as td:
        for filename, size_or_content in content_map.items():
            dest = os.path.join(td, filename)
            safe_mkdir(os.path.dirname(dest))
            with open(dest, "wb") as fp:
                if isinstance(size_or_content, int):
                    fp.write(random_bytes(size_or_content))
                else:
                    fp.write((size_or_content % interp).encode("utf-8"))
            os.chmod(dest, perms)
        yield td


@contextlib.contextmanager
def make_project(
    name="my_project",  # type: str
    version="0.0.0",  # type: str
    zip_safe=True,  # type: bool
    install_reqs=None,  # type: Optional[List[str]]
    extras_require=None,  # type: Optional[Dict[str, List[str]]]
    entry_points=None,  # type: Optional[Union[str, Dict[str, List[str]]]]
    python_requires=None,  # type: Optional[str]
    universal=False,  # type: bool
):
    # type: (...) -> Iterator[str]
    project_content = {
        "setup.py": dedent(
            """
            from setuptools import setup
            
            setup(
            name=%(project_name)r,
            version=%(version)r,
            zip_safe=%(zip_safe)r,
            packages=[%(project_name)r],
            scripts=[
              'scripts/%(hello_world_script_name)s',
              'scripts/%(shell_script_name)s',
            ],
            package_data={%(project_name)r: ['package_data/*.dat']},
            install_requires=%(install_requires)r,
            extras_require=%(extras_require)r,
            entry_points=%(entry_points)r,
            python_requires=%(python_requires)r,
            options={'bdist_wheel': {'universal': %(universal)r}},
            )
            """
        ),
        os.path.join(name, "__init__.py"): 0,
        os.path.join(name, "my_module.py"): 'def do_something():\n  print("hello world!")\n',
        os.path.join(name, "package_data/resource1.dat"): 1000,
        os.path.join(name, "package_data/resource2.dat"): 1000,
    }  # type: Dict[str, Union[str, int]]

    if WINDOWS:
        project_content.update(
            (
                (
                    "scripts/hello_world.py",
                    '#!/usr/bin/env python\r\nprint("hello world from py script!")\r\n',
                ),
                ("scripts/shell_script.bat", "@echo off\r\necho hello world from shell script\r\n"),
            )
        )
    else:
        project_content.update(
            (
                (
                    "scripts/hello_world",
                    '#!/usr/bin/env python\nprint("hello world from py script!")\n',
                ),
                (
                    "scripts/shell_script",
                    "#!/usr/bin/env bash\necho hello world from shell script\n",
                ),
            )
        )

    interp = {
        "project_name": name,
        "version": version,
        "zip_safe": zip_safe,
        "install_requires": install_reqs or [],
        "extras_require": extras_require or {},
        "entry_points": entry_points or {},
        "python_requires": python_requires,
        "universal": universal,
        "hello_world_script_name": "hello_world.py" if WINDOWS else "hello_world",
        "shell_script_name": "shell_script.bat" if WINDOWS else "shell_script",
    }

    with temporary_content(project_content, interp=interp) as td:
        yield td


class WheelBuilder(object):
    """Create a wheel distribution from an unpacked setup.py-based project."""

    class BuildFailure(Exception):
        pass

    def __init__(
        self,
        source_dir,  # type: str
        interpreter=None,  # type: Optional[PythonInterpreter]
        wheel_dir=None,  # type: Optional[str]
        verify=True,  # type: bool
    ):
        # type: (...) -> None
        """Create a wheel from an unpacked source distribution in source_dir."""
        self._source_dir = source_dir
        self._wheel_dir = wheel_dir or safe_mkdtemp()
        self._interpreter = interpreter or PythonInterpreter.get()
        self._verify = verify

    def bdist(self):
        # type: () -> str
        get_pip(interpreter=self._interpreter).spawn_build_wheels(
            distributions=[self._source_dir],
            wheel_dir=self._wheel_dir,
            interpreter=self._interpreter,
            verify=self._verify,
        ).wait()
        dists = os.listdir(self._wheel_dir)
        if len(dists) == 0:
            raise self.BuildFailure("No distributions were produced!")
        if len(dists) > 1:
            raise self.BuildFailure("Ambiguous source distributions found: %s" % (" ".join(dists)))
        return os.path.join(self._wheel_dir, dists[0])


@contextlib.contextmanager
def built_wheel(
    name="my_project",  # type: str
    version="0.0.0",  # type: str
    zip_safe=True,  # type: bool
    install_reqs=None,  # type: Optional[List[str]]
    extras_require=None,  # type: Optional[Dict[str, List[str]]]
    entry_points=None,  # type: Optional[Union[str, Dict[str, List[str]]]]
    interpreter=None,  # type: Optional[PythonInterpreter]
    python_requires=None,  # type: Optional[str]
    universal=False,  # type: bool
    **kwargs  # type: Any
):
    # type: (...) -> Iterator[str]
    with make_project(
        name=name,
        version=version,
        zip_safe=zip_safe,
        install_reqs=install_reqs,
        extras_require=extras_require,
        entry_points=entry_points,
        python_requires=python_requires,
        universal=universal,
    ) as td:
        builder = WheelBuilder(td, interpreter=interpreter, **kwargs)
        yield builder.bdist()


@contextlib.contextmanager
def make_source_dir(
    name="my_project",  # type: str
    version="0.0.0",  # type: str
    install_reqs=None,  # type: Optional[List[str]]
    extras_require=None,  # type: Optional[Dict[str, List[str]]]
):
    # type: (...) -> Iterator[str]
    with make_project(
        name=name, version=version, install_reqs=install_reqs, extras_require=extras_require
    ) as td:
        yield td


@contextlib.contextmanager
def make_bdist(
    name="my_project",  # type: str
    version="0.0.0",  # type: str
    zip_safe=True,  # type: bool
    interpreter=None,  # type: Optional[PythonInterpreter]
    **kwargs  # type: Any
):
    # type: (...) -> Iterator[Distribution]
    with built_wheel(
        name=name, version=version, zip_safe=zip_safe, interpreter=interpreter, **kwargs
    ) as dist_location:
        yield install_wheel(dist_location, interpreter=interpreter)


def install_wheel(
    wheel,  # type: str
    interpreter=None,  # type: Optional[PythonInterpreter]
):
    # type: (...) -> Distribution
    install_dir = os.path.join(safe_mkdtemp(), os.path.basename(wheel))
    get_pip(interpreter=interpreter).spawn_install_wheel(
        wheel=wheel,
        install_dir=install_dir,
        target=LocalInterpreter.create(interpreter),
    ).wait()
    return Distribution.load(install_dir)


COVERAGE_PREAMBLE = """
try:
  from coverage import coverage
  cov = coverage(auto_data=True, data_suffix=True)
  cov.start()
except ImportError:
  pass
"""


def write_simple_pex(
    td,  # type: str
    exe_contents=None,  # type: Optional[str]
    dists=None,  # type: Optional[Iterable[Distribution]]
    sources=None,  # type: Optional[Iterable[Tuple[str, str]]]
    coverage=False,  # type: bool
    interpreter=None,  # type: Optional[PythonInterpreter]
    pex_info=None,  # type: Optional[PexInfo]
):
    # type: (...) -> PEXBuilder
    """Write a pex file that optionally contains an executable entry point.

    :param td: temporary directory path
    :param exe_contents: entry point python file
    :param dists: distributions to include, typically sdists or bdists
    :param sources: sources to include, as a list of pairs (env_filename, contents)
    :param coverage: include coverage header
    :param interpreter: a custom interpreter to use to build the pex
    :param pex_info: a custom PexInfo to use to build the pex.
    """
    dists = dists or []
    sources = sources or []

    safe_mkdir(td)

    pb = PEXBuilder(
        path=td,
        preamble=COVERAGE_PREAMBLE if coverage else None,
        interpreter=interpreter,
        pex_info=pex_info,
    )

    for dist in dists:
        pb.add_dist_location(dist.location if isinstance(dist, Distribution) else dist)

    for env_filename, contents in sources:
        src_path = os.path.join(td, env_filename)
        safe_mkdir(os.path.dirname(src_path))
        with open(src_path, "w") as fp:
            fp.write(contents)
        pb.add_source(src_path, env_filename)

    if exe_contents:
        with open(os.path.join(td, "exe.py"), "w") as fp:
            fp.write(exe_contents)
        pb.set_executable(os.path.join(td, "exe.py"))

    pb.freeze()

    return pb


@attr.s(frozen=True)
class IntegResults(object):
    """Convenience object to return integration run results."""

    argv = attr.ib()  # type: Tuple[Text, ...]
    output = attr.ib()  # type: Text
    error = attr.ib()  # type: Text
    return_code = attr.ib()  # type: int

    def _failure_message(self, expectation):
        return (
            "Expected {expectation} for {argv} but got exit code {exit_code}\n"
            "STDOUT:\n"
            "{stdout}\n"
            "STDERR:\n"
            "{stderr}".format(
                expectation=expectation,
                argv=" ".join(self.argv),
                exit_code=self.return_code,
                stdout=self.output,
                stderr=self.error,
            )
        )

    def assert_success(self):
        # type: () -> None
        assert self.return_code == 0, self._failure_message("success")

    def assert_failure(self):
        # type: () -> None
        assert self.return_code != 0, self._failure_message("failure")


def create_pex_command(
    args=None,  # type: Optional[Iterable[str]]
    python=None,  # type: Optional[str]
    quiet=False,  # type: bool
):
    # type: (...) -> List[str]
    cmd = [python or sys.executable, "-mpex"]
    if not quiet:
        cmd.append("-vvvvv")
    if args:
        cmd.extend(args)
    return cmd


def run_pex_command(
    args,  # type: Iterable[str]
    env=None,  # type: Optional[Dict[str, str]]
    python=None,  # type: Optional[str]
    quiet=False,  # type: bool
):
    # type: (...) -> IntegResults
    """Simulate running pex command for integration testing.

    This is different from run_simple_pex in that it calls the pex command rather than running a
    generated pex.  This is useful for testing end to end runs with specific command line arguments
    or env options.
    """
    cmd = create_pex_command(args, python=python, quiet=quiet)
    process = Executor.open_process(
        cmd=cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    output, error = process.communicate()
    return IntegResults(
        argv=tuple(cmd),
        output=output.decode("utf-8"),
        error=error.decode("utf-8"),
        return_code=process.returncode,
    )


def run_simple_pex(
    pex,  # type: str
    args=(),  # type: Iterable[str]
    interpreter=None,  # type: Optional[PythonInterpreter]
    stdin=None,  # type: Optional[bytes]
    **kwargs  # type: Any
):
    # type: (...) -> Tuple[bytes, int]
    p = PEX(pex, interpreter=interpreter)
    process = p.run(
        args=args,
        blocking=False,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        **kwargs
    )
    stdout, _ = process.communicate(input=stdin)
    return stdout.replace(b"\r", b""), process.returncode


def run_simple_pex_test(
    body,  # type: str
    args=(),  # type: Iterable[str]
    env=None,  # type: Optional[Mapping[str, str]]
    dists=None,  # type: Optional[Iterable[Distribution]]
    coverage=False,  # type: bool
    interpreter=None,  # type: Optional[PythonInterpreter]
):
    # type: (...) -> Tuple[bytes, int]
    with temporary_dir() as td1, temporary_dir() as td2:
        pb = write_simple_pex(td1, body, dists=dists, coverage=coverage, interpreter=interpreter)
        pex = os.path.join(td2, "app.pex")
        pb.build(pex)
        return run_simple_pex(pex, args=args, env=env, interpreter=interpreter)


PYENV_GIT_URL = "https://github.com/{pyenv}".format(
    pyenv="pyenv-win/pyenv-win.git" if WINDOWS else "pyenv/pyenv.git"
)


def bootstrap_python_installer(dest):
    # type: (str) -> None
    for _ in range(3):
        try:
            pex_check_call(["git", "clone", PYENV_GIT_URL, dest])
        except subprocess.CalledProcessError as e:
            print("caught exception: %r" % e)
            continue
        else:
            break
    else:
        raise RuntimeError("Helper method could not clone pyenv from git after 3 tries")


# NB: We keep the pool of bootstrapped interpreters as small as possible to avoid timeouts in CI
# otherwise encountered when fetching and building too many on a cache miss. In the past we had
# issues with the combination of 7 total unique interpreter versions and a Travis-CI timeout of 50
# minutes for a shard.
# N.B.: Make sure to stick to versions that have binary releases for all supported platforms to
# support use of pyenv-win which does not build from source, just running released installers
# robotically instead.
PY27 = "2.7.18"
PY38 = "3.8.10"
PY39 = "3.9.13"
PY310 = "3.10.7"

ALL_PY_VERSIONS = (PY27, PY38, PY39, PY310)
_ALL_PY_VERSIONS_TO_VERSION_INFO = {
    version: tuple(map(int, version.split("."))) for version in ALL_PY_VERSIONS
}


@attr.s(frozen=True)
class PythonDistribution(object):
    @classmethod
    def from_venv(cls, venv):
        # type: (str) -> PythonDistribution
        virtualenv = Virtualenv(venv)
        return cls(home=venv, interpreter=virtualenv.interpreter, pip=virtualenv.bin_path("pip"))

    home = attr.ib()  # type: str
    interpreter = attr.ib()  # type: PythonInterpreter
    pip = attr.ib()  # type: str

    @property
    def binary(self):
        # type: () -> str
        return self.interpreter.binary


@attr.s(frozen=True)
class PyenvPythonDistribution(PythonDistribution):
    pyenv_root = attr.ib()  # type: str
    _pyenv_script = attr.ib()  # type: str

    def pyenv_env(self, **extra_env):
        # type: (**str) -> Dict[str, str]
        env = os.environ.copy()
        env.update(extra_env)
        env["PYENV_ROOT"] = self.pyenv_root
        env["PATH"] = os.pathsep.join(
            [os.path.join(self.pyenv_root, path) for path in ("bin", "shims")]
            + os.getenv("PATH", os.defpath).split(os.pathsep)
        )
        return env

    def run_pyenv(
        self,
        args,  # type: Iterable[str]
        **popen_kwargs  # type: Any
    ):
        # type: (...) -> Text
        return pex_check_output(
            args=[self._pyenv_script] + list(args),
            env=self.pyenv_env(**popen_kwargs.pop("env", {})),
            **popen_kwargs
        ).decode("utf-8")


def ensure_python_distribution(version):
    # type: (str) -> PyenvPythonDistribution
    if version not in ALL_PY_VERSIONS:
        raise ValueError("Please constrain version to one of {}".format(ALL_PY_VERSIONS))

    assert not WINDOWS or _ALL_PY_VERSIONS_TO_VERSION_INFO[version][:2] >= (
        3,
        8,
    ), "Test uses pyenv {} interpreter which is not supported on Windows.".format(version)

    basedir = os.path.expanduser(
        os.environ.get("_PEX_TEST_PYENV_ROOT", os.path.join("~", ".pex_dev"))
    )
    clone_dir = os.path.abspath(os.path.join(basedir, "pyenv-win" if WINDOWS else "pyenv"))
    pyenv_root = os.path.join(clone_dir, "pyenv-win") if WINDOWS else clone_dir
    interpreter_location = os.path.join(pyenv_root, "versions", version)

    pyenv = os.path.join(pyenv_root, "bin", "pyenv.bat" if WINDOWS else "pyenv")

    if WINDOWS:
        python = os.path.join(interpreter_location, "python.exe")
    else:
        major, minor = version.split(".")[:2]
        python = os.path.join(
            interpreter_location, "bin", "python{major}.{minor}".format(major=major, minor=minor)
        )

    pip = os.path.join(interpreter_location, SCRIPT_DIR, script_name("pip"))

    with atomic_directory(target_dir=clone_dir) as atomic_dir:
        if not atomic_dir.is_finalized():
            bootstrap_python_installer(atomic_dir.work_dir)

    with atomic_directory(target_dir=interpreter_location) as interpreter_target_dir:
        if not interpreter_target_dir.is_finalized():
            pex_check_call(
                [
                    "git",
                    "--git-dir={}".format(os.path.join(clone_dir, ".git")),
                    "--work-tree={}".format(clone_dir),
                    "pull",
                    "--ff-only",
                    PYENV_GIT_URL,
                ]
            )
            env = os.environ.copy()
            env["PYENV_ROOT"] = pyenv_root
            if sys.platform.lower().startswith("linux"):
                env["CONFIGURE_OPTS"] = "--enable-shared"
                # The pyenv builder detects `--enable-shared` and sets up `RPATH` via
                # `LDFLAGS=-Wl,-rpath=... $LDFLAGS` to ensure the built python binary links the
                # correct libpython shared lib. Some versions of compiler set the `RUNPATH` instead
                # though which is searched _after_ the `LD_LIBRARY_PATH` environment variable. To
                # ensure an inopportune `LD_LIBRARY_PATH` doesn't fool the pyenv python binary into
                # linking the wrong libpython, force `RPATH`, which is searched 1st by the linker,
                # with with `--disable-new-dtags`.
                env["LDFLAGS"] = "-Wl,--disable-new-dtags"
            pex_check_call([pyenv, "install", version], env=env)
            pex_check_call([python, "-m", "pip", "install", "-U", "pip<22.1"])

    return PyenvPythonDistribution(
        home=interpreter_location,
        interpreter=PythonInterpreter.from_binary(python),
        pip=pip,
        pyenv_root=pyenv_root,
        pyenv_script=pyenv,
    )


def ensure_python_venv(
    version,  # type: str
    latest_pip=True,  # type: bool
    system_site_packages=False,  # type: bool
):
    # type: (...) -> Virtualenv
    pyenv_distribution = ensure_python_distribution(version)
    venv = safe_mkdtemp()
    if _ALL_PY_VERSIONS_TO_VERSION_INFO[version][0] == 3:
        args = [pyenv_distribution.binary, "-m", "venv", venv]
        if system_site_packages:
            args.append("--system-site-packages")
        pex_check_call(args=args)
    else:
        pex_check_call(args=[pyenv_distribution.pip, "install", "virtualenv==16.7.10"])
        args = [pyenv_distribution.binary, "-m", "virtualenv", venv, "-q"]
        if system_site_packages:
            args.append("--system-site-packages")
        pex_check_call(args=args)
    python, pip = tuple(
        os.path.join(venv, SCRIPT_DIR, script_name(exe)) for exe in ("python", "pip")
    )
    if latest_pip:
        pex_check_call(args=[python, "-mpip", "install", "-U", "pip<22.1"])
    return Virtualenv(venv)


def ensure_python_interpreter(version):
    # type: (str) -> str
    return ensure_python_distribution(version).binary


class InterpreterImplementation(Enum["InterpreterImplementation.Value"]):
    class Value(Enum.Value):
        pass

    CPython = Value("CPython")
    PyPy = Value("PyPy")


def find_python_interpreter(
    version=(),  # type: Tuple[int, ...]
    implementation=InterpreterImplementation.CPython,  # type: InterpreterImplementation.Value
):
    # type: (...) -> Optional[str]
    for pyenv_version, penv_version_info in _ALL_PY_VERSIONS_TO_VERSION_INFO.items():
        if version and version == penv_version_info[: len(version)]:
            return ensure_python_interpreter(pyenv_version)

    for interpreter in PythonInterpreter.iter():
        if version != interpreter.version[: len(version)]:
            continue
        if implementation != InterpreterImplementation.for_value(interpreter.identity.interpreter):
            continue
        return interpreter.binary

    return None


def skip_unless_python27(
    implementation=InterpreterImplementation.CPython,  # type: InterpreterImplementation.Value
):
    # type: (...) -> str

    if WINDOWS:
        pytest.skip("Pex for Windows does not support Python 2.7")

    python = find_python_interpreter(version=(2, 7), implementation=implementation)
    if python is not None:
        return python

    pytest.skip("Test requires a Python 2.7 on the PATH")
    raise AssertionError("Unreachable.")


def python_venv(
    python,  # type: str
    system_site_packages=False,  # type: bool
    venv_dir=None,  # type: Optional[str]
):
    # type: (...) -> Virtualenv
    venv = Virtualenv.create(
        venv_dir=venv_dir or safe_mkdtemp(),
        interpreter=PythonInterpreter.from_binary(python),
        system_site_packages=system_site_packages,
    )
    venv.install_pip()
    return venv


def skip_unless_python27_venv(
    implementation=InterpreterImplementation.CPython,  # type: InterpreterImplementation.Value
    system_site_packages=False,  # type: bool
    venv_dir=None,  # type: Optional[str]
):
    # type: (...) -> Virtualenv
    return python_venv(
        skip_unless_python27(implementation=implementation),
        system_site_packages=system_site_packages,
        venv_dir=venv_dir,
    )


def _applicable_py_versions():
    # type: () -> Iterable[str]
    for version in ALL_PY_VERSIONS:
        if WINDOWS and _ALL_PY_VERSIONS_TO_VERSION_INFO[version][:2] < (3, 8):
            continue
        yield version


def all_pythons():
    # type: () -> Tuple[str, ...]
    return tuple(ensure_python_interpreter(version) for version in _applicable_py_versions())


@attr.s(frozen=True)
class VenvFactory(object):
    python_version = attr.ib()  # type: str
    _factory = attr.ib()  # type: Callable[[], Virtualenv]

    def create_venv(self):
        # type: () -> Virtualenv
        return self._factory()


def all_python_venvs(system_site_packages=False):
    # type: (bool) -> Iterable[VenvFactory]
    return tuple(
        VenvFactory(
            python_version=version,
            factory=lambda: ensure_python_venv(version, system_site_packages=system_site_packages),
        )
        for version in _applicable_py_versions()
    )


@contextmanager
def environment_as(**kwargs):
    # type: (**Any) -> Iterator[None]
    existing = {key: os.environ.get(key) for key in kwargs}

    def adjust_environment(mapping):
        for key, value in mapping.items():
            if value is not None:
                os.environ[key] = str(value)
            else:
                os.environ.pop(key, None)

    adjust_environment(kwargs)
    try:
        yield
    finally:
        adjust_environment(existing)


@contextmanager
def pushd(directory):
    # type: (str) -> Iterator[None]
    cwd = os.getcwd()
    try:
        os.chdir(directory)
        yield
    finally:
        os.chdir(cwd)


def make_env(
    *args,  # type: Tuple[str, Any]
    **kwargs  # type: Any
):
    # type: (...) -> Dict[str, str]
    """Create a copy of the current environment with the given modifications.

    The given kwargs add to or update the environment when they have a non-`None` value. When they
    have a `None` value, the environment variable is removed from the environment.

    All non-`None` values are converted to strings by apply `str`.
    """
    env = os.environ.copy()
    entries = args + tuple(kwargs.items())
    env.update((k, str(v)) for k, v in entries if v is not None)
    for k, v in entries:
        if v is None:
            env.pop(k, None)
    return env


def run_commands_with_jitter(
    commands,  # type: Iterable[Iterable[str]]
    path_argument,  # type: str
    extra_env=None,  # type: Optional[Mapping[str, str]]
    delay=2.0,  # type: float
):
    # type: (...) -> List[str]
    """Runs the commands with tactics that attempt to introduce randomness in outputs.

    Each command will run against a clean Pex cache with a unique path injected as the value for
    `path_argument`. A unique `PYTHONHASHSEED` is set in the environment for each execution as well.

    Additionally, a delay is inserted between executions. By default, this delay is 2s to ensure zip
    precision is stressed. See: https://pkware.cachefly.net/webdocs/casestudies/APPNOTE.TXT.
    """
    td = safe_mkdtemp()
    pex_root = os.path.join(td, "pex_root")

    paths = []
    for index, command in enumerate(commands):
        path = os.path.join(td, str(index))
        cmd = list(command) + [path_argument, path]

        # Note that we change the `PYTHONHASHSEED` to ensure that there are no issues resulting
        # from the random seed, such as data structures, as Tox sets this value by default.
        # See:
        # https://tox.readthedocs.io/en/latest/example/basic.html#special-handling-of-pythonhashseed
        env = make_env(PEX_ROOT=pex_root, PYTHONHASHSEED=(index * 497) + 4)
        if extra_env:
            env.update(extra_env)

        if index > 0:
            safe_sleep(delay)

        # Ensure the PEX is fully rebuilt.
        safe_rmtree(pex_root)
        pex_check_call(args=cmd, env=env)
        paths.append(path)
    return paths


def run_command_with_jitter(
    args,  # type: Iterable[str]
    path_argument,  # type: str
    extra_env=None,  # type: Optional[Mapping[str, str]]
    delay=2.0,  # type: float
    count=3,  # type: int
):
    # type: (...) -> List[str]
    """Runs the command `count` times in an attempt to introduce randomness.

    Each run of the command will run against a clean Pex cache with a unique path injected as the
    value for `path_argument`. A unique `PYTHONHASHSEED` is set in the environment for each
    execution as well.

    Additionally, a delay is inserted between executions. By default, this delay is 2s to ensure zip
    precision is stressed. See: https://pkware.cachefly.net/webdocs/casestudies/APPNOTE.TXT.
    """
    return run_commands_with_jitter(
        commands=list(itertools.repeat(list(args), count)),
        path_argument=path_argument,
        extra_env=extra_env,
        delay=delay,
    )


def pex_project_dir():
    # type: () -> str
    return str(pex_check_output(["git", "rev-parse", "--show-toplevel"]).decode("ascii").strip())


def _maybe_load_pex_info(path):
    # type: (str) -> Optional[PexInfo]
    try:
        return PexInfo.from_pex(path)
    except (KeyError, IOError, OSError):
        return None


def _safe_args(args):
    # type: (Sequence[str]) -> List[str]
    if WINDOWS:
        argv0 = args[0]
        pex_info = _maybe_load_pex_info(argv0)
        if pex_info and is_python_script(argv0, check_executable=False):
            try:
                return [Virtualenv(os.path.dirname(argv0)).interpreter.binary] + list(args)
            except InvalidVirtualenvError:
                pass
        if pex_info or argv0.endswith(".py"):
            return [sys.executable] + list(args)
    return args if isinstance(args, list) else list(args)


def pex_call(
    args,  # type: Sequence[str]
    **kwargs  # type: Any
):
    # type: (...) -> int
    return subprocess.call(args=_safe_args(args), **kwargs)


def pex_check_call(
    args,  # type: Sequence[str]
    **kwargs  # type: Any
):
    # type: (...) -> None
    subprocess.check_call(args=_safe_args(args), **kwargs)


def pex_check_output(
    args,  # type: Sequence[str]
    **kwargs  # type: Any
):
    # type: (...) -> bytes
    return cast(bytes, subprocess.check_output(args=_safe_args(args), **kwargs))


def pex_popen(
    args,  # type: Sequence[str]
    **kwargs  # type: Any
):
    # type: (...) -> subprocess.Popen
    return subprocess.Popen(args=_safe_args(args), **kwargs)
