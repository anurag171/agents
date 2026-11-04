"""Fundamentals for the screen: ROE, margins, leverage, growth, valuation.

Sources (merged, best first):
  1. FILES in $VM_FUNDAMENTALS_DIR (*.csv / *.json) - e.g. an export from Screener/Trendlyne/your broker. Most robust in a locked-down network.
  2. SQLite cache (fundamentals table), refreshed every VM_FUND_CACHE_DAYS (default 7): fundamentals change quarterly, not daily.
  3. Yahoo quoteSummary (crumb flow), fetched progressively: at most VM_FUND_BATCH (default 120) missing symbols per run, so the first
     full fill takes a few runs and never hammers the endpoint. NOTE: the Yahoo response format and crumb flow could not be verified from
     the build sandbox; a failure is reported in the coverage info and the screen falls back to price-only (and says so).
Units: percentages are in percent (24.0 = 24%), debt_equity is a ratio (0.45), market_cap in currency units."""
from __future__ import annotations

import csv
import io
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from .store import Store

CACHE_DAYS = int(os.getenv("VM_FUND_CACHE_DAYS", "7"))
BATCH = int(os.getenv("VM_FUND_BATCH", "120"))
FIELDS = ("roe", "roce", "debt_equity", "rev_growth", "earn_growth", "op_margin", "profit_margin", "pe", "pb", "peg", "promoter_pct", "market_cap", "div_yield")
YAHOO_SUMMARY = "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{sym}?modules=financialData,defaultKeyStatistics,summaryDetail,assetProfile&crumb={crumb}"
ALIASES = {
    "symbol": ("symbol", "ticker", "nse code", "nse symbol", "scrip"), "roe": ("roe", "return on equity", "roe %"), "roce": ("roce", "return on capital employed", "roce %"),
    "debt_equity": ("debt_equity", "debt to equity", "debt/equity", "d/e", "de"), "rev_growth": ("rev_growth", "sales growth", "revenue growth", "sales growth 3years", "sales var 3yrs %"),
    "earn_growth": ("earn_growth", "profit growth", "earnings growth", "profit growth 3years", "profit var 3yrs %"), "op_margin": ("op_margin", "opm", "operating margin", "opm %"),
    "profit_margin": ("profit_margin", "npm", "net margin", "net profit margin"), "pe": ("pe", "p/e", "price to earning", "stock p/e"), "pb": ("pb", "p/b", "price to book value", "price to book"),
    "peg": ("peg", "peg ratio"), "promoter_pct": ("promoter_pct", "promoter holding", "promoter holding %"), "market_cap": ("market_cap", "market cap", "mar cap rs.cr."),
    "div_yield": ("div_yield", "dividend yield", "dividend yield %"), "sector": ("sector", "industry"), "name": ("name", "company"),
}


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d[\d,]*\.?\d*", str(v))
    return float(m.group(0).replace(",", "")) if m else None


def _raw(d, k):
    v = d.get(k) if isinstance(d, dict) else None
    return _num(v.get("raw") if isinstance(v, dict) else v)


def parse_yahoo_summary(text: str):
    res = (json.loads(text).get("quoteSummary", {}).get("result") or [None])[0]
    if not res:
        return None
    fd, ks, sd, ap = (res.get(k) or {} for k in ("financialData", "defaultKeyStatistics", "summaryDetail", "assetProfile"))
    pct = lambda v: None if v is None else v * 100
    de = _raw(fd, "debtToEquity")
    out = {"roe": pct(_raw(fd, "returnOnEquity")), "roce": None, "debt_equity": None if de is None else de / 100,        # Yahoo quotes D/E in percent
           "rev_growth": pct(_raw(fd, "revenueGrowth")), "earn_growth": pct(_raw(fd, "earningsGrowth")), "op_margin": pct(_raw(fd, "operatingMargins")),
           "profit_margin": pct(_raw(fd, "profitMargins")), "pe": _raw(sd, "trailingPE"), "pb": _raw(ks, "priceToBook"), "peg": _raw(ks, "pegRatio"),
           "promoter_pct": pct(_raw(ks, "heldPercentInsiders")), "market_cap": _raw(sd, "marketCap"), "div_yield": pct(_raw(sd, "dividendYield"))}
    if not any(out[k] is not None for k in FIELDS):
        return None
    out["sector"] = ap.get("sector") or ap.get("industry") or ""
    return out


