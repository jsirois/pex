# Copyright 2022 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import

import io
import os
import shutil
import struct

from pex.typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import BinaryIO, Optional

    import attr  # vendor:skip
else:
    from pex.third_party import attr


class ZipError(Exception):
    """Indicates a problem reading a zip file."""


@attr.s(frozen=True)
class _Zip64EndOfCentralDirectory(object):
    _SIGNATURE = b"PK\x06\x06"
    _STRUCT = struct.Struct("<4sQHHLLQQQQ")

    @classmethod
    def load(cls, fp):
        # type: (BinaryIO) -> _Zip64EndOfCentralDirectory
        if cls._SIGNATURE != fp.read(len(cls._SIGNATURE)):
            raise ZipError(
                "The zip at {path} was expected to have a Zip64 end of central directory record "
                "but does not.".format(path=fp.name)
            )

        fp.seek(-len(cls._SIGNATURE), os.SEEK_CUR)
        return cls(*cls._STRUCT.unpack(fp.read(cls._STRUCT.size)))

    # See: https://pkware.cachefly.net/webdocs/casestudies/APPNOTE.TXT
    # 4.3.14  Zip64 end of central directory record
    #
    #         zip64 end of central dir
    #         signature                       4 bytes  (0x06064b50)
    #         size of zip64 end of central
    #         directory record                8 bytes
    #         version made by                 2 bytes
    #         version needed to extract       2 bytes
    #         number of this disk             4 bytes
    #         number of the disk with the
    #         start of the central directory  4 bytes
    #         total number of entries in the
    #         central directory on this disk  8 bytes
    #         total number of entries in the
    #         central directory               8 bytes
    #         size of the central directory   8 bytes
    #         offset of start of central
    #         directory with respect to
    #         the starting disk number        8 bytes
    #         zip64 extensible data sector    (variable size)

    sig = attr.ib()  # type: bytes
    _size = attr.ib()  # type: int
    version_made_by = attr.ib()  # type: int
    version_needed_to_extract = attr.ib()  # type: int
    disk_no = attr.ib()  # type: int
    cd_disk_no = attr.ib()  # type: int
    disk_cd_record_count = attr.ib()  # type: int
    total_cd_record_count = attr.ib()  # type: int
    cd_size = attr.ib()  # type: int
    cd_offset = attr.ib()  # type: int

    @property
    def size(self):
        # type: () -> int

        # See: https://pkware.cachefly.net/webdocs/casestudies/APPNOTE.TXT
        # 4.3.14.1 The value stored into the "size of zip64 end of central
        #       directory record" SHOULD be the size of the remaining
        #       record and SHOULD NOT include the leading 12 bytes.
        return 12 + self._size


@attr.s(frozen=True)
class _Zip64EndOfCentralDirectoryLocator(object):
    _SIGNATURE = b"PK\x06\x07"
    _STRUCT = struct.Struct("<4sLQL")

    @classmethod
    def load(cls, fp):
        # type: (BinaryIO) -> _Zip64EndOfCentralDirectoryLocator
        if cls._SIGNATURE != fp.read(len(cls._SIGNATURE)):
            raise ZipError(
                "The zip at {path} does was expected to have a Zip64 end of central directory "
                "locator record but does not.".format(path=fp.name)
            )

        fp.seek(-len(cls._SIGNATURE), os.SEEK_CUR)
        _struct = cls._STRUCT.unpack(fp.read(cls._STRUCT.size))

        zip64_eocd_offset = _struct[2]
        fp.seek(zip64_eocd_offset, os.SEEK_SET)
        zip64_eocd = _Zip64EndOfCentralDirectory.load(fp)

        return cls(*(_struct + (zip64_eocd,)))

    size = _STRUCT.size

    # See: https://pkware.cachefly.net/webdocs/casestudies/APPNOTE.TXT
    # 4.3.15 Zip64 end of central directory locator
    #
    #       zip64 end of central dir locator
    #       signature                       4 bytes  (0x07064b50)
    #       number of the disk with the
    #       start of the zip64 end of
    #       central directory               4 bytes
    #       relative offset of the zip64
    #       end of central directory record 8 bytes
    #       total number of disks           4 bytes

    sig = attr.ib()  # type: bytes
    zip64_eocd_disk_no = attr.ib()  # type: int
    zip64_eocd_offset = attr.ib()  # type: int
    disk_count = attr.ib()  # type: int
    zip64_eocd = attr.ib()  # type: _Zip64EndOfCentralDirectory


