# Copyright 2023 Pex project contributors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import, print_function

import errno
import glob
import hashlib
import itertools
import json
import os
import os.path
import re
import shutil
import subprocess
import sys
from contextlib import closing
from email.message import Message
from fileinput import FileInput
from textwrap import dedent

from pex import pex_warnings
from pex.common import (
    chmod_plus_x,
    dir_size,
    is_pyc_dir,
    is_pyc_file,
    iter_copytree,
    iter_copytree_entries,
    open_zip,
    safe_delete,
    safe_mkdir,
    safe_open,
    safe_rmtree,
)
from pex.compatibility import commonpath, get_stdout_bytes_buffer
from pex.dist_metadata import (
    DistMetadata,
    Distribution,
    MetadataFiles,
    MetadataType,
    load_metadata,
    parse_message,
)
from pex.enum import Enum
from pex.interpreter import PythonInterpreter, create_shebang
from pex.orderedset import OrderedSet
from pex.pep_376 import InstalledFile, Record
from pex.provenance import Source
from pex.scripts import create_sh_python_redirector_shebang, is_exe, is_script
from pex.tracer import TRACER
from pex.typing import TYPE_CHECKING, cast
from pex.util import CacheHelper
from pex.venv.virtualenv import Virtualenv

if TYPE_CHECKING:
    from typing import (  # noqa
        Callable,
        DefaultDict,
        Dict,
        Iterable,
        Iterator,
        List,
        Optional,
        Set,
        Text,
        Tuple,
        Union,
    )

    import attr  # vendor:skip
else:
    from pex.third_party import attr


class WheelError(Exception):
    """Indicates an error interacting with a wheel."""


class InstallableType(Enum["InstallableType.Value"]):
    class Value(Enum.Value):
        pass

    INSTALLED_WHEEL_CHROOT = Value("installed wheel chroot")
    WHEEL_FILE = Value(".whl file")


_STASH_DIR = ".stash"


def installed_scripts_dir(stash_dir):
    # type: (str) -> str

    # For backwards compatibility with old chroot layouts, we fix the reified scripts dir to
    # always be the `bin` subdir of the stash.
    return os.path.join(stash_dir, "bin")


@attr.s(frozen=True)
class InstallPaths(object):
    @classmethod
    def chroot(
        cls,
        wheel,  # type: Wheel
        destination,  # type: str
    ):
        # type: (...) -> InstallPaths
        install_to = os.path.abspath(destination)
        stash_dir = os.path.join(install_to, _STASH_DIR)
        return cls(
            extract_dir=install_to,
            purelib=os.path.join(install_to, wheel.data_dir, "purelib"),
            platlib=os.path.join(install_to, wheel.data_dir, "platlib"),
            headers=os.path.join(install_to, wheel.data_dir, "headers"),
            scripts=installed_scripts_dir(stash_dir),
            data=os.path.join(install_to, wheel.data_dir, "data"),
        )

    @classmethod
    def flat(cls, destination):
        # type: (str) -> InstallPaths
        install_to = os.path.abspath(destination)
        return cls(
            extract_dir=install_to,
            purelib=install_to,
            platlib=install_to,
            headers=install_to,
            scripts=installed_scripts_dir(install_to),
            data=install_to,
        )

    @classmethod
    def interpreter(
        cls,
        wheel,  # type: Wheel
        interpreter,  # type: PythonInterpreter
        rel_extra_path=None,  # type: Optional[str]
    ):
        # type: (...) -> InstallPaths
        sysconfig_paths = interpreter.identity.paths
        purelib = sysconfig_paths["purelib"]
        platlib = sysconfig_paths["platlib"]

        extract_dir = purelib if wheel.purelib else platlib
        if rel_extra_path:
            extract_dir = os.path.join(extract_dir, rel_extra_path)

        if interpreter.is_venv:
            # We match the value Pip concocts in `pip._internal.locations` since Pip is the de-facto
            # standard people expect in the vacuum of Python / PyPA issuing more "MUST"y PEPs.
            #
            # The "headers" install scheme path is basically invalid today for venvs as tracked by:
            # + https://github.com/python/cpython/issues/88611
            # + https://discuss.python.org/t/deprecating-the-headers-wheel-data-key/23712/1
            #
            # The basic thrust in this Feb 2023 conversation is typical of the PyPA, roughly:
            #
            # > PyPA member:
            #   https://discuss.python.org/t/deprecating-the-headers-wheel-data-key/23712/7
            #   If NumPy has figured out how to make this work, it must be possible, so lets call it
            #   good then.
            # > Core SymPy maintainer:
            #   https://discuss.python.org/t/deprecating-the-headers-wheel-data-key/23712/8
            #   NumPy has figured it out, but by totally working around the longstanding Python /
            #   PyPA non-solution.
            # > Core Python member:
            #   https://discuss.python.org/t/deprecating-the-headers-wheel-data-key/23712/11
            #   I think the current situation is actually ideal!
            # > NumPy lead:
            #   https://discuss.python.org/t/deprecating-the-headers-wheel-data-key/23712/12
            #   We do insane BS to make this work which requires folks wishing to build against us
            #   to do further insane bs (c.f. numerous `setup_requires` / `setup.py` fiascoes
            #   ameliorated by PEP-518).
            #
            # The conversation bits I snipped contrast 2 non Python core / non PyPA members who know
            # what's going on against a Python core maintainer and a PyPA maintainer who do not.
            # This 2023 conversation ended with no resulting action, which contrasts with the real
            # use case Pex fixed in https://github.com/pantsbuild/pex/issues/1656 in 2022 which
            # originated at least as far back as 2010 in greenlet (
            # https://github.com/python-greenlet/greenlet/commit/93abb2fc95ef99527bed858966b8af457f3dc0a5#diff-60f61ab7a8d1910d86d9fda2261620314edcae5894d5aaa236b821c7256badd7R78)
            # which uses `setup(headers=...)` to attempt to allow other C-extensions (uwsgi is an
            # example) to link to it during sdist builds. Although
            # https://github.com/python-greenlet/greenlet/issues/96 is closed, the "solution" there
            # was to manually include the well-known Pip venv include location via ~:
            # ```
            # CFLAGS="-I/tmp/py3.5/include/site/python3.5" pip ...
            # ```
            headers = os.path.join(
                interpreter.prefix,
                "include",
                "site",
                interpreter.identity.binary_name(version_components=2),
                wheel.dist_metadata().project_name.normalized,
            )
        else:
            headers = sysconfig_paths["include"]

        return cls(
            extract_dir=extract_dir,
            purelib=purelib,
            platlib=platlib,
            headers=headers,
            scripts=sysconfig_paths["scripts"],
            data=sysconfig_paths["data"],
        )

    extract_dir = attr.ib()  # type: str
    purelib = attr.ib()  # type: str
    platlib = attr.ib()  # type: str
    headers = attr.ib()  # type: str
    scripts = attr.ib()  # type: str
    data = attr.ib()  # type: str

    def __getitem__(self, item):
        # type: (Text) -> str
        if "purelib" == item:
            return self.purelib
        elif "platlib" == item:
            return self.platlib
        elif "headers" == item:
            return self.headers
        elif "scripts" == item:
            return self.scripts
        elif "data" == item:
            return self.data
        raise KeyError("Not a known install path: {item}".format(item=item))


