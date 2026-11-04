"""Dynamic instrument universe (replaces the hardcoded COMPANIES dict).

Sources (all optional, merged, cached on disk, refreshed on a TTL):
  * NSE index constituent CSVs  (Nifty 50 / Next 50 / 500)  -> tier 1/2/3 + industry
  * NSE listed-equity CSV (EQUITY_L.csv)                   -> tier 4 (everything listed)
  * SEC company_tickers.json                                -> US issuers (no sector data)
  * Files dropped in $VM_UNIVERSE_DIR (*.csv / *.json)      -> works behind restricted networks
  * Curated asset instruments from config/assets.json       -> ETFs, REITs, gold, crypto, FX
  * Legacy seed (old COMPANIES)                             -> last-resort offline fallback
Source URLs/formats must be verified from your own network; every loader degrades to the cache.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field

DATA_DIR = os.getenv("VM_DATA", "data")
CACHE = os.path.join(DATA_DIR, "universe.json")
TTL_S = int(os.getenv("VM_UNIVERSE_TTL_H", "24")) * 3600
NSE_URLS = {
    1: "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv",
    2: "https://nsearchives.nseindia.com/content/indices/ind_niftynext50list.csv",
    3: "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
}
NSE_ALL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
SEC_URL = "https://www.sec.gov/files/company_tickers.json"

INDUSTRY_TAGS = {  # NSE "Industry" -> coarse tags (fine-grained tags come from config/tag_overrides.json)
    "information technology": ["it", "tech"], "financial services": ["financials"],
    "oil gas & consumable fuels": ["oil_gas", "energy"], "automobile and auto components": ["auto"],
    "fast moving consumer goods": ["fmcg", "consumer"], "healthcare": ["pharma"], "metals & mining": ["metals"],
    "power": ["power"], "construction": ["infra"], "construction materials": ["cement"], "capital goods": ["capital-goods"],
    "chemicals": ["chemicals"], "realty": ["realty"], "telecommunication": ["telecom"], "consumer durables": ["consumer"],
    "consumer services": ["consumer"], "services": ["services"], "textiles": ["textiles"],
}
_LEGAL = re.compile(r"\b(limited|ltd|inc|incorporated|corporation|corp|plc|co|company|holdings?|the|class [a-c]|common stock)\b|\(.*?\)", re.I)
_COMMON = {"ALL", "FOR", "ARE", "CAN", "ONE", "NOW", "NEW", "OUT", "TOP", "BIG", "HAS", "WAS", "SEE", "LOW", "ANY", "GOLD", "BANK", "POWER", "IT"}


@dataclass
class Instrument:
    symbol: str
    name: str
    mkt: str = "IN"                       # IN | US | GL
    asset_class: str = "equity"           # equity|etf|reit|invit|gold|silver|commodity|fx|crypto|bond|index
    exchange: str = "NSE"
    industry: str = ""
    isin: str = ""
    tier: int = 4                         # 1 = Nifty50 ... 4 = other listed; lower = more liquid
    tags: list = field(default_factory=list)
    aliases: list = field(default_factory=list)
    source: str = ""
    verified: bool = True                 # False for curated asset symbols not yet checked against exchange lists


def clean_name(name: str) -> str:
    n = _LEGAL.sub(" ", (name or "").lower().replace("&", " and "))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", n)).strip()


def parse_nse_index_csv(text: str, tier: int = 3) -> list[Instrument]:
    out = []
    for r in csv.DictReader(io.StringIO(text.lstrip("\ufeff"))):
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        sym = r.get("symbol")
        if sym:
            out.append(Instrument(sym.upper(), r.get("company name", sym), industry=r.get("industry", ""),
                                  isin=r.get("isin code", ""), tier=tier, source=f"nse_index_t{tier}"))
    return out


def parse_nse_equity_list(text: str) -> list[Instrument]:
    out = []
    for r in csv.DictReader(io.StringIO(text.lstrip("\ufeff"))):
        r = {(k or "").strip().upper(): (v or "").strip() for k, v in r.items()}
        if r.get("SYMBOL") and r.get("SERIES", "EQ") in ("EQ", "BE", "SM", "ST"):
            out.append(Instrument(r["SYMBOL"].upper(), r.get("NAME OF COMPANY", r["SYMBOL"]), isin=r.get("ISIN NUMBER", ""), tier=4, source="nse_all"))
    return out


def parse_sec_tickers(text: str) -> list[Instrument]:
    data = json.loads(text)
    rows = data.values() if isinstance(data, dict) else data
    return [Instrument(str(r["ticker"]).upper(), r["title"], mkt="US", exchange="US", tier=3, source="sec")
            for r in rows if r.get("ticker") and r.get("title")]


class Universe:
    def __init__(self, instruments=(), overrides=None):
        self.by_symbol: dict[str, Instrument] = {}
        self.overrides = overrides or {}     # symbol -> [fine tags]
        self._rx = self._sym_rx = None
        self._alias_map: dict[str, str] = {}
        self.meta = {"loaded_at": time.time(), "sources": {}}
        self.add(instruments)

    # ---- build -----------------------------------------------------------
    def add(self, instruments):
        for i in instruments:
            cur = self.by_symbol.get(i.symbol)
            if cur and cur.tier < i.tier:                      # keep the more liquid/specific record
                if i.industry and not cur.industry: cur.industry = i.industry
                continue
            if cur:
                i.industry = i.industry or cur.industry; i.tags = list(set(i.tags) | set(cur.tags))
            self.by_symbol[i.symbol] = i
            self.meta["sources"][i.source] = self.meta["sources"].get(i.source, 0) + 1
        self._rx, self._sym_rx, self._alias_map = None, None, {}          # force the matcher to rebuild (was stale after add())
        for i in self.by_symbol.values():
            tags = set(i.tags) | set(self.overrides.get(i.symbol, [])) | set(INDUSTRY_TAGS.get(i.industry.lower(), []))
            i.tags = sorted(tags)
        return self

    def __len__(self): return len(self.by_symbol)
    def has(self, sym): return sym in self.by_symbol
    def tickers(self): return sorted(self.by_symbol)

    # ---- matching --------------------------------------------------------
    def _build(self):
        amap, count = {}, {}
        for i in self.by_symbol.values():
            if i.asset_class != "equity" and not i.aliases:
                continue
            names = {clean_name(i.name), *[a.lower() for a in i.aliases]}
            for a in {n for n in names if len(n) >= 5 or n in {x.lower() for x in i.aliases}}:
                count.setdefault(a, set()).add(i.symbol)
        for a, syms in count.items():
            if len(syms) == 1:                                   # drop ambiguous aliases entirely
                amap[a] = next(iter(syms))
        self._alias_map = amap
        alts = sorted(amap, key=len, reverse=True)
        self._rx = re.compile(r"(?<![a-z0-9])(?:%s)(?![a-z0-9])" % "|".join(map(re.escape, alts))) if alts else None
        syms = [s for s, i in self.by_symbol.items() if i.mkt == "IN" and i.asset_class == "equity" and len(s) >= 3 and s not in _COMMON]
        self._sym_rx = re.compile(r"(?<![A-Za-z0-9&$])(?:%s)(?![A-Za-z0-9&])" % "|".join(map(re.escape, sorted(syms, key=len, reverse=True)))) if syms else None

    def match(self, text: str) -> list[Instrument]:
        if self._rx is None and not self._alias_map: self._build()
        found, low = {}, (text or "").lower()
        if self._rx:
            for m in self._rx.finditer(low): found.setdefault(self._alias_map[m.group(0)], m.start())
        if self._sym_rx:                                          # ALL-CAPS NSE symbols only (TCS, ITC, ONGC)
            for m in self._sym_rx.finditer(text or ""): found.setdefault(m.group(0), m.start())
        for m in re.finditer(r"\$([A-Z]{1,5})\b", text or ""):    # cashtags for US names
            if m.group(1) in self.by_symbol: found.setdefault(m.group(1), m.start())
        return [self.by_symbol[s] for s, _ in sorted(found.items(), key=lambda kv: kv[1])]

    def select(self, tags, mkt=None, limit=3, exclude=()):
        want = set(tags)
        c = [i for i in self.by_symbol.values() if want & set(i.tags) and (not mkt or i.mkt == mkt) and i.symbol not in exclude]
        return sorted(c, key=lambda i: (i.tier, i.symbol))[:limit]

    # ---- persistence -----------------------------------------------------
    def save(self, path=None):
        path = path or CACHE
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({"saved_at": time.time(), "meta": self.meta, "instruments": [asdict(i) for i in self.by_symbol.values()]}, fh)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path=None, overrides=None):
        with open(path or CACHE) as fh:
            d = json.load(fh)
        u = cls([Instrument(**i) for i in d["instruments"]], overrides)
        u.meta["loaded_at"] = d.get("saved_at", 0)
        return u


def grow_universe(universe, quotes) -> int:
    """Every NSE symbol seen in the live quote snapshot becomes an instrument, so coverage no longer depends on the index CSVs loading."""
    new = [Instrument(q.symbol, q.name or q.symbol, mkt=q.mkt, industry=getattr(q, "industry", ""), tier=3, source="quotes")
           for q in quotes if q.mkt == "IN" and not universe.has(q.symbol)]
    if new:
        universe.add(new)
    return len(new)


def http_text(url):
    import requests
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; VittaMantri/2.0; research)", "Accept": "text/csv,application/json,*/*"}, timeout=(3.05, 20))
    r.raise_for_status()
    return r.text


# ---- loading / refresh --------------------------------------------------------
def _read_json(path, default):
    try:
        with open(path) as fh: return json.load(fh)
    except (OSError, ValueError): return default

CFG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")

def asset_instruments() -> list[Instrument]:
    return [Instrument(**{**a, "source": "assets_cfg", "tier": a.get("tier", 2)}) for a in _read_json(os.path.join(CFG, "assets.json"), {}).get("instruments", [])]

def legacy_seed(overrides) -> list[Instrument]:
    from .universe import COMPANIES
    out = []
    for key, m in COMPANIES.items():
        overrides.setdefault(m["ticker"], [])
        if m["sector"] not in overrides[m["ticker"]]: overrides[m["ticker"]].append(m["sector"])
        out.append(Instrument(m["ticker"], m["name"], mkt=m.get("mkt", "IN"), tier=1, aliases=[key], source="legacy_seed"))
    return out

def fetch_sources(get, drop_dir=None) -> list[Instrument]:
    """`get(url) -> str` must use your proxy/CA-aware session. Each source fails independently."""
    out, errors = [], {}
    for tier, url in NSE_URLS.items():
        try: out += parse_nse_index_csv(get(url), tier)
        except Exception as e: errors[url] = type(e).__name__
    for name, fn, url in (("nse_all", parse_nse_equity_list, NSE_ALL), ("sec", parse_sec_tickers, SEC_URL)):
        try: out += fn(get(url))
        except Exception as e: errors[name] = type(e).__name__
    if drop_dir and os.path.isdir(drop_dir):
        for f in sorted(os.listdir(drop_dir)):
            try:
                txt = open(os.path.join(drop_dir, f), encoding="utf-8-sig").read()
                out += parse_sec_tickers(txt) if f.endswith(".json") else (parse_nse_index_csv(txt, 3) if "industry" in txt.splitlines()[0].lower() else parse_nse_equity_list(txt))
            except Exception as e: errors[f] = type(e).__name__
    if not out:
        raise RuntimeError(f"no universe source reachable: {errors}")
    return out

def _no_network(url):
    raise RuntimeError("no fetcher configured")

_LOCK, _U = threading.Lock(), None

RETRY_S = int(os.getenv("VM_UNIVERSE_RETRY_MIN", "30")) * 60


def get_universe(get=None, force=False) -> Universe:
    """Return the current universe. Cheap for the decoder (get=None never touches disk/network once loaded).
    The scheduler passes `get` (proxy/CA-aware fetcher): a stale universe is then refreshed live, at most once
    per RETRY_S after a failure. Order: live -> disk cache -> legacy seed. Never raises, never empty."""
    global _U
    with _LOCK:
        now = time.time()
        stale = _U is not None and now - _U.meta["loaded_at"] >= TTL_S
        if _U is not None and not force and (get is None or not stale or now - _U.meta.get("last_attempt", 0) < RETRY_S):
            return _U
        overrides = _read_json(os.path.join(CFG, "tag_overrides.json"), {})
        attempt, fresh = _U.meta.get("last_attempt", 0) if _U else 0, None
        if get is not None or os.getenv("VM_UNIVERSE_DIR"):
            attempt = now
            try:
                fresh = fetch_sources(get or _no_network, os.getenv("VM_UNIVERSE_DIR"))
            except Exception:
                fresh = None
        if fresh:
            u = Universe(legacy_seed(overrides) + fresh + asset_instruments(), overrides)
            u.meta["origin"] = "live"; u.meta["loaded_at"] = now; u.save()
        elif _U is not None and _U.meta.get("origin") in ("live", "cache"):
            u = _U                                           # keep serving what we have; retry after backoff
        else:
            try:
                u = Universe.load(CACHE, overrides); u.meta["origin"] = "cache"      # loaded_at = file save time
            except Exception:
                u = Universe(legacy_seed(overrides) + asset_instruments(), overrides)
                u.meta["origin"] = "seed"; u.meta["loaded_at"] = now - TTL_S + 600     # re-try live within ~10 min
        u.meta["last_attempt"] = attempt
        _U = u
        return u


if __name__ == "__main__":
    # python -m engine.instruments   -> tries every universe source from THIS machine and tells you what worked
    errors = {}
    try:
        got = fetch_sources(http_text, os.getenv("VM_UNIVERSE_DIR"))
        u = get_universe(get=http_text, force=True)
        print(f"loaded {len(got)} instruments from live sources -> universe size {len(u)} (origin: {u.meta.get('origin')})")
    except Exception as exc:
        print(f"LIVE LOAD FAILED: {exc}")
        print("Fix: allow nsearchives.nseindia.com / www.sec.gov through the proxy, or download the CSVs on another machine and put them in VM_UNIVERSE_DIR "
              "(ind_nifty500list.csv, EQUITY_L.csv, company_tickers.json).")
