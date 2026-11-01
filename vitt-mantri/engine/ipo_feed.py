"""IPO data feed: NSE issue lists + analyst-supplied files (fundamentals) + headline-derived stubs -> insights.
Public APIs give dates/price band/subscription but not fundamentals; drop a JSON/CSV in $VM_IPO_DIR with the
fields of engine.ipo.Ipo (pe, peer_pe, roe, debt_equity, rev_growth_pct, profitable, ofs_cr, issue_size_cr, ...)
to unlock the full scoring. Without them the verdict honestly stays INSUFFICIENT DATA.
NSE endpoint formats could not be verified from the build sandbox; each source fails independently."""
from __future__ import annotations

import csv
import io
import json
import os
import re
from dataclasses import fields
from datetime import date, datetime

from .instruments import clean_name
from .ipo import Ipo, insight, is_ipo_headline, parse_ipo_headline
from .movers import NSE_HOME, _session

NSE_ENDPOINTS = ["/api/ipo-current-issue", "/api/all-upcoming-issues?category=ipo"]
RANK = {"upcoming": 0, "open": 1, "closed": 2, "listed": 3}
_NUM = re.compile(r"\d[\d,]*\.?\d*")
_FIELDS = {f.name: f for f in fields(Ipo)}
_FLOATS = {n for n, f in _FIELDS.items() if "Optional[float]" in str(f.type)}


def key(name: str) -> str:
    return " ".join(re.sub(r"\b(ipo|sme)\b", " ", clean_name(name)).split())


def _nums(s):
    return [float(x.replace(",", "")) for x in _NUM.findall(str(s or ""))]


def _band(s):
    n = _nums(s)
    return (n[0], n[-1]) if n else None


def _date(s):
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(str(s).strip(), fmt).date()
        except ValueError:
            pass
    return None


def _status(raw, o, c, today):
    if o and c:
        return "upcoming" if today < o else "open" if today <= c else "closed"
    s = str(raw or "").lower()
    return "open" if s in ("active", "open") else "closed" if "clos" in s else "listed" if "list" in s else "upcoming"


def parse_nse_ipo(text: str, today: date | None = None) -> list[Ipo]:
    today, data = today or date.today(), json.loads(text)
    rows = data.get("data", data) if isinstance(data, dict) else data
    out = []
    for r in rows if isinstance(rows, list) else []:
        name = r.get("companyName") or r.get("company") or r.get("symbol")
        if not name:
            continue
        o, c = _date(r.get("issueStartDate")), _date(r.get("issueEndDate"))
        sub = _nums(r.get("noOfTimeSubscribed") or r.get("subscriptionTimes"))
        out.append(Ipo(name=str(name).strip(), status=_status(r.get("status"), o, c, today), price_band=_band(r.get("issuePrice") or r.get("priceBand")),
                       lot_size=int(_nums(r.get("lotSize") or r.get("minOrderQuantity"))[0]) if _nums(r.get("lotSize") or r.get("minOrderQuantity")) else None,
                       open_date=str(r.get("issueStartDate") or ""), close_date=str(r.get("issueEndDate") or ""), sub_total=sub[0] if sub else None))
    return out


def parse_ipo_file(text: str, as_csv: bool = False) -> list[Ipo]:
    rows = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff")))) if as_csv else json.loads(text)
    out = []
    for r in rows:
        kw = {}
        for k, v in r.items():
            k = (k or "").strip()
            if k not in _FIELDS or v in (None, ""):
                continue
            if k == "price_band":
                v = _band(v)
            elif k in _FLOATS or k == "sector_bias":
                v = _nums(v)[0] if isinstance(v, str) and _nums(v) else (v if not isinstance(v, str) else None)
            elif k == "lot_size":
                v = int(_nums(v)[0]) if _nums(v) else None
            elif k == "profitable":
                v = str(v).strip().lower() in ("1", "true", "yes", "y")
            kw[k] = v
        if kw.get("name"):
            out.append(Ipo(**kw))
    return out