class WheelInstallError(WheelError):
    """Indicates an error installing a `.whl` file."""


def install_wheel_chroot(
    wheel_path,  # type: str
    destination,  # type: str
    compile=False,  # type: bool
):
    # type: (...) -> None

    wheel = Wheel.load(wheel_path)
    install_paths = InstallPaths.chroot(wheel, destination)
    recording = install_wheel(
        wheel,
        install_paths,
        compile=compile,
        # TODO(John Sirois): XXX: Document why we don't finalize / what that entails.
        finalize=False,
    )
    InstalledWheel.save(
        prefix_dir=destination,
        record_relpath=recording.record.relpath,
        data_dir=recording.data_relpath,
    )


def install_wheel_interpreter(
    wheel_path,  # type: str
    interpreter,  # type: PythonInterpreter
    symlink=False,  # type: bool
    target_python=None,  # type: Optional[str]
    hermetic_scripts=True,  # type: bool
    compile=True,  # type: bool
    rel_extra_path=None,  # type: Optional[str]
    requested=True,  # type: bool
):
    # type: (...) -> Recording

    wheel = Wheel.load(wheel_path)
    install_paths = InstallPaths.interpreter(wheel, interpreter, rel_extra_path=rel_extra_path)
    return install_wheel(
        wheel,
        install_paths,
        symlink=symlink,
        target_python=target_python or interpreter.binary,
        hermetic_scripts=hermetic_scripts,
        compile=compile,
        requested=requested,
    )


def install_wheel_flat(
    wheel_path,  # type: str
    destination,  # type: str
    symlink=False,  # type: bool
    target_python=None,  # type: Optional[str]
    compile=True,  # type: bool
    requested=True,  # type: bool
):
    # type: (...) -> Recording

    wheel = Wheel.load(wheel_path)
    install_paths = InstallPaths.flat(destination)
    return install_wheel(
        wheel,
        install_paths,
        symlink=symlink,
        target_python=target_python,
        compile=compile,
        requested=requested,
    )


class WheelLoadError(WheelError):
    """Indicates loading a wheel from disk."""