def _row_to_fund(row: dict):
    low = {re.sub(r"\s+", " ", str(k).strip().lower()): v for k, v in row.items()}
    get = lambda f: next((low[a] for a in ALIASES[f] if a in low and low[a] not in ("", None)), None)
    sym = get("symbol")
    if not sym:
        return None, None
    out = {f: _num(get(f)) for f in FIELDS}
    out["sector"], out["name"] = str(get("sector") or ""), str(get("name") or "")
    return str(sym).strip().upper().removesuffix(".NS"), out


def parse_fundamentals_file(text: str, as_csv: bool = True) -> dict:
    rows = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff")))) if as_csv else (lambda d: d if isinstance(d, list) else [dict(v, symbol=k) for k, v in d.items()])(json.loads(text))
    out = {}
    for r in rows:
        sym, f = _row_to_fund(r)
        if sym and any(f[k] is not None for k in FIELDS):
            out[sym] = f
    return out


# ---- cache ------------------------------------------------------------------------------------------------
def cache_get(store: Store, symbols, max_age_days: int = CACHE_DAYS) -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    out = {}
    with store.conn() as c:
        for i in range(0, len(symbols), 500):
            chunk = symbols[i:i + 500]
            for r in c.execute(f"SELECT symbol, json FROM fundamentals WHERE fetched_at>=? AND symbol IN ({','.join('?' * len(chunk))})", (cutoff, *chunk)).fetchall():
                out[r["symbol"]] = json.loads(r["json"])
    return out


def cache_put(store: Store, data: dict, source: str):
    now = datetime.now(timezone.utc).isoformat()
    with store.conn() as c:
        for sym, f in data.items():
            c.execute("INSERT INTO fundamentals(symbol,source,fetched_at,json) VALUES(?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET source=excluded.source, fetched_at=excluded.fetched_at, json=excluded.json",
                      (sym, source, now, json.dumps(f)))


# ---- Yahoo ------------------------------------------------------------------------------------------------
def _session():
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (compatible; VittaMantri/2.0; research)", "Accept": "application/json,*/*"})
    return s


def fetch_yahoo(symbols, session=None, workers: int = 4):
    """-> (data, error). Crumb flow: cookie from fc.yahoo.com, then /v1/test/getcrumb. Any failure returns what we have plus an error string."""
    s = session or _session()
    try:
        try:
            s.get("https://fc.yahoo.com", timeout=(3.05, 8))
        except Exception:
            pass
        r = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=(3.05, 8)); r.raise_for_status()
        crumb = r.text.strip()
        if not crumb or "<" in crumb:
            return {}, "no crumb"
    except Exception as exc:
        return {}, f"crumb: {type(exc).__name__}"

    def one(sym):
        try:
            rr = s.get(YAHOO_SUMMARY.format(sym=quote(sym + ".NS", safe=""), crumb=quote(crumb, safe="")), timeout=(3.05, 10)); rr.raise_for_status()
            return sym, parse_yahoo_summary(rr.text)
        except Exception:
            return sym, None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        got = {sym: f for sym, f in pool.map(one, symbols) if f}
    return got, (None if got or not symbols else "no data returned")


def load_fundamentals(store: Store, symbols: list, file_dir: str | None = None, session=None, batch: int = BATCH, fetch=fetch_yahoo):
    """-> (fundamentals by symbol, coverage info). Files beat cache beat fresh fetches."""
    file_dir = file_dir if file_dir is not None else os.getenv("VM_FUNDAMENTALS_DIR")
    data, info = {}, {"file": 0, "cache": 0, "fetched": 0, "missing": 0, "error": None}
    wanted = set(symbols)
    if file_dir and os.path.isdir(file_dir):
        for f in sorted(os.listdir(file_dir)):
            try:
                txt = open(os.path.join(file_dir, f), encoding="utf-8-sig").read()
                for sym, fund in parse_fundamentals_file(txt, f.endswith(".csv")).items():
                    if sym in wanted:
                        data[sym] = fund
            except Exception as exc:
                info["error"] = f"{f}: {type(exc).__name__}"
        info["file"] = len(data)
    cached = {k: v for k, v in cache_get(store, symbols).items() if k not in data}
    data.update(cached); info["cache"] = len(cached)
    todo = [s for s in symbols if s not in data][:batch]
    if todo:
        got, err = fetch(todo, session)
        if got:
            cache_put(store, got, "yahoo"); data.update(got); info["fetched"] = len(got)
        info["error"] = info["error"] or err
    info["missing"] = len([s for s in symbols if s not in data])
    return data, info