@attr.s(frozen=True)
class _EndOfCentralDirectoryRecord(object):
    _SIGNATURE = b"PK\x05\x06"
    _STRUCT = struct.Struct("<4sHHHHLLH")

    _MAX_SIZE = _STRUCT.size + (
        # The comment field is of variable length but that length is capped at a 2 byte integer.
        0xFFFF
    )

    @classmethod
    def load(cls, zip_path):
        # type: (str) -> _EndOfCentralDirectoryRecord
        file_size = os.path.getsize(zip_path)
        if file_size < cls._STRUCT.size:
            raise ZipError(
                "The file at {path} is too small to be a valid Zip file.".format(path=zip_path)
            )

        with open(zip_path, "rb") as fp:
            # Try for the common case of no EOCD comment 1st.
            fp.seek(-cls._STRUCT.size, os.SEEK_END)
            if cls._SIGNATURE == fp.read(len(cls._SIGNATURE)):
                fp.seek(-len(cls._SIGNATURE), os.SEEK_CUR)
                eocd = cls(cls._STRUCT.size, *cls._STRUCT.unpack(fp.read()))
            else:
                # There must be an EOCD comment, rewind to allow for the biggest possible comment (
                # which is not that big at all).
                read_size = min(cls._MAX_SIZE, file_size)
                fp.seek(-read_size, os.SEEK_END)
                last_data_chunk = fp.read()
                start_eocd = last_data_chunk.find(cls._SIGNATURE)
                if -1 == start_eocd:
                    raise ZipError(
                        "The file at {path} does not have a Zip end of central directory "
                        "record.".format(path=zip_path)
                    )
                _struct = cls._STRUCT.unpack_from(last_data_chunk, start_eocd)
                zip_comment = last_data_chunk[start_eocd + cls._STRUCT.size :]
                eocd_size = len(last_data_chunk) - start_eocd
                eocd = cls(eocd_size, *(_struct + (zip_comment,)))

            # See: https://pkware.cachefly.net/webdocs/casestudies/APPNOTE.TXT
            #
            # 4.4.1.4  If one of the fields in the end of central directory
            #       record is too small to hold required data, the field SHOULD be
            #       set to -1 (0xFFFF or 0xFFFFFFFF) and the ZIP64 format record
            #       SHOULD be created.
            if 0xFFFF in (
                eocd.disk_no,
                eocd.cd_disk_no,
                eocd.disk_cd_record_count,
                eocd.total_cd_record_count,
            ) or 0xFFFFFFFF in (eocd.cd_size, eocd.cd_offset):
                if file_size < (eocd.size + _Zip64EndOfCentralDirectoryLocator._STRUCT.size):
                    raise ZipError(
                        "The file at {path} is too small to be a valid Zip64 file.".format(
                            path=zip_path
                        )
                    )
                fp.seek(-(eocd.size + _Zip64EndOfCentralDirectoryLocator._STRUCT.size), os.SEEK_END)
                zip64_eocd_locator = _Zip64EndOfCentralDirectoryLocator.load(fp)
                eocd = attr.evolve(eocd, zip64_eocd_locator=zip64_eocd_locator)

            return eocd

    size = attr.ib()  # type: int

    # See: https://pkware.cachefly.net/webdocs/casestudies/APPNOTE.TXT
    # 4.3.16  End of central directory record:
    #
    #       end of central dir signature    4 bytes  (0x06054b50)
    #       number of this disk             2 bytes
    #       number of the disk with the
    #       start of the central directory  2 bytes
    #       total number of entries in the
    #       central directory on this disk  2 bytes
    #       total number of entries in
    #       the central directory           2 bytes
    #       size of the central directory   4 bytes
    #       offset of start of central
    #       directory with respect to
    #       the starting disk number        4 bytes
    #       .ZIP file comment length        2 bytes
    #       .ZIP file comment       (variable size)

    sig = attr.ib()  # type: bytes
    disk_no = attr.ib()  # type: int
    cd_disk_no = attr.ib()  # type: int
    disk_cd_record_count = attr.ib()  # type: int
    total_cd_record_count = attr.ib()  # type: int
    cd_size = attr.ib()  # type: int
    cd_offset = attr.ib()  # type: int
    zip_comment_size = attr.ib()  # type: int
    zip_comment = attr.ib(default=b"")  # type: bytes
    zip64_eocd_locator = attr.ib(default=None)  # type: Optional[_Zip64EndOfCentralDirectoryLocator]

    @property
    def start_of_zip_offset_from_eof(self):
        # type: () -> int

        # See: https://pkware.cachefly.net/webdocs/casestudies/APPNOTE.TXT
        # 4.3.6 Overall .ZIP file format:
        #       ...
        #       [central directory header 1]
        #       .
        #       .
        #       .
        #       [central directory header n]
        #       [zip64 end of central directory record]
        #       [zip64 end of central directory locator]
        #       [end of central directory record]
        if self.zip64_eocd_locator:
            return (
                self.size
                + self.zip64_eocd_locator.size
                + self.zip64_eocd_locator.zip64_eocd_offset
                + self.zip64_eocd_locator.zip64_eocd.size
            )
        return self.size + self.cd_size + self.cd_offset