@attr.s(frozen=True)
class Wheel(object):
    @classmethod
    def load(cls, wheel_path):
        # type: (str) -> Wheel

        metadata_files = load_metadata(wheel_path, restrict_types_to=(MetadataType.DIST_INFO,))
        if not metadata_files:
            raise WheelLoadError("Could not find any metadata in {wheel}.".format(wheel=wheel_path))

        metadata_path = metadata_files.metadata_file_rel_path("WHEEL")
        metadata_bytes = metadata_files.read("WHEEL")
        if not metadata_path or not metadata_bytes:
            raise WheelLoadError(
                "Could not find WHEEL metadata in {wheel}.".format(wheel=wheel_path)
            )
        wheel_metadata_dir = os.path.dirname(metadata_path)
        if not wheel_metadata_dir.endswith(".dist-info"):
            raise WheelLoadError(
                "Expected WHEEL metadata for {wheel} to be housed in a .dist-info directory, but "
                "was found at {wheel_metadata_path}.".format(
                    wheel=wheel_path, wheel_metadata_path=metadata_path
                )
            )
        # Although not crisply defined, all PEPs lead to PEP-508 which restricts project names
        # to ASCII: https://peps.python.org/pep-0508/#names. Likewise, version numbers are also
        # restricted to ASCII: https://peps.python.org/pep-0440/. Since the `.dist-info` dir
        # path is defined as `<project name>-<version>.dist-info` in
        # https://peps.python.org/pep-0427/, we are safe in assuming ASCII overall for the wheel
        # metadata dir path.
        metadata_dir = str(wheel_metadata_dir)
        metadata = parse_message(metadata_bytes)

        data_dir = re.sub(r"\.dist-info$", ".data", metadata_dir)

        return cls(
            location=wheel_path,
            metadata_dir=metadata_dir,
            metadata_files=metadata_files,
            metadata=metadata,
            data_dir=data_dir,
        )

    location = attr.ib()  # type: str
    metadata_dir = attr.ib()  # type: str
    metadata_files = attr.ib()  # type: MetadataFiles
    metadata = attr.ib()  # type: Message
    data_dir = attr.ib()  # type: str

    @property
    def purelib(self):
        # type: () -> bool
        return cast(bool, "true" == self.metadata.get("Root-Is-Purelib"))

    def dist_metadata(self):
        # type: () -> DistMetadata
        return DistMetadata.from_metadata_files(self.metadata_files)

    def metadata_path(self, *components):
        # type: (*str) -> str
        return os.path.join(self.metadata_dir, *components)

    def data_path(self, *components):
        # type: (*str) -> str
        return os.path.join(self.data_dir, *components)


def _create_shebang(
    target_python=None,  # type: Optional[Text]
    args=None,  # type: Optional[Text]
):
    # type: (...) -> Text
    if target_python:
        return create_shebang(python_exe=target_python, python_args=args)

    # N.B: The exit codes below are picked from Linux's /usr/include/sysexits.h:
    # /*
    #  *  EX_USAGE -- The command was used incorrectly, e.g., with
    #  *      the wrong number of arguments, a bad flag, a bad
    #  *      syntax in a parameter, or whatever.
    #  *  EX_DATAERR -- The input data was incorrect in some way.
    #  *      This should only be used for user's data & not
    #  *      system files.
    #  */
    # #define EX_USAGE    64  /* command line usage error */
    # #define EX_DATAERR  65  /* data format error */
    return create_sh_python_redirector_shebang(
        sh_script_content=dedent(
            """\
            # N.B.: This script should stick to syntax defined for POSIX `sh` and avoid non-builtins.
            # See: https://pubs.opengroup.org/onlinepubs/9699919799/idx/shell.html
            set -eu

            if [ -z "${{PEX:-}}" ]; then
                echo >&2 "This script must be called with the PEX environment variable set to the"
                echo >&2 "path of an executable PEX."
                exit 64
            fi

            # Support all PEX layouts: zipapp files as well as loose and packed directories.
            if [ -d "$PEX" ]; then
                PEX="$PEX/__main__.py"
            fi
            if [ ! -x "$PEX" ]; then
                echo >&2 "The PEX environment variable is set to $PEX which does not point to an"
                echo >&2 "executable PEX."
                exit 65
            fi

            if [ -n "${{PEX_VERBOSE:-}}" ]; then
                echo >&2 "Re-executing $0 via the PEX interpreter at $PEX"
            fi
            PEX_INTERPRETER=1 exec "$PEX" {args} "$0" "$@"
            """
        ).format(args=args or "")
    )


@attr.s(frozen=True)
class Recording(object):
    record = attr.ib()  # type: Record
    copies = attr.ib()  # type: Tuple[Tuple[Source, Text], ...]
    data_relpath = attr.ib(init=False)  # type: str

    def __attrs_post_init__(self):
        # type: () -> None
        data_relpath = re.sub(r"\.dist-info$", ".data", os.path.dirname(self.record.relpath))
        object.__setattr__(self, "data_relpath", data_relpath)


