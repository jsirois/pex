# Copyright 2024 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import

from collections import defaultdict
from contextlib import contextmanager
from hashlib import sha1

from pex import hashing, pex_warnings
from pex.common import open_zip, pluralize
from pex.typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import (
        IO,
        Callable,
        ContextManager,
        DefaultDict,
        Iterable,
        Iterator,
        List,
        Optional,
        Text,
        Tuple,
    )

    import attr  # vendor:skip

    from pex.hashing import Hasher
else:
    from pex.third_party import attr


class CollisionError(Exception):
    """Indicates multiple distributions provided the same file when merging a PEX into a venv."""


@attr.s(frozen=True)
class Source(object):
    @classmethod
    def file(cls, path):
        # type: (Text) -> Source
        return cls(display=path, open=lambda: open(path, "rb"))

    @classmethod
    def zip_entry(
        cls,
        zip_path,  # type: Text
        entry_name,  # type: Text
    ):
        # type: (...) -> Source

        @contextmanager
        def open_entry():
            # type: () -> Iterator[IO[bytes]]
            with open_zip(zip_path) as zf:
                with zf.open(entry_name) as fp:
                    yield fp

        return cls(display="{zip}:{entry}".format(zip=zip_path, entry=entry_name), open=open_entry)

    display = attr.ib()  # type: Text
    _open = attr.ib(eq=False, repr=False)  # type: Callable[[], ContextManager[IO[bytes]]]

    def fingerprint(self, hasher=sha1):
        # type: (Callable[[], Hasher]) -> str
        digest = hasher()
        with self._open() as fp:
            hashing.update_hash(fp, digest)
        return digest.hexdigest()


@attr.s
class Provenance(object):
    _target_dir = attr.ib()  # type: str
    _provenance = attr.ib(init=False)  # type: DefaultDict[Text, List[Source]]

    def __attrs_post_init__(self):
        object.__setattr__(self, "_provenance", defaultdict(list))

    def record(self, src_to_dst):
        # type: (Iterable[Tuple[Source, Text]]) -> None
        for src, dst in src_to_dst:
            self._provenance[dst].append(src)

    def check_collisions(
        self,
        collisions_ok=False,  # type: bool
        source=None,  # type: Optional[str]
    ):
        # type: (...) -> None

        potential_collisions = {
            dst: srcs for dst, srcs in self._provenance.items() if len(srcs) > 1
        }
        if not potential_collisions:
            return

        collisions = {}
        for dst, srcs in potential_collisions.items():
            contents = defaultdict(list)  # type: DefaultDict[str, List[Text]]
            for src in srcs:
                contents[src.fingerprint()].append(src.display)
            if len(contents) > 1:
                collisions[dst] = contents

        if not collisions:
            return

        message_lines = [
            "Encountered {collision} populating {target_dir}{source}:".format(
                collision=pluralize(collisions, "collision"),
                target_dir=self._target_dir,
                source=" from {source}".format(source=source) if source else "",
            )
        ]
        for index, (dst, contents) in enumerate(collisions.items(), start=1):
            message_lines.append(
                "{index}. {dst} was provided by:\n\t{srcs}".format(
                    index=index,
                    dst=dst,
                    srcs="\n\t".join(
                        "sha1:{fingerprint} -> {srcs}".format(
                            fingerprint=fingerprint, srcs=", ".join(srcs)
                        )
                        for fingerprint, srcs in contents.items()
                    ),
                )
            )
        message = "\n".join(message_lines)
        if not collisions_ok:
            raise CollisionError(message)
        pex_warnings.warn(message)
