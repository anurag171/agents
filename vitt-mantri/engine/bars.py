"""Daily OHLCV history from Yahoo's chart endpoint (v8). Cached, parallel-friendly, never raises.
The endpoint format could not be verified from the build sandbox: parse failures return None and the callers
degrade to 'chart unavailable'."""
from __future__ import annotations

import json
from typing import Optional
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote

from .technicals import Bar

URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d"
TTL_S, FAIL_TTL_S = 300, 600
_LOCK, _CACHE = threading.Lock(), {}


def yahoo_symbol(ticker: str, mkt: str = "IN") -> str:
    return ticker if mkt != "IN" else f"{ticker}.NS"


def parse_yahoo_chart(text: str) -> Optional[dict]:
    res = (json.loads(text).get("chart", {}).get("result") or [None])[0]
    if not res or not res.get("timestamp"):
        return None
    q, meta, bars = res["indicators"]["quote"][0], res.get("meta", {}), []
    for i, t in enumerate(res["timestamp"]):
        o, h, l, c = (q[k][i] for k in ("open", "high", "low", "close"))
        if None in (o, h, l, c) or min(o, h, l, c) <= 0 or h < l:
            continue                                          # drop holes and corrupt bars
        v = (q.get("volume") or [None] * len(res["timestamp"]))[i] or 0
        bars.append(Bar(datetime.fromtimestamp(t, timezone.utc).date(), o, h, l, c, float(v)))
    if len(bars) < 2:
        return None
    price = float(meta.get("regularMarketPrice") or bars[-1].c)
    prev = bars[-2].c if abs(bars[-1].c / price - 1) < 0.02 else bars[-1].c   # last bar is "today" when it matches live price
    return {"symbol": meta.get("symbol", ""), "currency": meta.get("currency", ""), "price": price,
            "prev_close": prev, "change_pct": (price / prev - 1) * 100, "bars": bars}


def fetch_bars(sym: str, session=None, rng: str = "1y", ttl: Optional[int] = None) -> Optional[dict]:
    key = (sym, rng)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < ((ttl or TTL_S) if hit[1] else FAIL_TTL_S):
            return hit[1]
    data = None
    try:
        if session is None:
            import requests
            session = requests.Session()
            session.headers["User-Agent"] = "Mozilla/5.0 (compatible; VittaMantri/2.0; research)"
        r = session.get(URL.format(sym=quote(sym, safe="^=-."), rng=rng), timeout=(3.05, 10))
        r.raise_for_status()
        data = parse_yahoo_chart(r.text)
    except Exception:
        data = None
    with _LOCK:
        _CACHE[key] = (time.time(), data)
        if len(_CACHE) > 1500:
            cutoff = time.time() - 6 * 3600
            for k in [k for k, v in _CACHE.items() if v[0] < cutoff]:
                del _CACHE[k]
    return data