@attr.s
class Recorder(object):
    _base = attr.ib()  # type: str
    _record_relpath = attr.ib()  # type: str
    _installed_files = attr.ib(factory=list)  # type: List[InstalledFile]
    _copies = attr.ib(factory=list)  # type: List[Tuple[Source, Text]]

    def record_file(self, file_path):
        # type: (Text) -> Optional[Text]
        if is_pyc_file(file_path):
            # These files are both optional to RECORD and should never be present in wheels
            # anyway per the spec.
            return None

        file_relpath = (
            os.path.relpath(file_path, self._base) if os.path.isabs(file_path) else file_path
        )
        if self._record_relpath == file_relpath:
            # We never want to use a pre-existing RECORD.
            return None

        file_abspath = (
            file_path if os.path.isabs(file_path) else os.path.join(self._base, file_path)
        )
        self._installed_files.append(InstalledFile.create(path=file_abspath, base=self._base))
        return file_abspath

    def record_copy(
        self,
        source,  # type: Source
        dest_path,  # type: Text
    ):
        # type: (...) -> Optional[Text]
        dest_abspath = self.record_file(dest_path)
        if dest_abspath:
            self._copies.append((source, dest_abspath))
        return dest_abspath

    def iter_python_files(self):
        # type: () -> Iterator[Text]
        for installed_file in self._installed_files:
            if installed_file.path.endswith(".py"):
                yield os.path.join(self._base, installed_file.path)

    def get_recording(self):
        # type: () -> Recording
        return Recording(
            record=Record(
                base=self._base,
                relpath=self._record_relpath,
                installed_files=tuple(self._installed_files),
            ),
            copies=tuple(self._copies),
        )


def create_script_args(
    hermetic_scripts=True,  # type: bool
    switches=None,  # type: Optional[Text]
):
    # type: (...) -> (Optional[Text])
    all_switches = OrderedSet("sE" if hermetic_scripts else ())  # type: OrderedSet[Text]
    if switches:
        all_switches.update(switches)
    if not all_switches:
        return None
    return "-{switches}".format(switches="".join(all_switches))


@attr.s(frozen=True)
class ScriptProcessor(object):
    @staticmethod
    def needs_processing(file_path):
        # type: (Text) -> bool
        chmod_plus_x(file_path)
        return is_script(
            file_path,
            # N.B.: The trailer supports passing Python switches like -i. See:
            #   https://github.com/pypa/pip/issues/10661
            pattern=br"^pythonw?(?: -[a-zA-Z]+)?$",
            check_executable=False,
        )

    hermetic_scripts = attr.ib()  # type: bool
    target_python = attr.ib()  # type: Optional[str]

    def process(self, scripts):
        # type: (Iterable[Text]) -> None

        # N.B.: The `FileInput(inplace=True,...)` ensures a new copy is edited (turns links into
        # copies), which is exactly what we want here in the installed wheel chroot source case;
        # i.e.: do not tamper with data dirs contents which need to stay pristine to support
        # .whl -> chroot -> .whl round-tripping. Only allow edits to the bin dir reified executable
        # scripts.
        with closing(FileInput(files=scripts, inplace=True, mode="rb")) as script_fi:
            for line in cast("Iterator[bytes]", script_fi):
                buffer = get_stdout_bytes_buffer()
                if script_fi.isfirstline():
                    # N.B.: Our needs_processing check above ensures the #!python shebang only has
                    # a switch arg block if it has args at all; so we can safely treat the
                    # shebang args as composed of single character Python interpreter
                    # switches.
                    _, _, shebang_args_bytes = line.partition(b" -")
                    shebang = _create_shebang(
                        target_python=self.target_python,
                        args=create_script_args(
                            hermetic_scripts=self.hermetic_scripts,
                            switches=shebang_args_bytes.decode("utf-8"),
                        ),
                    )
                    buffer.write("{shebang}\n".format(shebang=shebang).encode("utf-8"))
                else:
                    # N.B.: These lines include the newline already.
                    buffer.write(cast(bytes, line))


