# Copyright 2015 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

import codecs
import contextlib
import functools
import os
import tarfile
import zipfile

from .common import safe_mkdir, safe_mkdtemp
from .compatibility import to_bytes


class Archiver(object):
  class Error(Exception): pass
  class UnpackError(Error): pass
  class InvalidArchive(Error): pass

  _TAR_EXTRACTOR = (functools.partial(tarfile.open, encoding='utf-8'), tarfile.ReadError)

  class ZipExtractor(object):
    """Allows extraction of zip files with UTF-8 file names.

    Some unix systems (Linux) may have non UTF-8 default character encodings in which case using
    `ZipFile.extractall` directly can fail.

    See:
      https://github.com/pantsbuild/pex/issues/298
      https://github.com/pantsbuild/pants/issues/3823
    """

    _UTF_8 = codecs.lookup('utf-8')

    def __init__(self, filename):
      self._zipfile = zipfile.ZipFile(filename)

    def extractall(self, path=None):
      extract_dir = to_bytes(os.path.realpath(path or os.curdir))
      for info in self._zipfile.infolist():
        filename, _ = self._UTF_8.encode(info.filename)
        if os.path.isabs(filename):
          raise Archiver.UnpackError(
              'Refusing to unpack archive member with absolute path: {}'.format(filename))
        if filename.endswith(b'/'):
          abs_dir = os.path.join(extract_dir, filename)
          safe_mkdir(abs_dir)
        else:
          rel_dir = os.path.dirname(filename)
          abs_dir = os.path.join(extract_dir, rel_dir)
          safe_mkdir(abs_dir)

          abs_path = os.path.join(abs_dir, os.path.basename(filename))
          with open(abs_path, 'wb') as fp:
            fp.write(self._zipfile.read(info))

    def close(self):
      self._zipfile.close()

  EXTENSIONS = {
    '.tar': _TAR_EXTRACTOR,
    '.tar.gz': _TAR_EXTRACTOR,
    '.tar.bz2': _TAR_EXTRACTOR,
    '.tgz': _TAR_EXTRACTOR,
    '.zip': (ZipExtractor, zipfile.BadZipfile)
  }

  @classmethod
  def first_nontrivial_dir(cls, path):
    files = os.listdir(path)
    if len(files) == 1 and os.path.isdir(os.path.join(path, files[0])):
      return cls.first_nontrivial_dir(os.path.join(path, files[0]))
    else:
      return path

  @classmethod
  def get_extension(cls, filename):
    for ext in cls.EXTENSIONS:
      if filename.endswith(ext):
        return ext

  @classmethod
  def unpack(cls, filename, location=None):
    path = location or safe_mkdtemp()
    ext = cls.get_extension(filename)
    if ext is None:
      raise cls.InvalidArchive('Unknown archive format: %s' % filename)
    archive_class, error_class = cls.EXTENSIONS[ext]
    try:
      with contextlib.closing(archive_class(filename)) as package:
        package.extractall(path=path)
    except error_class:
      raise cls.UnpackError('Could not extract %s' % filename)
    return cls.first_nontrivial_dir(path)
