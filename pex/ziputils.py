# Copyright 2022 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import, print_function

import io
import os
import shutil
import struct
from typing import BinaryIO, Optional

from pex.typing import TYPE_CHECKING

if TYPE_CHECKING:
    import attr  # vendor:skip
else:
    from pex.third_party import attr


@attr.s(frozen=True)
class _EndOfCentralDirectoryRecord(object):
    _STRUCT = struct.Struct("<4sHHHHLLH")

    _SIGNATURE = b"\x50\x4b\x05\x06"
    _MAX_SIZE = _STRUCT.size + (
        # The comment field is of variable length but that length is capped at a 2 byte integer.
        2
        ^ 16
    )

    @classmethod
    def load(cls, zip_path):
        # type: (str) -> _EndOfCentralDirectoryRecord
        file_size = os.path.getsize(zip_path)
        if file_size < cls._STRUCT.size:
            raise ValueError(
                "The file at {path} is too small to be a valid Zip file.".format(path=zip_path)
            )

        with open(zip_path, "rb") as fp:
            # Try for the common case of no EOCD comment 1st.
            fp.seek(-cls._STRUCT.size, os.SEEK_END)
            if cls._SIGNATURE == fp.read(len(cls._SIGNATURE)):
                fp.seek(-len(cls._SIGNATURE), os.SEEK_CUR)
                return cls(cls._STRUCT.size, *cls._STRUCT.unpack(fp.read()))

            # There must be an EOCD comment, rewind to allow for the biggest possible comment (
            # which is not that big at all).
            read_size = min(cls._MAX_SIZE, file_size)
            fp.seek(-read_size, os.SEEK_END)
            last_data_chunk = fp.read()
            start_eocd = last_data_chunk.find(cls._SIGNATURE)
            _struct = cls._STRUCT.unpack_from(last_data_chunk, start_eocd)
            comment = last_data_chunk[start_eocd + cls._STRUCT.size :]
            return cls(len(last_data_chunk) - start_eocd, *(_struct + (comment,)))

    _offset = attr.ib()  # type: int

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
    comment_size = attr.ib()  # type: int
    comment = attr.ib(default=b"")  # type: bytes

    @property
    def start_of_zip_offset_from_eof(self):
        # type: () -> int
        return self._offset + self.cd_offset + self.cd_size


@attr.s(frozen=True)
class Zip(object):
    @classmethod
    def load(cls, path):
        # type: (str) -> Zip
        return cls(
            end_of_central_directory_record=_EndOfCentralDirectoryRecord.load(path), path=path
        )

    _end_of_central_directory_record = attr.ib()  # type: _EndOfCentralDirectoryRecord
    path = attr.ib()  # type: str
    header_size = attr.ib(init=False)  # type: int

    @header_size.default
    def _header_size(self):
        return (
            os.path.getsize(self.path)
            - self._end_of_central_directory_record.start_of_zip_offset_from_eof
        )

    @property
    def has_header(self):
        # type: () -> bool
        return self.header_size > 0

    def isolate_header(
        self,
        out_fp,  # type: BinaryIO
        stop_at=None,  # type: Optional[bytes]
    ):
        # type: (...) -> bytes
        if not self.has_header:
            return b""

        remaining = self.header_size
        with open(self.path, "rb") as in_fp:
            if stop_at:
                in_fp.seek(self.header_size, os.SEEK_SET)
                while remaining > 0:
                    in_fp.seek(-io.DEFAULT_BUFFER_SIZE, os.SEEK_CUR)
                    chunk = in_fp.read(min(remaining, io.DEFAULT_BUFFER_SIZE))
                    offset = chunk.rfind(stop_at)
                    remaining -= len(chunk)
                    if offset != -1:
                        remaining += offset
                        break

            excess = self.header_size - remaining
            in_fp.seek(0, os.SEEK_SET)
            for chunk in iter(lambda: in_fp.read(min(io.DEFAULT_BUFFER_SIZE, remaining)), b""):
                remaining -= len(chunk)
                out_fp.write(chunk)

            return in_fp.read(excess)

    def isolate_zip(self, out_fp):
        # type: (BinaryIO) -> None
        if not self.has_header:
            return

        with open(self.path, "rb") as in_fp:
            in_fp.seek(self.header_size, os.SEEK_SET)
            shutil.copyfileobj(in_fp, out_fp)