def install_wheel(
    wheel,  # type: Wheel
    install_paths,  # type: InstallPaths
    symlink=False,  # type: bool
    target_python=None,  # type: Optional[str]
    hermetic_scripts=True,  # type: bool
    compile=False,  # type: bool
    finalize=True,  # type: bool
    requested=True,  # type: bool
):
    # type: (...) -> Recording

    # See: https://packaging.python.org/en/latest/specifications/binary-distribution-format/#installing-a-wheel-distribution-1-0-py32-none-any-whl
    # 1. Unpack
    # TODO(John Sirois): Consider verifying signatures.
    # N.B.: Pip does not and its also not clear what good this does. A zip can be easily poked
    # on a per-entry basis allowing forging a RECORD entry and its associated file. Only an
    # outer fingerprint of the whole wheel really solves this sort of tampering.
    data_sources = {}  # type: Dict[Text, Source]
    if os.path.isdir(wheel.location):
        try:
            installed_wheel = InstalledWheel.load(wheel.location)
        except LoadError as e:
            raise WheelInstallError(
                "Can only install .whl files and re-install installed wheel directories.\n"
                "The directory {wheel_dir} does not contain a re-installable installed wheel:\n"
                "{err}".format(wheel_dir=wheel.location, err=e)
            )
        data_abspath = (
            os.path.join(install_paths.extract_dir, installed_wheel.data_dir)
            if installed_wheel.data_dir
            else None
        )
        recorder = Recorder(
            base=install_paths.extract_dir, record_relpath=installed_wheel.record_relpath
        )
        if wheel.location != install_paths.extract_dir:
            for src, dst in iter_copytree(
                src=wheel.location,
                dst=install_paths.extract_dir,
                exclude=(installed_wheel.stash_dir, InstalledWheel.LAYOUT_JSON_FILENAME),
                symlink=symlink,
            ):
                source = Source.file(src)
                if data_abspath and data_abspath == commonpath((data_abspath, dst)):
                    data_sources[dst] = source
                else:
                    recorder.record_copy(source, dst)
        # TODO(John Sirois): XXX: Add a comment explaining this re-rip through the src tree.
        if symlink or wheel.location == install_paths.extract_dir:
            for is_dir, src, dst in iter_copytree_entries(
                src=wheel.location,
                dst=install_paths.extract_dir,
                exclude=(installed_wheel.stash_dir, InstalledWheel.LAYOUT_JSON_FILENAME),
            ):
                if is_dir:
                    continue
                source = Source.file(src)
                if data_abspath and data_abspath == commonpath((data_abspath, dst)):
                    data_sources[dst] = source
                else:
                    recorder.record_copy(source, dst)
    else:
        data_rel_path = wheel.data_dir
        recorder = Recorder(
            base=install_paths.extract_dir, record_relpath=wheel.metadata_path("RECORD")
        )
        with open_zip(wheel.location) as zf:
            zf.extractall(install_paths.extract_dir)
            for name in zf.namelist():
                if name.endswith("/"):
                    continue
                source = Source.zip_entry(wheel.location, name)
                dst = os.path.join(install_paths.extract_dir, name)
                if data_rel_path == commonpath((data_rel_path, name)):
                    data_sources[dst] = source
                else:
                    recorder.record_copy(source=source, dest_path=dst)
        data_abspath = os.path.join(install_paths.extract_dir, data_rel_path)

    # 2. Spread
    data_scripts = {}  # type: Dict[Text, Source]
    if data_abspath and os.path.isdir(data_abspath):
        try:
            for entry in sorted(os.listdir(data_abspath)):
                entry_path = os.path.join(data_abspath, entry)
                try:
                    dest_dir = install_paths[entry]
                except KeyError as e:
                    raise WheelInstallError(
                        "The wheel at {wheel_path} is invalid and cannot be installed: "
                        "{err}".format(wheel_path=wheel.location, err=e)
                    )
                script_processor = (
                    ScriptProcessor(hermetic_scripts=hermetic_scripts, target_python=target_python)
                    if "scripts" == entry
                    else None
                )
                # TODO(John Sirois): XXX: Scrutinize short-circuit scheme.
                if entry_path == dest_dir and not script_processor:
                    continue

                scripts = []  # type: List[Tuple[Text, Text]]
                for src, dst in iter_copytree(src=entry_path, dst=dest_dir, symlink=symlink):
                    if script_processor and script_processor.needs_processing(dst):
                        scripts.append((src, dst))
                    else:
                        recorder.record_copy(data_sources[src], dst)
                if script_processor and scripts:
                    script_processor.process(scripts=[dst for _, dst in scripts])
                    for src, dst in scripts:
                        source = data_sources[src]
                        data_scripts[dst] = source
                        recorder.record_copy(source, dst)
        finally:
            if finalize:
                safe_rmtree(data_abspath)

    if compile:
        py_files = list(recorder.iter_python_files())
        if py_files:
            args = [
                target_python or sys.executable,
                "-sE",
                "-m",
                "compileall",
            ]  # type: List[Text]

            process = subprocess.Popen(
                args=args + py_files, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            _, stderr = process.communicate()
            if process.returncode != 0:
                pex_warnings.warn(
                    "Failed to compile some .py files for install of {wheel} to {dest}:\n"
                    "{stderr}".format(
                        wheel=wheel.location,
                        dest=install_paths.extract_dir,
                        stderr=stderr.decode("utf-8"),
                    )
                )
            for pyc_root, _, pyc_files in os.walk(commonpath(py_files)):
                for pyc_file in pyc_files:
                    if pyc_file.endswith(".pyc"):
                        recorder.record_file(os.path.join(pyc_root, pyc_file))

    dist_metadata = wheel.dist_metadata()
    dist = Distribution(location=wheel.location, metadata=dist_metadata)
    entry_points = dist.get_entry_map()
    for entry_point in itertools.chain.from_iterable(
        entry_points.get(key, {}).values() for key in ("console_scripts", "gui_scripts")
    ):
        module, qualname_separator, function = str(entry_point).partition(":")
        if not qualname_separator:
            raise WheelInstallError(
                "The entry point '{name}' defined in wheel {wheel_path} is invalid: {entry_point}\n"
                "It must separate the module name to import from the function name within that "
                "module to execute with a ':'.".format(
                    name=entry_point.name, entry_point=entry_point, wheel_path=wheel.location
                )
            )
        script_dst = os.path.join(install_paths.scripts, entry_point.name)
        script_source = data_scripts.get(script_dst)
        if script_source:
            TRACER.log(
                "The {name} {version} distribution provides script {script} via both {source} and "
                "entry_points.txt. Using {source} instead of generating a console script from "
                "entry_points.txt metadata.".format(
                    name=dist_metadata.project_name,
                    version=dist_metadata.version,
                    script=entry_point.name,
                    source=script_source.display,
                ),
                V=2,
            )
        else:
            with safe_open(script_dst, "w") as fp:
                fp.write(
                    dedent(
                        """\
                        {shebang}
                        # -*- coding: utf-8 -*-
                        import sys
    
                        import {module}
    
                        if __name__ == '__main__':
                            sys.exit({module}.{function}())
                        """
                    ).format(
                        shebang=_create_shebang(
                            target_python=target_python,
                            args=create_script_args(hermetic_scripts=hermetic_scripts),
                        ),
                        module=module,
                        function=function,
                    )
                )
            chmod_plus_x(fp.name)

            # Although the entry_points.txt metadata file handles more than one console script and more
            # than console scripts, it's still a fine proxy for a source since a console script of a
            # given name should only come from 1 distribution.
            entry_points_relpath = wheel.metadata_path("entry_points.txt")
            recorder.record_copy(
                source=(
                    Source.file(os.path.join(wheel.location, entry_points_relpath))
                    if os.path.isdir(wheel.location)
                    else Source.zip_entry(wheel.location, entry_points_relpath)
                ),
                dest_path=fp.name,
            )

    if finalize:
        with safe_open(
            os.path.join(install_paths.extract_dir, wheel.metadata_path("INSTALLER")), "w"
        ) as fp:
            print("pex", file=fp)
        recorder.record_file(fp.name)

        recording = recorder.get_recording()
        recording.record.write(requested=requested)
    else:
        recording = recorder.get_recording()
        safe_delete(os.path.join(install_paths.extract_dir, recording.record.relpath))
    return recording


class InstalledWheelError(Exception):
    pass


class LoadError(InstalledWheelError):
    """Indicates an installed wheel was not loadable at a particular path."""


@attr.s(frozen=True)
class InstalledWheel(object):
    _VERSION = 1
    LAYOUT_JSON_FILENAME = ".layout.json"

    @classmethod
    def layout_file(cls, prefix_dir):
        # type: (str) -> str
        return os.path.join(prefix_dir, cls.LAYOUT_JSON_FILENAME)

    @classmethod
    def save(
        cls,
        prefix_dir,  # type: str
        record_relpath,  # type: str
        data_dir=None,  # type: Optional[str]
    ):
        # type: (...) -> InstalledWheel
        layout_file = cls.layout_file(prefix_dir)
        safe_delete(layout_file)

        # We currently need the installed wheel chroot hash for PEX-INFO / boot purposes. It is
        # expensive to calculate; so we do it here 1 time when saving the installed wheel.
        fingerprint = CacheHelper.dir_hash(prefix_dir, hasher=hashlib.sha256)

        size = dir_size(prefix_dir)
        layout = {
            "version": cls._VERSION,
            "stash_dir": _STASH_DIR,
            "record_relpath": record_relpath,
            "fingerprint": fingerprint,
            "data_dir": data_dir,
            "size": size,
        }
        with open(layout_file, "w") as fp:
            json.dump(layout, fp, sort_keys=True)
        return cls(
            layout_version=cls._VERSION,
            prefix_dir=prefix_dir,
            stash_dir=_STASH_DIR,
            record_relpath=record_relpath,
            fingerprint=fingerprint,
            data_dir=data_dir,
        )

    @classmethod
    def load(cls, prefix_dir):
        # type: (str) -> InstalledWheel
        layout_file = cls.layout_file(prefix_dir)
        try:
            with open(layout_file) as fp:
                layout = json.load(fp)
        except (IOError, OSError) as e:
            raise LoadError(
                "Failed to load an installed wheel layout from {layout_file}: {err}".format(
                    layout_file=layout_file, err=e
                )
            )
        if not isinstance(layout, dict):
            raise LoadError(
                "The installed wheel layout file at {layout_file} must contain a single top-level "
                "object, found: {value}.".format(layout_file=layout_file, value=layout)
            )
        stash_dir = layout.get("stash_dir")
        record_relpath = layout.get("record_relpath")
        if not stash_dir or not record_relpath:
            raise LoadError(
                "The installed wheel layout file at {layout_file} must contain an object with both "
                "`stash_dir` and `record_relpath` attributes, found: {value}".format(
                    layout_file=layout_file, value=layout
                )
            )
        fingerprint = layout.get("fingerprint")
        data_dir = layout.get("data_dir")
        layout_version = layout.get("version", 0)
        size = layout.get("size")
        return cls(
            layout_version=layout_version,
            prefix_dir=prefix_dir,
            stash_dir=cast(str, stash_dir),
            record_relpath=cast(str, record_relpath),
            fingerprint=cast("Optional[str]", fingerprint),
            data_dir=cast("Optional[str]", data_dir),
            size=cast("Optional[int]", size),
        )

    @classmethod
    def maybe_load(cls, prefix_dir):
        # type: (str) -> Optional[InstalledWheel]
        try:
            return cls.load(prefix_dir)
        except LoadError:
            return None

    layout_version = attr.ib()  # type: int
    prefix_dir = attr.ib()  # type: str
    stash_dir = attr.ib()  # type: str
    record_relpath = attr.ib()  # type: str
    fingerprint = attr.ib(default=None)  # type: Optional[str]
    data_dir = attr.ib(default=None)  # type: Optional[str]
    size = attr.ib(default=None)  # type: Optional[int]

    def stashed_path(self, *components):
        # type: (*str) -> str
        return os.path.join(self.prefix_dir, self.stash_dir, *components)

    @property
    def supports_sh_python_redirector_scripts(self):
        # type: () -> bool
        return self.layout_version >= 1

    def script_path(self, name):
        # type: (str) -> Optional[str]
        scripts_dir = installed_scripts_dir(stash_dir=self.stashed_path())
        script = os.path.join(scripts_dir, name)
        return script if is_exe(script) else None

    def iter_scripts(self):
        # type: () -> Iterator[str]
        scripts_dir = installed_scripts_dir(stash_dir=self.stashed_path())
        if os.path.isdir(scripts_dir):
            for script in glob.glob(os.path.join(scripts_dir, "*")):
                if is_exe(script):
                    yield script

    def iter_top_level(self, target_dir):
        # type: (str) -> Iterator[Tuple[str, str]]
        if self.layout_version >= 1:
            for path in os.listdir(self.prefix_dir):
                if path != self.stash_dir:
                    yield os.path.join(self.prefix_dir, path), os.path.join(target_dir, path)
        else:
            yield self.prefix_dir, target_dir

    def iter_files(self, target_dir):
        # type: (str) -> Iterator[Tuple[str, str]]
        for root, dirs, files in os.walk(self.prefix_dir):
            if root == self.prefix_dir and self.layout_version >= 1:
                dirs[:] = [d for d in dirs if d != self.stash_dir]

            for f in files:
                abs_path = os.path.join(root, f)
                rel_path = os.path.relpath(abs_path, self.prefix_dir)
                yield abs_path, os.path.join(target_dir, rel_path)

    def reinstall_flat(
        self,
        target_dir,  # type: str
        target_python=None,  # type: Optional[str]
        symlink=False,  # type: bool
    ):
        # type: (...) -> Iterator[Tuple[Source, Text]]
        """Re-installs the installed wheel in a flat target directory.

        N.B.: A record of reinstalled files is returned in the form of an iterator that must be
        consumed to drive the installation to completion.

        If there is an error re-installing a file due to it already existing in the target
        directory, the error is suppressed, and it's expected that the caller detects this by
        comparing the record of installed files against those installed previously.

        :return: An iterator over src -> dst pairs.
        """
        if self.layout_version >= 1:
            recording = install_wheel_flat(
                wheel_path=self.prefix_dir,
                destination=os.path.abspath(target_dir),
                symlink=symlink,
                target_python=target_python,
                compile=False,
            )
            for source, dst in recording.copies:
                yield source, dst
        else:
            recorder = Recorder(base=target_dir, record_relpath=self.record_relpath)
            for src, dst in itertools.chain(
                self._reinstall_stash(dest_dir=target_dir),
                self._reinstall_site_packages(target_dir, symlink=symlink),
            ):
                recorder.record_file(dst)
                yield Source.file(src), dst
            recorder.get_recording().record.write()

    def reinstall_venv(
        self,
        venv,  # type: Virtualenv
        target_venv_python=None,  # type: Optional[str]
        hermetic_scripts=True,  # type: bool
        symlink=False,  # type: bool
        rel_extra_path=None,  # type: Optional[str]
    ):
        # type: (...) -> Iterator[Tuple[Source, Text]]
        """Re-installs the installed wheel in a venv.

        N.B.: A record of reinstalled files is returned in the form of an iterator that must be
        consumed to drive the installation to completion.

        If there is an error re-installing a file due to it already existing in the destination
        venv, the error is suppressed, and it's expected that the caller detects this by comparing
        the record of installed files against those installed previously.

        :return: An iterator over src -> dst pairs.
        """
        if self.layout_version >= 1:
            recording = install_wheel_interpreter(
                wheel_path=self.prefix_dir,
                interpreter=venv.interpreter,
                target_python=target_venv_python,
                symlink=symlink,
                rel_extra_path=rel_extra_path,
                hermetic_scripts=hermetic_scripts,
                compile=False,
            )
            for source, dst in recording.copies:
                yield source, dst
        else:
            site_packages_dir = (
                os.path.join(venv.site_packages_dir, rel_extra_path)
                if rel_extra_path
                else venv.site_packages_dir
            )

            recorder = Recorder(base=site_packages_dir, record_relpath=self.record_relpath)
            for src, dst in itertools.chain(
                self._reinstall_stash(dest_dir=venv.venv_dir, interpreter=venv.interpreter),
                self._reinstall_site_packages(site_packages_dir, symlink=symlink),
            ):
                recorder.record_file(dst)
                yield Source.file(src), dst
            recorder.get_recording().record.write()

    def _reinstall_stash(
        self,
        dest_dir,  # type: str
        interpreter=None,  # type: Optional[PythonInterpreter]
    ):
        # type: (...) -> Iterator[Tuple[Text, Text]]

        link = True
        stash_abs_path = os.path.join(self.prefix_dir, self.stash_dir)
        for root, dirs, files in os.walk(stash_abs_path, topdown=True, followlinks=True):
            dir_created = False
            for f in files:
                src = os.path.join(root, f)
                src_relpath = os.path.relpath(src, stash_abs_path)
                dst = InstalledFile.denormalized_path(
                    path=os.path.join(dest_dir, src_relpath), interpreter=interpreter
                )
                if not dir_created:
                    safe_mkdir(os.path.dirname(dst))
                    dir_created = True
                try:
                    # We only try to link regular files since linking a symlink on Linux can produce
                    # another symlink, which leaves open the possibility the src target could later
                    # go missing leaving the dst dangling.
                    if link and not os.path.islink(src):
                        try:
                            os.link(src, dst)
                            continue
                        except OSError as e:
                            if e.errno != errno.EXDEV:
                                raise e
                            link = False
                    shutil.copy(src, dst)
                except (IOError, OSError) as e:
                    if e.errno != errno.EEXIST:
                        raise e
                finally:
                    yield src, dst

    def _reinstall_site_packages(
        self,
        site_packages_dir,  # type: str
        symlink=False,  # type: bool
    ):
        # type: (...) -> Iterator[Tuple[Text, Text]]

        link = True
        for root, dirs, files in os.walk(self.prefix_dir, topdown=True, followlinks=True):
            if root == self.prefix_dir:
                dirs[:] = [d for d in dirs if not is_pyc_dir(d) and d != self.stash_dir]
                files[:] = [
                    f for f in files if not is_pyc_file(f) and f != self.LAYOUT_JSON_FILENAME
                ]

            traverse = set(dirs)
            for path, is_dir in itertools.chain(
                zip(dirs, itertools.repeat(True)), zip(files, itertools.repeat(False))
            ):
                src_entry = os.path.join(root, path)
                dst_entry = os.path.join(
                    site_packages_dir, os.path.relpath(src_entry, self.prefix_dir)
                )
                try:
                    if symlink and not (
                        src_entry.endswith(".dist-info") and os.path.isdir(src_entry)
                    ):
                        dst_parent = os.path.dirname(dst_entry)
                        safe_mkdir(dst_parent)
                        rel_src = os.path.relpath(src_entry, dst_parent)
                        os.symlink(rel_src, dst_entry)
                        traverse.discard(path)
                    elif is_dir:
                        safe_mkdir(dst_entry)
                    else:
                        # We only try to link regular files since linking a symlink on Linux can
                        # produce another symlink, which leaves open the possibility the src_entry
                        # target could later go missing leaving the dst_entry dangling.
                        if link and not os.path.islink(src_entry):
                            try:
                                os.link(src_entry, dst_entry)
                                continue
                            except OSError as e:
                                if e.errno != errno.EXDEV:
                                    raise e
                                link = False
                        shutil.copy(src_entry, dst_entry)
                except (IOError, OSError) as e:
                    if e.errno != errno.EEXIST:
                        raise e
                finally:
                    if not is_dir:
                        yield src_entry, dst_entry

            dirs[:] = list(traverse)
