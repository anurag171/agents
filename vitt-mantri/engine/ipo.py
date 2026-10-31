"""IPO insight engine: structured, transparent, rule-based (not a prediction model).

Two views because they depend on different things:
  listing_score   - near-term / listing-day: valuation vs peers, subscription demand, anchors, sector tailwind
  longterm_score  - fundamentals: profitability, growth, ROE, leverage, use of proceeds, promoter holding
Every factor is listed with points and a reason; missing data is *not* scored and lowers `coverage`.
Grey-market premium (GMP) is shown as an unofficial, manipulable indicator and never scored.
Educational output only, not advice.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

IPO_WORDS = re.compile(r"\b(ipo|price band|rhp|drhp|anchor investors?|subscription|allotment|listing gains?|grey market|gmp)\b", re.I)
_BAND = re.compile(r"(?:rs\.?|₹|inr)\s*(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(?:rs\.?|₹|inr)?\s*(\d+(?:\.\d+)?)", re.I)
_NAME = re.compile(r"^(?P<n>[A-Z][\w&.\- ]{2,60}?)\s+(?:ipo|limited ipo|ltd ipo)\b", re.I)


@dataclass
class Ipo:
    name: str
    status: str = "upcoming"                      # upcoming | open | closed | listed
    price_band: Optional[tuple] = None            # (low, high)
    lot_size: Optional[int] = None
    open_date: str = ""; close_date: str = ""; listing_date: str = ""
    issue_size_cr: Optional[float] = None; fresh_cr: Optional[float] = None; ofs_cr: Optional[float] = None
    sub_total: Optional[float] = None; sub_qib: Optional[float] = None; sub_nii: Optional[float] = None; sub_retail: Optional[float] = None
    anchor_cr: Optional[float] = None
    pe: Optional[float] = None; peer_pe: Optional[float] = None
    roe: Optional[float] = None; debt_equity: Optional[float] = None
    rev_growth_pct: Optional[float] = None; profitable: Optional[bool] = None
    promoter_post_pct: Optional[float] = None
    gmp_pct: Optional[float] = None               # unofficial; informational only
    sector: str = ""; sector_bias: float = 0.0    # -1..+1 from the theme engine (optional)
    issue_price: Optional[float] = None; listing_price: Optional[float] = None
    url: str = ""


def is_ipo_headline(title: str) -> bool:
    return bool(IPO_WORDS.search(title or ""))


def parse_ipo_headline(title: str) -> dict:
    m, b = _NAME.search(title or ""), _BAND.search(title or "")
    return {"name": m.group("n").strip() if m else None, "price_band": (float(b.group(1)), float(b.group(2))) if b else None}


def _f(factors, key, pts, why):
    factors.append({"factor": key, "points": pts, "why": why})
    return pts


def insight(i: Ipo) -> dict:
    listing, longt, flags = [], [], []
    possible = scored = 0

    def have(*v):
        nonlocal possible, scored
        possible += 1
        ok = all(x is not None for x in v)
        scored += ok
        return ok

    if have(i.pe, i.peer_pe) and i.peer_pe:
        r = i.pe / i.peer_pe
        p = 18 if r <= 0.8 else 8 if r <= 1.1 else 0 if r <= 1.4 else -15
        _f(listing, "valuation", p, f"P/E {i.pe:g} vs peers {i.peer_pe:g} ({r:.2f}x)"); _f(longt, "valuation", p, f"issue P/E is {r:.2f}x peers")
    if have(i.profitable):
        p = 10 if i.profitable else -15
        _f(longt, "profitability", p, "profitable" if i.profitable else "loss-making issuer")
        if not i.profitable: flags.append("loss-making issuer")
    if have(i.rev_growth_pct):
        _f(longt, "growth", 10 if i.rev_growth_pct >= 15 else 5 if i.rev_growth_pct >= 5 else -5, f"revenue growth {i.rev_growth_pct:g}%")
    if have(i.roe):
        _f(longt, "returns", 10 if i.roe >= 15 else 0 if i.roe >= 8 else -5, f"ROE {i.roe:g}%")
    if have(i.debt_equity):
        p = -10 if i.debt_equity > 2 else 0 if i.debt_equity > 1 else 4
        _f(longt, "leverage", p, f"debt/equity {i.debt_equity:g}")
        if i.debt_equity > 2: flags.append("high leverage")
    if have(i.ofs_cr, i.issue_size_cr) and i.issue_size_cr:
        share = i.ofs_cr / i.issue_size_cr
        p = -12 if share > 0.8 else -5 if share > 0.5 else 8 if share < 0.3 else 0
        _f(longt, "use of proceeds", p, f"{share:.0%} of the issue is offer-for-sale (existing holders selling)")
        if share > 0.8: flags.append("mostly OFS: little money reaches the company")
    if have(i.promoter_post_pct):
        _f(longt, "promoter holding", 5 if i.promoter_post_pct >= 60 else -5 if i.promoter_post_pct < 40 else 0, f"promoters hold {i.promoter_post_pct:g}% post-issue")
    if i.status in ("open", "closed", "listed"):
        if have(i.sub_total):
            p = 20 if i.sub_total >= 30 else 12 if i.sub_total >= 10 else 5 if i.sub_total >= 3 else -20 if i.sub_total < 1 else 0
            _f(listing, "demand", p, f"subscribed {i.sub_total:g}x overall")
            if i.sub_total < 1: flags.append("under-subscribed")
        if i.sub_qib is not None:
            _f(listing, "institutional demand", 8 if i.sub_qib >= 10 else 0 if i.sub_qib >= 2 else -6, f"QIB {i.sub_qib:g}x")
    if have(i.anchor_cr, i.issue_size_cr) and i.issue_size_cr:
        _f(listing, "anchors", 5 if i.anchor_cr / i.issue_size_cr >= 0.25 else 0, f"anchors took {i.anchor_cr / i.issue_size_cr:.0%} of the issue")
    if i.sector_bias:
        _f(listing, "sector tailwind", round(8 * max(-1, min(1, i.sector_bias))), f"theme engine bias {i.sector_bias:+.1f} for {i.sector or 'sector'}")
    if i.gmp_pct is not None:
        flags.append(f"GMP {i.gmp_pct:+g}% is unofficial and can be manipulated; not scored")

    ls = max(0, min(100, 50 + sum(f["points"] for f in listing)))
    ts = max(0, min(100, 50 + sum(f["points"] for f in longt)))
    coverage = scored / possible if possible else 0.0
    n_scored = len(listing) + len(longt)
    blended = (ls + ts) / 2
    verdict = "INSUFFICIENT DATA" if coverage < 0.4 or n_scored < 3 else "FAVOURABLE" if blended >= 62 else "CAUTION" if blended < 45 else "NEUTRAL"
    out = {"name": i.name, "status": i.status, "verdict": verdict, "listing_score": ls, "longterm_score": ts,
           "coverage": round(coverage, 2), "factors": {"listing": listing, "longterm": longt}, "flags": flags,
           "terms": {"price_band": i.price_band, "lot_size": i.lot_size,
                     "min_investment": round(i.lot_size * i.price_band[1]) if i.lot_size and i.price_band else None,
                     "open": i.open_date, "close": i.close_date, "listing": i.listing_date},
           "disclaimer": "Educational summary of public data, not advice. Check the RHP; IPOs can list below issue price."}
    if i.status == "listed" and i.issue_price and i.listing_price:
        out["listing_gain_pct"] = round((i.listing_price / i.issue_price - 1) * 100, 1)
    return out