@attr.s(frozen=True)
class Zip(object):
    """Allows interacting with a Zip that may have arbitrary header content.

    Since the zip format is defined relative to the end of a file, a zip file can have arbitrary
    content pre-pended to it and not affect the validity of the zip archive. This class allows
    identifying if a Zip has arbitrary header content and then isolating that content from the zip
    archive.

    N.B.: Zips that need Zip64 extensions are not supported yet.
    """

    @classmethod
    def load(cls, path):
        # type: (str) -> Zip
        """Loads a zip file with detection of header presence.

        :raises: :class:`ZipError` if the zip could not be analyzed for the presence of a header.
        """
        eocd = _EndOfCentralDirectoryRecord.load(path)
        header_size = os.path.getsize(path) - eocd.start_of_zip_offset_from_eof
        return cls(path=path, is_zip64=eocd.zip64_eocd_locator is not None, header_size=header_size)

    path = attr.ib()  # type: str
    is_zip64 = attr.ib()  # type: bool
    header_size = attr.ib(validator=attr.validators.ge(0))  # type: int

    @property
    def has_header(self):
        # type: () -> bool
        """Returns `True` if this zip has arbitrary header content."""
        return self.header_size > 0

    def isolate_header(
        self,
        out_fp,  # type: BinaryIO
        stop_at=None,  # type: Optional[bytes]
    ):
        # type: (...) -> bytes
        """Writes any non-zip header content to the given output stream.

        If `stop_at` is specified, all the header content up to the right-most (last) occurrence of
        the `stop_at` byte pattern is encountered. If the `stop_at` byte pattern is found, it and
        all the content after it and up until the start of the zip archive is returned.
        """

        if not self.has_header:
            return b""

        remaining = self.header_size
        with open(self.path, "rb") as in_fp:
            if stop_at:
                # Assume the `stop_at` pattern is closer to the end of the header content and search
                # backwards from there to be more efficient. This supports the pattern of
                # sandwiching "small" content between a head-based format (like Microsoft's PE
                # format, Apple's Mach-O format, ELF and even PNG) and a tail-based format like zip.
                #
                # In practice, Windows console scripts are implemented as a single file with a PE
                # loader executable head sandwiching a shebang line between it and a zip archive
                # trailer. The loader uses knowledge of its own format and the zip format to find
                # the sandwiched shebang line and then interpret it to find a suitable Python and
                # then execute that Python interpreter against the file which Python sees as a zip
                # with an embedded `__main__.py` entry point.
                in_fp.seek(self.header_size, os.SEEK_SET)
                while remaining > 0:
                    chunk_size = min(remaining, io.DEFAULT_BUFFER_SIZE)
                    in_fp.seek(-chunk_size, os.SEEK_CUR)
                    chunk = in_fp.read(chunk_size)
                    remaining -= len(chunk)

                    offset = chunk.rfind(stop_at)
                    if offset != -1:
                        remaining += offset
                        break

            excess = self.header_size - remaining
            in_fp.seek(0, os.SEEK_SET)
            for chunk in iter(lambda: in_fp.read(min(remaining, io.DEFAULT_BUFFER_SIZE)), b""):
                remaining -= len(chunk)
                out_fp.write(chunk)

            return in_fp.read(excess)

    def isolate_zip(self, out_fp):
        # type: (BinaryIO) -> None
        """Writes the pure zip archive portion of this zip file to the given output stream."""
        with open(self.path, "rb") as in_fp:
            if self.has_header:
                in_fp.seek(self.header_size, os.SEEK_SET)
            shutil.copyfileobj(in_fp, out_fp)
