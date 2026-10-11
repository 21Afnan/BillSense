"""Small on-disk JSON cache helpers: versioned keys, atomic writes, corruption-tolerant reads."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

CACHE_SCHEMA_VERSION = "3"


def cache_key(*parts: bytes | str) -> str:
    """sha256 over the schema version and every part, with separators so ("ab","c") != ("a","bc")."""
    h = hashlib.sha256(CACHE_SCHEMA_VERSION.encode())
    for part in parts:
        data = part if isinstance(part, bytes) else part.encode("utf-8")
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
    return h.hexdigest()


def read_json(path: Path):
    """Return cached JSON, or None if missing or unreadable (a corrupt file is removed, never fatal)."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        log.warning("ignoring corrupt cache file %s (%s)", path.name, err)
        try:
            path.unlink()
        except OSError:
            pass
        return None


def write_json_atomic(path: Path, data) -> None:
    """Write to a temp file in the same folder, then rename: readers never see a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError as err:  # a cache write failure must never stop the pipeline
        log.warning("could not write cache %s (%s)", path.name, err)
        try:
            os.unlink(tmp)
        except OSError:
            pass
