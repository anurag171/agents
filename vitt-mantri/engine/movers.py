"""Live market-mover layer: what the market is doing right now (gainers, losers, most active, 52-week extremes).

The news board only knows names reachable through themes. This layer
  1. pulls a live quote snapshot (NSE Nifty-500 snapshot, Yahoo screeners, or a dropped CSV),
  2. derives gainers / losers / most-active / near-52w-high / near-52w-low / volume shockers,
  3. annotates every news signal with the market's reaction (agrees / disagrees / priced in),
  4. adds market-driven WATCH rows for big movers the news engine has no explanation for.
Mover-only rows are never BUY/SELL: price momentum without an explanation is not a recommendation.
Endpoint formats below could not be verified from the build sandbox; each source fails independently
and is reported in `coverage`, so a format change shows up as "Market: NSE - 0 quotes", not a crash.
"""
from __future__ import annotations

import csv
import io
import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from typing import Optional

NSE_HOME = "https://www.nseindia.com"
NSE_SNAPSHOT = NSE_HOME + "/api/equity-stockIndices?index=NIFTY%20500"
YAHOO_SCREENER = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?scrIds={scr}&count=25"
CACHE_S = int(os.getenv("VM_MARKET_TTL_S", "120"))
PRICED_IN_PCT, DISAGREE_PCT = 3.0, -2.0


@dataclass
class Quote:
    symbol: str
    name: str = ""
    mkt: str = "IN"
    price: float = 0.0
    change_pct: float = 0.0
    volume: float = 0.0
    value: float = 0.0
    avg_volume: Optional[float] = None
    year_high: Optional[float] = None
    year_low: Optional[float] = None
    source: str = ""


def _f(v, d=None):
    try:
        return float(str(v).replace(",", "")) if v not in (None, "", "-") else d
    except ValueError:
        return d


def parse_nse_snapshot(text: str) -> list[Quote]:
    out = []
    for r in json.loads(text).get("data", []):
        sym = str(r.get("symbol") or "")
        if not sym or " " in sym or str(r.get("priority")) == "0":      # first row is the index itself
            continue
        p = _f(r.get("lastPrice"), 0.0)
        if p > 0:
            out.append(Quote(sym.upper(), (r.get("meta") or {}).get("companyName", sym), "IN", p, _f(r.get("pChange"), 0.0),
                             _f(r.get("totalTradedVolume"), 0.0), _f(r.get("totalTradedValue"), 0.0), None,
                             _f(r.get("yearHigh")), _f(r.get("yearLow")), "NSE"))
    return out


def parse_yahoo_screener(text: str) -> list[Quote]:
    res = (json.loads(text).get("finance", {}).get("result") or [{}])[0].get("quotes", [])
    out = []
    for q in res:
        sym, p = str(q.get("symbol") or ""), _f(q.get("regularMarketPrice"), 0.0)
        if not sym or p <= 0:
            continue
        ind = sym.endswith((".NS", ".BO"))
        vol = _f(q.get("regularMarketVolume"), 0.0)
        out.append(Quote(sym.rsplit(".", 1)[0].upper() if ind else sym.upper(), q.get("shortName") or q.get("longName") or sym,
                         "IN" if ind else "US", p, _f(q.get("regularMarketChangePercent"), 0.0), vol, vol * p,
                         _f(q.get("averageDailyVolume3Month")), _f(q.get("fiftyTwoWeekHigh")), _f(q.get("fiftyTwoWeekLow")), "Yahoo"))
    return out


def parse_quotes_csv(text: str, mkt: str = "IN") -> list[Quote]:
    """Drop-in for restricted networks. Header (any case): symbol,price,change_pct[,volume,avg_volume,year_high,year_low,name]."""
    out = []
    for r in csv.DictReader(io.StringIO(text.lstrip("\ufeff"))):
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        p = _f(r.get("price"), 0.0)
        if r.get("symbol") and p > 0:
            vol = _f(r.get("volume"), 0.0)
            out.append(Quote(r["symbol"].upper(), r.get("name", ""), mkt, p, _f(r.get("change_pct"), 0.0), vol, vol * p,
                             _f(r.get("avg_volume")), _f(r.get("year_high")), _f(r.get("year_low")), "file"))
    return out


def derive_lists(quotes: list[Quote], n: int = 10) -> dict:
    """Gainers/losers are limited to liquid names (top ~60% by traded value) so thin stocks don't dominate."""
    if not quotes:
        return {k: [] for k in ("gainers", "losers", "active", "near_high", "near_low", "volume_shock")}
    vals = sorted(q.value for q in quotes)
    floor = vals[int(len(vals) * 0.4)] if len(vals) >= 10 else 0.0
    liquid = [q for q in quotes if q.value >= floor]
    d = lambda qs: [asdict(q) for q in qs[:n]]
    return {
        "gainers": d(sorted((q for q in liquid if q.change_pct > 0), key=lambda q: -q.change_pct)),
        "losers": d(sorted((q for q in liquid if q.change_pct < 0), key=lambda q: q.change_pct)),
        "active": d(sorted(quotes, key=lambda q: -q.value)),
        "near_high": d(sorted((q for q in liquid if q.year_high and q.price >= 0.98 * q.year_high), key=lambda q: -q.price / q.year_high)),
        "near_low": d(sorted((q for q in liquid if q.year_low and q.price <= 1.02 * q.year_low), key=lambda q: q.price / q.year_low)),
        "volume_shock": d(sorted((q for q in quotes if q.avg_volume and q.volume >= 2 * q.avg_volume), key=lambda q: -q.volume / q.avg_volume)),
    }