_LISTED = re.compile(r"\b(list(s|ed|ing)?\s+(at|on|with)|debut|listing gains?|lists? at a (premium|discount))\b", re.I)
_CLOSED = re.compile(r"\b(allotment|basis of allotment|subscription (status )?(final|closes?))\b", re.I)
_OPEN = re.compile(r"\b(day\s*[123]|opens?(?!\s+on)|subscription status|subscribed)\b", re.I)
_SUB = [re.compile(r"subscri\w*[^.]{0,40}?(\d+(?:\.\d+)?)\s*(?:x|times)", re.I), re.compile(r"(\d+(?:\.\d+)?)\s*(?:x|times)[^.]{0,30}subscri", re.I)]
_GMP = re.compile(r"gmp[^0-9\-]{0,12}(?:rs\.?|₹)?\s*(-?\d+(?:\.\d+)?)", re.I)


def ipo_from_news(items) -> list[Ipo]:
    out = []
    for it in items:
        title = it.get("title") or ""
        if not is_ipo_headline(title):
            continue
        info = parse_ipo_headline(title)
        if not info["name"]:
            continue
        text = f"{title} {it.get('summary') or ''}"
        status = "listed" if _LISTED.search(title) else "closed" if _CLOSED.search(title) else "open" if _OPEN.search(title) else "upcoming"
        sub = next((float(m.group(1)) for rx in _SUB if (m := rx.search(text))), None)
        gmp = _GMP.search(text)
        band = info["price_band"]
        out.append(Ipo(name=info["name"], status=status, price_band=band, sub_total=sub, url=it.get("url", ""),
                       gmp_pct=round(float(gmp.group(1)) / band[1] * 100, 1) if gmp and band else None))
    return out


def merge(*lists) -> dict:
    """lists in precedence order (files, NSE, news). Fills gaps from lower precedence; status = most advanced stage."""
    merged, srcs = {}, {}
    for tag, lst in lists:
        for i in lst:
            k = key(i.name)
            if not k:
                continue
            cur = merged.get(k)
            srcs.setdefault(k, []).append(tag)
            if cur is None:
                merged[k] = i
                continue
            for f in _FIELDS:
                if getattr(cur, f) in (None, "", 0.0) and getattr(i, f) not in (None, ""):
                    setattr(cur, f, getattr(i, f))
            if RANK[i.status] > RANK[cur.status]:
                cur.status = i.status
    return {k: (v, sorted(set(srcs[k]))) for k, v in merged.items()}


def build_ipos(items, session=None, ipo_dir=None):
    ipo_dir = ipo_dir or os.getenv("VM_IPO_DIR")
    cov, files, nse = [], [], []
    if ipo_dir and os.path.isdir(ipo_dir):
        try:
            for f in sorted(os.listdir(ipo_dir)):
                if f.endswith((".json", ".csv")):
                    files += parse_ipo_file(open(os.path.join(ipo_dir, f), encoding="utf-8-sig").read(), f.endswith(".csv"))
            cov.append({"source": "IPO: files", "raw": len(files)})
        except Exception as e:
            cov.append({"source": "IPO: files", "raw": 0, "error": type(e).__name__})
    if os.getenv("VM_IPO_NSE", "1") != "0":
        try:
            s = session or _session()
            s.get(NSE_HOME, timeout=(3.05, 10))
            for path in NSE_ENDPOINTS:
                try:
                    r = s.get(NSE_HOME + path, timeout=(3.05, 15), headers={"Referer": NSE_HOME}); r.raise_for_status()
                    nse += parse_nse_ipo(r.text)
                except Exception:
                    continue
            cov.append({"source": "IPO: NSE", "raw": len(nse)})
        except Exception as e:
            cov.append({"source": "IPO: NSE", "raw": 0, "error": type(e).__name__})
    news = ipo_from_news(items)
    cov.append({"source": "IPO: headlines", "raw": len(news)})
    out = []
    for k, (ipo, srcs) in merge(("file", files), ("nse", nse), ("news", news)).items():
        d = insight(ipo)
        d.update(sources=srcs, url=ipo.url, gmp_pct=ipo.gmp_pct, price_band=ipo.price_band,
                 subscription={"total": ipo.sub_total, "qib": ipo.sub_qib, "nii": ipo.sub_nii, "retail": ipo.sub_retail})
        out.append(d)
    out.sort(key=lambda d: ({"open": 0, "upcoming": 1, "closed": 2, "listed": 3}[d["status"]], d["name"]))
    return out[:40], cov
