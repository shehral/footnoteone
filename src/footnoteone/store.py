"""Append-only JSONL records and content-addressed raw responses under `.footnote/`."""

from __future__ import annotations

import hashlib
import json
import mmap
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from footnoteone.schema import canonical_json

M = TypeVar("M", bound=BaseModel)


def _is_torn(line: bytes) -> bool:
    """The one definition of a torn write, shared by reader and writer: unterminated AND not valid JSON.

    Only the last line of a file can lack its newline. An unterminated line that is valid JSON is a complete
    record that only misses its newline, so it is kept.
    """
    if line.endswith(b"\n"):
        return False
    try:
        json.loads(line)
    except ValueError:  # JSONDecodeError, or UnicodeDecodeError from a split multibyte character
        return True
    return False


def _heal_tail(path: Path) -> None:
    """Make a non-empty file end with a newline: cut a torn last line, or terminate a complete one."""
    if not path.exists():
        return
    with path.open("r+b") as fh:
        size = fh.seek(0, os.SEEK_END)
        if size == 0:
            return
        fh.seek(size - 1)
        if fh.read(1) == b"\n":
            return
        with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as view:
            start = view.rfind(b"\n") + 1  # 0 when the file holds no newline at all
            tail = view[start:]
        if _is_torn(tail):
            fh.truncate(start)
        else:
            fh.seek(0, os.SEEK_END)
            fh.write(b"\n")


class JsonlStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> Path:
        return self.root / f"{name}.jsonl"

    def append(self, name: str, record: BaseModel) -> None:
        """Append one record as a JSON line, first healing the tail so a torn write cannot swallow it.

        Assumes a single writer per store, which is the v0.1 contract.
        """
        path = self.path(name)
        _heal_tail(path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(record.model_dump_json() + "\n")

    def iter(self, name: str, model_cls: type[M]) -> Iterator[M]:
        """Yield the records in file order, skipping blank lines.

        A torn last line (see `_is_torn`) is skipped. Any other bad line, wherever it is, raises ValueError
        naming the file and its 1-based line number.
        """
        path = self.path(name)
        if not path.exists():
            return
        # Binary mode: a torn write can split a multibyte UTF-8 character, which a text-mode read would
        # reject with UnicodeDecodeError before the line could be judged.
        with path.open("rb") as fh:
            for lineno, raw in enumerate(fh, start=1):
                line = raw.strip()
                if not line or _is_torn(raw):
                    continue
                try:
                    record = model_cls.model_validate_json(line)
                except ValidationError as exc:
                    raise ValueError(f"{path}:{lineno}: invalid record: {exc}") from exc
                yield record


class RawStore:
    def __init__(self, root: Path) -> None:
        self.dir = Path(root) / "raw"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, sha: str) -> Path:
        return self.dir / f"{sha}.json"

    def put(self, obj: dict[str, Any]) -> str:
        # Serialize once and name the blob by the sha256 of exactly the bytes written.
        payload = canonical_json(obj).encode()
        sha = hashlib.sha256(payload).hexdigest()
        path = self._path(sha)
        if not path.exists():
            # Write a temp file in the same directory, flush it to disk, then rename it into place, so the
            # blob is either absent or complete. If anything fails before the rename, leaving the block
            # deletes the temp file.
            with tempfile.NamedTemporaryFile("wb", dir=self.dir, suffix=".tmp", delete_on_close=False) as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
                fh.close()
                os.replace(fh.name, path)
        return sha

    def exists(self, sha: str) -> bool:
        return self._path(sha).exists()

    def get(self, sha: str) -> dict[str, Any]:
        return json.loads(self._path(sha).read_text(encoding="utf-8"))
