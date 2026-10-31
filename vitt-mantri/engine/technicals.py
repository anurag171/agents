"""engine/technicals.py - chart confirmation layer for Vitta-Mantri.

Pure Python (no numpy/pandas) so it installs in locked-down environments.
Purpose: confirm or veto a news-derived BUY/SELL using
  1. trend / momentum / extension (is the chart on the same side as the news?)
  2. a price-range check (entry zone from support/resistance, stop, target, risk:reward)
  3. an analog history check (how did THIS stock behave after similar chart states?)

Design rules
  * No look-ahead: every indicator value at index i uses bars[0..i] only.
  * Parameters are fixed, documented constants. Do not tune them on the same data you
    evaluate on; validate with the walk-forward backtest (SKILL.md, phase P6A).
  * The layer can only DOWNGRADE (BUY/SELL -> WATCH) or annotate; it never creates signals.
  * Educational output, not advice.
"""
from __future__ import annotations

import csv
import math
import statistics
from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

# ---- constants (documented, not tuned) ------------------------------------
MIN_BARS = 60            # minimum history to analyse at all
SMA_FAST, SMA_MID, SMA_SLOW = 20, 50, 200
RSI_N, ATR_N = 14, 14
PIVOT_K = 5              # swing high/low = extreme of 2K+1 bars (confirmed K bars later)
LOOKBACK_LEVELS = 252
STOP_ATR = 0.5           # stop sits this many ATRs beyond the support/resistance level
RR_MIN = 1.5
CHASE_ATR = 2.5          # price this many ATRs beyond the 20d mean (in signal direction) = chasing
ANALOG_H = 10            # forward horizon (bars) for the analog study
ANALOG_MIN_N = 20        # minimum independent (non-overlapping) analogs to use the result
ENTRY_COMPONENT_CAP = 0.3


@dataclass(frozen=True)
class Bar:
    d: date
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0


def load_ohlcv_csv(path: str) -> list[Bar]:
    """CSV header: date,open,high,low,close,volume (volume optional). Sorted ascending, bad rows skipped."""
    out: list[Bar] = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                out.append(Bar(date.fromisoformat(r["date"]), float(r["open"]), float(r["high"]),
                               float(r["low"]), float(r["close"]), float(r.get("volume") or 0)))
            except (KeyError, ValueError):
                continue
    out.sort(key=lambda b: b.d)
    return out