def _mk(q: dict) -> dict:
    flags = []
    if q.get("year_high") and q["price"] >= 0.98 * q["year_high"]: flags.append("near 52w high")
    if q.get("year_low") and q["price"] <= 1.02 * q["year_low"]: flags.append("near 52w low")
    if q.get("avg_volume") and q["volume"] >= 2 * q["avg_volume"]: flags.append(f"volume {q['volume'] / q['avg_volume']:.1f}x avg")
    return {"price": round(q["price"], 2), "change_pct": round(q["change_pct"], 2), "mkt": q["mkt"], "flags": flags, "source": q["source"]}


def merge_into_board(board: list[dict], quotes: list[Quote], lists: dict, extra_rows: int = 12) -> list[dict]:
    """Annotate news rows with the market reaction; add unexplained big movers as WATCH rows."""
    by_sym = {q.symbol: asdict(q) for q in quotes}
    for r in board:
        q = by_sym.get(r["ticker"])
        if not q:
            continue
        r["market"] = _mk(q)
        d = 1 if r["action"] == "BUY" else -1 if r["action"] == "SELL" else 0
        aligned = q["change_pct"] * d
        if d and aligned >= PRICED_IN_PCT:
            r["news_action"], r["priced_in"], r["action"] = r["action"], True, "WATCH"
            r["why"] = f"{r.get('why') or ''} [priced in: {q['change_pct']:+.1f}% today]".strip()
        elif d and aligned <= DISAGREE_PCT:
            r["market"]["flags"].append("market disagrees")
    have, added = {r["ticker"] for r in board}, 0
    pool = ([("Top gainer", x) for x in lists["gainers"]] + [("Top loser", x) for x in lists["losers"]] +
            [("Volume shock", x) for x in lists["volume_shock"]] + [("Most active", x) for x in lists["active"][:5]])
    for label, q in sorted(pool, key=lambda t: -abs(t[1]["change_pct"])):
        if added >= extra_rows or q["symbol"] in have:
            continue
        have.add(q["symbol"]); added += 1
        why = f"{label}: {q['change_pct']:+.1f}% — no mapped news explains the move yet."
        board.append({"ticker": q["symbol"], "action": "WATCH", "net": 0, "conviction": 1, "why": why, "whys": [why], "headlines": [],
                      "sources": [q["source"]], "regions": [q["mkt"]], "direct_buy": 0, "direct_sell": 0, "indirect_buy": 0, "indirect_sell": 0,
                      "origin": "market-mover", "unexplained": True, "market": _mk(q)})
    return board


# ---- live fetch (cached, never raises) -------------------------------------------------
_LOCK, _CACHE = threading.Lock(), {"at": 0.0, "quotes": [], "coverage": []}


def _session():
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (compatible; VittaMantri/2.0; research)", "Accept": "application/json,text/csv,*/*", "Accept-Language": "en-US,en;q=0.9"})
    return s


def fetch_quotes(force: bool = False, session=None) -> tuple[list[Quote], list[dict]]:
    with _LOCK:
        if not force and time.time() - _CACHE["at"] < CACHE_S:
            return _CACHE["quotes"], _CACHE["coverage"]
        quotes, cov, s = [], [], session or _session()
        want = os.getenv("VM_MARKET_SOURCES", "nse,yahoo").split(",")

        def run(name, fn):
            try:
                got = fn()
                quotes.extend(got); cov.append({"source": f"Market: {name}", "raw": len(got)})
            except Exception as e:                                   # one source failing must never break a cycle
                cov.append({"source": f"Market: {name}", "raw": 0, "error": type(e).__name__})

        if "nse" in want:
            def nse():
                s.get(NSE_HOME, timeout=(3.05, 10))                  # NSE needs a cookie from the home page first
                r = s.get(NSE_SNAPSHOT, timeout=(3.05, 15), headers={"Referer": NSE_HOME})
                r.raise_for_status()
                return parse_nse_snapshot(r.text)
            run("NSE", nse)
        if "yahoo" in want:
            def yahoo():
                got = []
                for scr in ("day_gainers", "day_losers", "most_actives"):
                    r = s.get(YAHOO_SCREENER.format(scr=scr), timeout=(3.05, 10)); r.raise_for_status()
                    got += parse_yahoo_screener(r.text)
                return got
            run("Yahoo", yahoo)
        d = os.getenv("VM_QUOTES_DIR")
        if d and os.path.isdir(d):
            def files():
                got = []
                for f in sorted(os.listdir(d)):
                    if f.endswith(".csv"):
                        got += parse_quotes_csv(open(os.path.join(d, f), encoding="utf-8-sig").read(), "US" if f.lower().startswith("us") else "IN")
                return got
            run("files", files)
        seen = {}
        for q in quotes:                                             # prefer NSE (first) over duplicates
            seen.setdefault((q.symbol, q.mkt), q)
        quotes = list(seen.values())
        _CACHE.update(at=time.time(), quotes=quotes, coverage=cov)
        return quotes, cov
