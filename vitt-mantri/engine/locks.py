"""Cross-process file lock (flock). Released automatically by the OS if the holder dies, so another
gunicorn worker can take over. On platforms without fcntl it degrades to a process-local lock."""
from __future__ import annotations

import os
import threading

try:
    import fcntl
except ImportError:                                   # Windows
    fcntl = None

_LOCAL, _LOCAL_GUARD = set(), threading.Lock()


class FileLock:
    def __init__(self, path: str):
        self.path, self._fh = path, None

    def acquire(self) -> bool:
        """Non-blocking. True if this object now holds the lock."""
        if self._fh is not None:
            return True
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        fh = open(self.path, "a+")
        try:
            if fcntl is not None:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                with _LOCAL_GUARD:
                    if self.path in _LOCAL:
                        raise OSError("held")
                    _LOCAL.add(self.path)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self):
        if self._fh is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self._fh, fcntl.LOCK_UN)
            else:
                with _LOCAL_GUARD:
                    _LOCAL.discard(self.path)
        finally:
            self._fh.close(); self._fh = None
