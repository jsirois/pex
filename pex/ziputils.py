# Copyright 2022 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import

import io
import os
import shutil
import struct
from typing import Tuple

from pex.typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import BinaryIO, Optional

    import attr  # vendor:skip
else:
    from pex.third_party import attr


@attr.s(frozen=True)
class _FileHeaderRecord(object):
    _STRUCT = struct.Struct("<4sHHHHHHLLLHHHHHLL")

    _SIGNATURE = b"\x50\x4b\x01\x02"

    @classmethod
    def load(cls, fp):
        # type: (BinaryIO) -> Optional[_FileHeaderRecord]
        maybe_sig = fp.read(len(cls._SIGNATURE))
        if cls._SIGNATURE != maybe_sig:
            return None

        fp.seek(-len(cls._SIGNATURE), os.SEEK_CUR)
        file_header_record = cls(*cls._STRUCT.unpack(fp.read(cls._STRUCT.size)))
        return attr.evolve(
            file_header_record,
            file_name=fp.read(file_header_record.file_name_length),
            extra_field=fp.read(file_header_record.extra_field_length),
            file_comment=fp.read(file_header_record.file_comment_length),
        )

    # See: https://pkware.cachefly.net/webdocs/casestudies/APPNOTE.TXT
    # 4.3.12  Central directory structure:
    #
    #    [central directory header 1]
    #    .
    #    .
    #    .
    #    [central directory header n]
    #    [digital signature]
    #
    #    File header:
    #
    #         central file header signature   4 bytes  (0x02014b50)
    #         version made by                 2 bytes
    #         version needed to extract       2 bytes
    #         general purpose bit flag        2 bytes
    #         compression method              2 bytes
    #         last mod file time              2 bytes
    #         last mod file date              2 bytes
    #         crc-32                          4 bytes
    #         compressed size                 4 bytes
    #         uncompressed size               4 bytes
    #         file name length                2 bytes
    #         extra field length              2 bytes
    #         file comment length             2 bytes
    #         disk number start               2 bytes
    #         internal file attributes        2 bytes
    #         external file attributes        4 bytes
    #         relative offset of local header 4 bytes
    #
    #         file name (variable size)
    #         extra field (variable size)
    #         file comment (variable size)

    sig = attr.ib()  # type: bytes
    version_made_by = attr.ib()  # type: int
    version_needed_to_extract = attr.ib()  # type: int
    general_purpose_bit_flag = attr.ib()  # type: int
    compression_method = attr.ib()  # type: int
    last_mod_file_time = attr.ib()  # type: int
    last_mod_file_date = attr.ib()  # type: int
    crc32 = attr.ib()  # type: int
    compressed_size = attr.ib()  # type: int
    uncompressed_size = attr.ib()  # type: int
    file_name_length = attr.ib()  # type: int
    extra_field_length = attr.ib()  # type: int
    file_comment_length = attr.ib()  # type: int
    disk_number_start = attr.ib()  # type: int
    internal_file_attributes = attr.ib()  # type: int
    external_file_attributes = attr.ib()  # type: int
    relative_offset_of_local_header = attr.ib()  # type: int

    file_name = attr.ib(default=b"")  # type: bytes
    extra_field = attr.ib(default=b"")  # type: bytes
    file_comment = attr.ib(default=b"")  # type: bytes


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
                eocd_record = cls(cls._STRUCT.size, *cls._STRUCT.unpack(fp.read()))
            else:
                # There must be an EOCD comment, rewind to allow for the biggest possible comment (
                # which is not that big at all).
                read_size = min(cls._MAX_SIZE, file_size)
                fp.seek(-read_size, os.SEEK_END)
                last_data_chunk = fp.read()
                start_eocd = last_data_chunk.find(cls._SIGNATURE)
                _struct = cls._STRUCT.unpack_from(last_data_chunk, start_eocd)
                zip_comment = last_data_chunk[start_eocd + cls._STRUCT.size :]
                eocd_record = cls(len(last_data_chunk) - start_eocd, *(_struct + (zip_comment,)))

            fp.seek(-(eocd_record.cd_size + eocd_record.size), os.SEEK_END)
            file_header_records = []
            while True:
                file_header_record = _FileHeaderRecord.load(fp)
                if file_header_record is None:
                    break
                file_header_records.append(file_header_record)
            return attr.evolve(eocd_record, file_header_records=tuple(file_header_records))

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
    file_header_records = attr.ib(default=())  # type: Tuple[_FileHeaderRecord, ...]


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
        start_of_zip_offset_from_eof = (
            self._end_of_central_directory_record.size
            + self._end_of_central_directory_record.cd_size
            + self._end_of_central_directory_record.cd_offset
        )
        return max(
            (os.path.getsize(self.path) - start_of_zip_offset_from_eof),
            min(
                fhr.relative_offset_of_local_header
                for fhr in self._end_of_central_directory_record.file_header_records
            )
            if self._end_of_central_directory_record.file_header_records
            else 0,
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
                    in_fp.seek(-min(remaining, io.DEFAULT_BUFFER_SIZE), os.SEEK_CUR)
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
