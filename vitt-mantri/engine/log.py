"""One-line-per-event logging. VM_LOG_JSON=1 -> JSON lines (for log collectors); otherwise readable text. VM_LOG_LEVEL sets the level."""
import json
import logging
import os
import sys
import time


class _Json(logging.Formatter):
    def format(self, r):
        d = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(r.created)) + "Z", "level": r.levelname, "msg": r.getMessage(), **getattr(r, "fields", {})}
        if r.exc_info:
            d["exc"] = self.formatException(r.exc_info)
        return json.dumps(d, default=str)


_log = logging.getLogger("vm")
if not _log.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(_Json() if os.getenv("VM_LOG_JSON") == "1" else logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _log.addHandler(h); _log.setLevel(os.getenv("VM_LOG_LEVEL", "INFO")); _log.propagate = False


def event(msg, level=logging.INFO, **fields):
    _log.log(level, msg + ("  " + " ".join(f"{k}={v}" for k, v in fields.items()) if os.getenv("VM_LOG_JSON") != "1" and fields else ""), extra={"fields": fields})