# ---- indicator series (aligned to input; None until enough data) ----------
def sma(x: Sequence[float], n: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(x)
    s = 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n:
            s -= x[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(x: Sequence[Optional[float]], n: int) -> list[Optional[float]]:
    """EMA seeded with the SMA of the first n non-None values."""
    out: list[Optional[float]] = [None] * len(x)
    k = 2.0 / (n + 1)
    seed: list[float] = []
    e: Optional[float] = None
    for i, v in enumerate(x):
        if v is None:
            continue
        if e is None:
            seed.append(v)
            if len(seed) == n:
                e = sum(seed) / n
                out[i] = e
        else:
            e = v * k + e * (1 - k)
            out[i] = e
    return out


def rsi(closes: Sequence[float], n: int = RSI_N) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    ag, al = gains / n, losses / n
    out[n] = 100.0 if al == 0 else 100.0 - 100.0 / (1 + ag / al)
    for i in range(n + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(ch, 0.0)) / n
        al = (al * (n - 1) + max(-ch, 0.0)) / n
        out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1 + ag / al)
    return out


def atr(bars: Sequence[Bar], n: int = ATR_N) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(bars)
    if len(bars) <= n:
        return out
    tr = [0.0] * len(bars)
    for i in range(1, len(bars)):
        b, p = bars[i], bars[i - 1]
        tr[i] = max(b.h - b.l, abs(b.h - p.c), abs(b.l - p.c))
    a = sum(tr[1:n + 1]) / n
    out[n] = a
    for i in range(n + 1, len(bars)):
        a = (a * (n - 1) + tr[i]) / n
        out[i] = a
    return out


def macd_hist(closes: Sequence[float]) -> list[Optional[float]]:
    e12, e26 = ema(closes, 12), ema(closes, 26)
    line = [(a - b) if a is not None and b is not None else None for a, b in zip(e12, e26)]
    sig = ema(line, 9)
    return [(m - s) if m is not None and s is not None else None for m, s in zip(line, sig)]


# ---- support / resistance --------------------------------------------------
def swing_levels(bars: Sequence[Bar], k: int = PIVOT_K, lookback: int = LOOKBACK_LEVELS):
    """Confirmed swing lows/highs (needs k bars after the pivot, so no look-ahead)."""
    n = len(bars)
    lo_i = max(k, n - lookback)
    lows, highs = [], []
    for i in range(lo_i, n - k):
        win = bars[i - k:i + k + 1]
        if bars[i].l == min(b.l for b in win):
            lows.append(bars[i].l)
        if bars[i].h == max(b.h for b in win):
            highs.append(bars[i].h)
    return lows, highs


def _nearest_below(levels, price):
    c = [v for v in levels if v < price]
    return max(c) if c else None


def _nearest_above(levels, price):
    c = [v for v in levels if v > price]
    return min(c) if c else None


# ---- state used for analog history ----------------------------------------
def _trend_state(c, s50, s200):
    if s50 is None or s200 is None:
        return "na"
    if c > s50 > s200:
        return "up"
    if c < s50 < s200:
        return "down"
    return "mixed"


def _rsi_bucket(r):
    if r is None:
        return "na"
    return "lt35" if r < 35 else "35_50" if r < 50 else "50_65" if r < 65 else "ge65"


def analog_stats(bars: Sequence[Bar], direction: int, h: int = ANALOG_H) -> dict:
    """Among past bars with the same (trend, RSI bucket) as the latest bar, what happened over the
    next h bars in the signal direction? Matches are thinned to non-overlapping samples so n is honest.
    Only bars whose h-bar outcome is already known (i + h < last index) are used."""
    closes = [b.c for b in bars]
    s50, s200, r = sma(closes, SMA_MID), sma(closes, SMA_SLOW), rsi(closes)
    last = len(bars) - 1
    key = (_trend_state(closes[last], s50[last], s200[last]), _rsi_bucket(r[last]))
    if "na" in key:
        return {"n": 0, "state": key, "reason": "insufficient history"}
    rets, next_ok = [], 0
    for i in range(SMA_SLOW, last - h):
        if i < next_ok:
            continue
        if (_trend_state(closes[i], s50[i], s200[i]), _rsi_bucket(r[i])) == key:
            rets.append((closes[i + h] / closes[i] - 1.0) * direction)
            next_ok = i + h          # non-overlapping
    n = len(rets)
    if n == 0:
        return {"n": 0, "state": key, "reason": "no analogs"}
    hits = sum(1 for x in rets if x > 0)
    return {"n": n, "state": key, "horizon_bars": h, "hit_rate": hits / n,
            "median_ret": statistics.median(rets), "mean_ret": statistics.fmean(rets)}


# ---- main analysis ----------------------------------------------------------
def analyze(bars: Sequence[Bar], action: str, *, rr_min: float = RR_MIN) -> dict:
    """Compute chart state and the price-range plan for `action` (BUY|SELL)."""
    if action not in ("BUY", "SELL"):
        return {"available": False, "reason": "action not directional"}
    if len(bars) < MIN_BARS:
        return {"available": False, "reason": f"need >= {MIN_BARS} bars, have {len(bars)}"}
    d = 1 if action == "BUY" else -1
    closes = [b.c for b in bars]
    i = len(bars) - 1
    c = closes[i]
    s20, s50, s200 = sma(closes, SMA_FAST)[i], sma(closes, SMA_MID)[i], sma(closes, SMA_SLOW)[i]
    r, a, mh = rsi(closes)[i], atr(bars)[i], macd_hist(closes)
    if a is None or a <= 0 or r is None or s20 is None or s50 is None:
        return {"available": False, "reason": "indicators not warmed up"}
    mh_now = mh[i]
    mh_prev = mh[i - 1] if i > 0 else None
    win = bars[-LOOKBACK_LEVELS:]
    hi52, lo52 = max(b.h for b in win), min(b.l for b in win)
    pos52 = (c - lo52) / (hi52 - lo52) if hi52 > lo52 else 0.5
    vols = [b.v for b in bars[-21:-1] if b.v > 0]
    vol_ratio = (bars[i].v / statistics.fmean(vols)) if vols and bars[i].v > 0 else None
    day_ret = c / bars[i - 1].c - 1.0

    lows, highs = swing_levels(bars)
    lows += [min(b.l for b in bars[-20:])]            # fallbacks: 20d extremes always available
    highs += [max(b.h for b in bars[-20:])]
    support, resistance = _nearest_below(lows, c), _nearest_above(highs, c)
    open_sky = resistance is None
    open_floor = support is None
    ext_atr = (c - s20) / a                            # signed distance from 20d mean in ATRs

    # price-range plan
    if d == 1:
        base = support if support is not None else c - 2 * a
        stop = base - STOP_ATR * a
        target = resistance if resistance is not None else c + 2 * a
        risk, reward = c - stop, target - c
        p_max = (target + rr_min * stop) / (1 + rr_min)       # highest price still giving rr_min
        zone = (round(base, 2), round(p_max, 2)) if p_max > base else None
    else:
        base = resistance if resistance is not None else c + 2 * a
        stop = base + STOP_ATR * a
        target = support if support is not None else c - 2 * a
        risk, reward = stop - c, c - target
        p_min = (target + rr_min * stop) / (1 + rr_min)       # lowest price still giving rr_min
        zone = (round(p_min, 2), round(base, 2)) if p_min < base else None
    rr = (reward / risk) if risk > 0 and reward > 0 else 0.0
    in_zone = bool(zone and zone[0] <= c <= zone[1])

    return {
        "available": True, "action": action, "close": round(c, 2), "as_of": bars[i].d.isoformat(),
        "trend": _trend_state(c, s50, s200) if s200 is not None else ("up" if c > s50 else "down"),
        "has_sma200": s200 is not None,
        "sma20": round(s20, 2), "sma50": round(s50, 2), "sma200": round(s200, 2) if s200 else None,
        "rsi": round(r, 1), "atr": round(a, 2), "macd_hist": None if mh_now is None else round(mh_now, 3),
        "macd_improving": (mh_now is not None and mh_prev is not None and (mh_now - mh_prev) * d > 0),
        "ext_atr": round(ext_atr, 2), "pos52": round(pos52, 3), "vol_ratio": None if vol_ratio is None else round(vol_ratio, 2),
        "day_ret": round(day_ret, 4), "support": None if open_floor else round(support, 2),
        "resistance": None if open_sky else round(resistance, 2), "open_sky": open_sky,
        "stop": round(stop, 2), "target": round(target, 2), "rr": round(rr, 2),
        "entry_zone": zone, "in_zone": in_zone,
    }


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def confirm(tech: dict, analog: Optional[dict] = None, *, rr_min: float = RR_MIN,
            chase_atr: float = CHASE_ATR, analog_min_n: int = ANALOG_MIN_N) -> dict:
    """Return a verdict: CONFIRM | NEUTRAL | WAIT | CONTRADICT | UNAVAILABLE.

    WAIT = chart agrees in direction but price is outside the sensible range
           (extended, poor risk:reward, or beyond the entry zone) -> do not chase.
    """
    if not tech.get("available"):
        return {"verdict": "UNAVAILABLE", "score": 0.0, "reasons": [tech.get("reason", "no data")]}
    d = 1 if tech["action"] == "BUY" else -1
    reasons, score = [], 0.0

    t = {"up": 1, "down": -1}.get(tech["trend"], 0)
    comp = 0.35 * d * t
    score += comp
    reasons.append(f"trend {tech['trend']} ({comp:+.2f})")

    m = 0.0
    if tech["macd_hist"] is not None:
        m += 0.15 if tech["macd_hist"] * d > 0 else -0.15
    if tech["macd_improving"]:
        m += 0.05
    r = tech["rsi"]
    if d == 1:
        m += -0.2 if r >= 75 else 0.1 if 45 <= r <= 65 else 0.1 if (r <= 30 and tech["trend"] != "down") else 0.0
    else:
        m += -0.2 if r <= 25 else 0.1 if 35 <= r <= 55 else 0.1 if (r >= 70 and tech["trend"] != "up") else 0.0
    score += m
    reasons.append(f"momentum RSI {r} / MACD ({m:+.2f})")

    extended = tech["ext_atr"] * d > chase_atr
    if extended:
        score -= 0.3
        reasons.append(f"extended {tech['ext_atr']:+.1f} ATR from 20d mean (-0.30)")

    edge_pos = tech["pos52"] if d == 1 else 1 - tech["pos52"]
    if edge_pos > 0.95 and not (tech["vol_ratio"] and tech["vol_ratio"] >= 1.5):
        score -= 0.1
        reasons.append("at 52w extreme without volume expansion (-0.10)")

    if tech["vol_ratio"] and tech["vol_ratio"] >= 1.2 and tech["day_ret"] * d > 0:
        score += 0.1
        reasons.append(f"volume {tech['vol_ratio']}x with move in direction (+0.10)")

    analog_ok = bool(analog and analog.get("n", 0) >= analog_min_n)
    if analog_ok:
        edge = analog["hit_rate"] - 0.5
        a = _clamp(edge * 2, -ENTRY_COMPONENT_CAP, ENTRY_COMPONENT_CAP)
        if analog["median_ret"] < 0:
            a -= 0.1
        score += a
        reasons.append(f"history: {analog['hit_rate']:.0%} hit, median {analog['median_ret']:+.1%} "
                       f"over {analog['horizon_bars']}d in {analog['n']} analogs ({a:+.2f})")
    else:
        n = (analog or {}).get("n", 0)
        reasons.append(f"history: insufficient analogs (n={n}); not used")

    rr_ok = tech["rr"] >= rr_min
    if not rr_ok:
        reasons.append(f"risk:reward {tech['rr']} < {rr_min}")
    if not tech["in_zone"]:
        z = tech["entry_zone"]
        reasons.append("price outside entry zone" + (f" {z[0]}-{z[1]}" if z else " (no valid zone)"))

    # Counter-trend veto: trading against a confirmed trend needs positive history for this
    # exact chart state; small momentum offsets must not be enough to neutralise it.
    counter_trend = d * t == -1
    history_supports = analog_ok and analog["hit_rate"] >= 0.55 and analog["median_ret"] > 0
    if counter_trend and not history_supports:
        reasons.append("counter-trend without supportive history (veto)")

    score = round(_clamp(score, -1.0, 1.0), 2)
    if score <= -0.25 or (counter_trend and not history_supports):
        verdict = "CONTRADICT"
    elif extended or not rr_ok or not tech["in_zone"]:
        verdict = "WAIT"
    elif score >= 0.25:
        verdict = "CONFIRM"
    else:
        verdict = "NEUTRAL"
    return {"verdict": verdict, "score": score, "reasons": reasons, "analog_used": analog_ok,
            "plan": {"entry_zone": tech["entry_zone"], "stop": tech["stop"], "target": tech["target"],
                     "rr": tech["rr"], "open_sky": tech["open_sky"]}}


def apply_gate(action: str, verdict: dict, *, require_confirmation: bool = False) -> tuple[str, Optional[str], float]:
    """Combine the news-derived action with the chart verdict.
    Returns (action, reason, conviction_multiplier). Can only downgrade, never upgrade."""
    if action not in ("BUY", "SELL"):
        return action, None, 1.0
    v = verdict["verdict"]
    if v == "CONFIRM":
        return action, None, 1.0
    if v == "NEUTRAL":
        return action, "chart neutral", 0.7
    if v == "WAIT":
        z = verdict["plan"]["entry_zone"]
        return "WATCH", "wait: price outside range" + (f" (zone {z[0]}-{z[1]})" if z else ""), 0.5
    if v == "CONTRADICT":
        return "WATCH", "chart contradicts news signal", 0.4
    if require_confirmation:                      # UNAVAILABLE
        return "WATCH", "no chart data to confirm", 0.5
    return action, "chart unavailable", 0.8


def chart_payload(bars: Sequence[Bar], tech: dict, n: int = 120) -> dict:
    """Series + levels for the UI mini-chart (API: /api/chart/<ticker>)."""
    closes = [b.c for b in bars]
    s20, s50 = sma(closes, SMA_FAST), sma(closes, SMA_MID)
    tail = range(max(0, len(bars) - n), len(bars))
    rnd = lambda v: None if v is None else round(v, 2)
    return {
        "dates": [bars[i].d.isoformat() for i in tail], "close": [rnd(closes[i]) for i in tail],
        "sma20": [rnd(s20[i]) for i in tail], "sma50": [rnd(s50[i]) for i in tail],
        "levels": {k: tech.get(k) for k in ("support", "resistance", "stop", "target")},
        "entry_zone": tech.get("entry_zone"),
    }
