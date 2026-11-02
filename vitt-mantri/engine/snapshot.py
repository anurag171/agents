"""Shared snapshot: the scheduler process writes, every web worker reads. Atomic replace, mtime-cached reads."""
from __future__ import annotations

import json
import os
import threading

_LOCK, _CACHE = threading.Lock(), {}


def write(path: str, snap: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w") as fh:
        json.dump(snap, fh)
        fh.flush(); os.fsync(fh.fileno())
    os.replace(tmp, path)                      # readers see the old or the new file, never a partial one


def read(path: str):
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (st.st_mtime_ns, st.st_size)
    with _LOCK:
        hit = _CACHE.get(path)
        if hit and hit[0] == key:
            return hit[1]
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    with _LOCK:
        _CACHE[path] = (key, data)
    return data
